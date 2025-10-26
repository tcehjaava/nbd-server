import asyncio
import logging
import socket
import uuid

from .storage.s3 import S3Storage
from .protocol.commands import TransmissionHandler
from .models import S3Config
from .protocol.negotiation import NegotiationHandler
from .constants import (
    DEFAULT_EXPORT_SIZE,
    DEFAULT_HOST,
    DEFAULT_PORT,
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
        protocol_handler = NegotiationHandler(self.export_size)

        try:
            self._enable_tcp_keepalive(writer, connection_id)

            await protocol_handler.handshake(writer)
            await protocol_handler.receive_client_flags(reader)

            export_name = await protocol_handler.negotiate_export(reader, writer)
            if export_name is None:
                return

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

            logger.info("Negotiation complete, entering transmission phase")
            command_handler = TransmissionHandler(storage)
            await command_handler.process_commands(reader, writer)

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

