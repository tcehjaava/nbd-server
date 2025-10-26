import asyncio
import logging
import struct

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
    ):
        """Initialize async NBD server with S3 configuration."""
        self.s3_config = s3_config
        self.block_size = block_size
        self.host = host
        self.port = port
        self.export_size = export_size
        self.storage_pool: dict[str, S3Storage] = {}

    async def run(self) -> None:
        """Start the async NBD server and handle concurrent connections."""
        server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
        )

        addr = server.sockets[0].getsockname() if server.sockets else (self.host, self.port)
        logger.info(f"Async NBD Server listening on {addr[0]}:{addr[1]}")
        logger.info("Ready to accept concurrent connections... (Press Ctrl+C to stop)")

        async with server:
            await server.serve_forever()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single async client connection through negotiation and transmission phases."""
        addr = writer.get_extra_info("peername")
        logger.info(f"Connection from {addr}")

        try:
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

            # Get or create storage from pool
            if export_name not in self.storage_pool:
                logger.info(f"Creating new storage for export '{export_name}'")
                self.storage_pool[export_name] = await S3Storage.create(
                    export_name=export_name,
                    s3_config=self.s3_config,
                    block_size=self.block_size,
                )
            else:
                logger.info(f"Reusing existing storage for export '{export_name}'")

            storage = self.storage_pool[export_name]

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
            writer.close()
            await writer.wait_closed()
            logger.info(f"Connection from {addr} closed")

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
