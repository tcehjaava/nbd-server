import asyncio
import logging
import socket
import struct
import uuid

from .async_protocol import Requests, Responses, receive_exactly
from .async_storage import S3Storage
from .models import S3Config
from .constants import (
    DEFAULT_EXPORT_SIZE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    NBD_CMD_DISC,
    NBD_CMD_FLUSH,
    NBD_CMD_READ,
    NBD_CMD_WRITE,
    NBD_OPT_ABORT,
    NBD_OPT_GO,
    TRANSMISSION_FLAGS,
    parse_size,
)

logger = logging.getLogger(__name__)


class NBDServer:
    """Async NBD (Network Block Device) server with concurrent connection support."""

    def __init__(
        self,
        s3_config: S3Config,
        block_size: int,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        export_size: int = parse_size(DEFAULT_EXPORT_SIZE),
        tcp_keepalive_idle: int = 60,
        tcp_keepalive_interval: int = 10,
        tcp_keepalive_count: int = 6,
    ):
        """Initialize async NBD server with S3 configuration and TCP keepalive settings.

        Args:
            s3_config: S3 configuration for storage backend
            block_size: Block size for storage operations
            host: Server host address
            port: Server port number
            export_size: Size of exports in bytes
            tcp_keepalive_idle: Seconds before sending first keepalive probe (default: 60)
            tcp_keepalive_interval: Seconds between keepalive probes (default: 10)
            tcp_keepalive_count: Number of failed probes before closing connection (default: 6)
        """
        self.s3_config = s3_config
        self.block_size = block_size
        self.host = host
        self.port = port
        self.export_size = export_size
        self.server_id = str(uuid.uuid4())
        self.tcp_keepalive_idle = tcp_keepalive_idle
        self.tcp_keepalive_interval = tcp_keepalive_interval
        self.tcp_keepalive_count = tcp_keepalive_count

    async def run(self) -> None:
        """Start the async NBD server and handle concurrent connections."""
        server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
        )

        addr = server.sockets[0].getsockname() if server.sockets else (self.host, self.port)
        logger.info(f"Async NBD Server listening on {addr[0]}:{addr[1]}")
        logger.info(f"Server ID: {self.server_id}")
        max_detection_time = self.tcp_keepalive_idle + (self.tcp_keepalive_interval * self.tcp_keepalive_count)
        logger.info(
            f"TCP keepalive enabled: idle={self.tcp_keepalive_idle}s, "
            f"interval={self.tcp_keepalive_interval}s, count={self.tcp_keepalive_count} "
            f"(max dead connection detection time: ~{max_detection_time}s)"
        )
        logger.info("Ready to accept concurrent connections... (Press Ctrl+C to stop)")

        async with server:
            await server.serve_forever()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single async client connection through negotiation and transmission phases."""
        connection_id = str(uuid.uuid4())
        addr = writer.get_extra_info("peername")
        logger.info(f"Connection {connection_id} from {addr}")

        storage = None

        try:
            self._enable_tcp_keepalive(writer, connection_id)

            # Handshake phase
            writer.write(Responses.handshake())
            await writer.drain()
            logger.debug(f"Sent handshake: {len(Responses.handshake())} bytes")

            # Parse client flags
            flags_data = await receive_exactly(reader, 4)
            flags = Requests.client_flags(flags_data)
            logger.debug(f"Received client flags: 0x{flags:08x}")

            # Negotiation phase
            export_name = await self._handle_negotiation(reader, writer)
            if export_name is None:
                return

            # Create storage with connection_id and server_id
            logger.info(f"Creating storage for export '{export_name}' (connection={connection_id})")
            try:
                storage = await S3Storage.create(
                    export_name=export_name,
                    s3_config=self.s3_config,
                    block_size=self.block_size,
                    connection_id=connection_id,
                    server_id=self.server_id,
                )
            except RuntimeError as e:
                logger.error(f"Failed to create storage: {e}")
                return

            # Transmission phase
            logger.info("Negotiation complete, entering transmission phase")
            await self._handle_transmission(reader, writer, storage)

        except ConnectionError as e:
            logger.error(f"Connection error: {e}")
        except ValueError as e:
            logger.error(f"Protocol error: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error handling connection: {e}")
        finally:
            if storage:
                await storage.release()
            writer.close()
            await writer.wait_closed()
            logger.info(f"Connection {connection_id} from {addr} closed")

    def _enable_tcp_keepalive(self, writer: asyncio.StreamWriter, connection_id: str) -> None:
        """Enable TCP keepalive on the socket to detect dead connections."""
        sock = writer.get_extra_info("socket")
        if sock is None:
            logger.warning(f"Connection {connection_id}: Cannot enable TCP keepalive (no socket)")
            return

        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            if hasattr(socket, "TCP_KEEPIDLE"):
                sock.setsockopt(
                    socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, self.tcp_keepalive_idle
                )
                sock.setsockopt(
                    socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, self.tcp_keepalive_interval
                )
                sock.setsockopt(
                    socket.IPPROTO_TCP, socket.TCP_KEEPCNT, self.tcp_keepalive_count
                )
                logger.debug(
                    f"Connection {connection_id}: TCP keepalive enabled "
                    f"(idle={self.tcp_keepalive_idle}s, interval={self.tcp_keepalive_interval}s, "
                    f"count={self.tcp_keepalive_count})"
                )
            elif hasattr(socket, "TCP_KEEPALIVE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, self.tcp_keepalive_idle)
                logger.debug(
                    f"Connection {connection_id}: TCP keepalive enabled "
                    f"(macOS style, idle={self.tcp_keepalive_idle}s)"
                )
            else:
                logger.warning(
                    f"Connection {connection_id}: TCP keepalive enabled but platform-specific "
                    f"settings not available (using OS defaults)"
                )

        except OSError as e:
            logger.warning(
                f"Connection {connection_id}: Failed to enable TCP keepalive: {e}"
            )

    async def _handle_negotiation(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> str | None:
        """Handle option negotiation phase, returns export name or None if aborted."""
        header = await receive_exactly(reader, 16)
        option_length = struct.unpack(">QII", header)[2]
        option_data = (
            await receive_exactly(reader, option_length) if option_length > 0 else b""
        )

        option, data = Requests.option_request(header, option_data)

        if option == NBD_OPT_GO:
            export_name_length = struct.unpack(">I", data[:4])[0]
            export_name = data[4 : 4 + export_name_length].decode("utf-8")
            logger.info(f"Export name: '{export_name}'")

            # Send info reply
            writer.write(Responses.info_reply(option, self.export_size, TRANSMISSION_FLAGS))
            size_mb = self.export_size / (1024 * 1024)
            logger.debug(
                f"Sent NBD_REP_INFO: size={size_mb:.0f}MB, flags=0x{TRANSMISSION_FLAGS:04x}"
            )

            # Send ack reply
            writer.write(Responses.ack_reply(option))
            await writer.drain()
            logger.debug(f"Sent NBD_REP_ACK for option 0x{option:08x}")

            return export_name

        elif option == NBD_OPT_ABORT:
            logger.info("Client requested abort")
            return None

        else:
            logger.warning(f"Unsupported option: 0x{option:08x}")
            return None

    async def _handle_transmission(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, storage: S3Storage
    ) -> None:
        """Handle transmission phase command loop."""
        while True:
            cmd_data = await receive_exactly(reader, 28)
            cmd_type, flags, handle, offset, length = Requests.command(cmd_data)
            logger.debug(f"Command: type={cmd_type}, offset={offset}, length={length}")

            if cmd_type == NBD_CMD_READ:
                await self._handle_read(writer, storage, handle, offset, length)
            elif cmd_type == NBD_CMD_WRITE:
                await self._handle_write(reader, writer, storage, handle, offset, length)
            elif cmd_type == NBD_CMD_FLUSH:
                await self._handle_flush(writer, storage, handle)
            elif cmd_type == NBD_CMD_DISC:
                logger.info("Client requested disconnect")
                break
            else:
                logger.warning(f"Unsupported command type {cmd_type} received")
                writer.write(Responses.simple_reply(1, handle))
                await writer.drain()

    async def _handle_read(
        self, writer: asyncio.StreamWriter, storage: S3Storage, handle: int, offset: int, length: int
    ) -> None:
        """Handle READ command: read from storage and send data to client."""
        data = await storage.read(offset, length)
        writer.write(Responses.simple_reply(0, handle))
        writer.write(data)
        await writer.drain()
        logger.debug(f"Sent READ reply: {length} bytes")

    async def _handle_write(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        storage: S3Storage,
        handle: int,
        offset: int,
        length: int,
    ) -> None:
        """Handle WRITE command: receive data from client and write to storage."""
        write_data = await receive_exactly(reader, length)
        await storage.write(offset, write_data)
        writer.write(Responses.simple_reply(0, handle))
        await writer.drain()
        logger.debug(f"Processed WRITE: {length} bytes at offset {offset}")

    async def _handle_flush(
        self, writer: asyncio.StreamWriter, storage: S3Storage, handle: int
    ) -> None:
        """Handle FLUSH command: flush storage and send acknowledgment."""
        await storage.flush()
        writer.write(Responses.simple_reply(0, handle))
        await writer.drain()
        logger.debug("Processed FLUSH")
