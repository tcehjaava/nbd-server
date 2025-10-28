import asyncio
import logging
import socket
import uuid

from .storage.s3 import S3Storage
from .storage.client import ClientManager
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

        # Create shared S3 client manager for connection pool reuse across all connections
        self.shared_client_manager = ClientManager(s3_config)
        logger.debug("Shared ClientManager created for connection pool reuse")

    async def run(self) -> None:
        """Start the async NBD server and handle concurrent connections."""
        server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
        )

        addr = server.sockets[0].getsockname() if server.sockets else (self.host, self.port)
        logger.info(f"NBD Server listening on {addr[0]}:{addr[1]} (ID: {self.server_id})")
        logger.debug(
            f"TCP keepalive: idle={self.tcp_keepalive_idle}s, "
            f"interval={self.tcp_keepalive_interval}s, count={self.tcp_keepalive_count}"
        )

        try:
            async with server:
                await server.serve_forever()
        finally:
            # Cleanup shared S3 client manager on server shutdown
            logger.info("Shutting down server, cleaning up shared resources...")
            await self.shared_client_manager.close()
            logger.info("Server shutdown complete")

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single async client connection through negotiation and transmission phases."""
        connection_id = str(uuid.uuid4())
        addr = writer.get_extra_info("peername")
        logger.info(f"New connection from {addr}")

        storage = None
        protocol_handler = NegotiationHandler(self.export_size)

        try:
            self._enable_tcp_keepalive(writer)

            await protocol_handler.handshake(writer)
            await protocol_handler.receive_client_flags(reader)

            export_name = await protocol_handler.negotiate_export(reader, writer)
            if export_name is None:
                return

            logger.debug(f"Creating storage for export '{export_name}'")
            try:
                storage = await S3Storage.create(
                    export_name=export_name,
                    s3_config=self.s3_config,
                    block_size=self.block_size,
                    connection_id=connection_id,
                    server_id=self.server_id,
                    s3_client_manager=self.shared_client_manager,
                )
            except RuntimeError as e:
                logger.error(f"Storage creation failed: {e}")
                return

            logger.info(f"Export '{export_name}' ready")
            command_handler = TransmissionHandler(storage)
            await command_handler.process_commands(reader, writer)

        except ConnectionError as e:
            logger.debug(f"Connection error: {e}")
        except ValueError as e:
            logger.error(f"Protocol error: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
        finally:
            if storage:
                await storage.release()
            writer.close()
            await writer.wait_closed()
            logger.debug(f"Connection closed: {addr}")

    def _enable_tcp_keepalive(self, writer: asyncio.StreamWriter) -> None:
        """Enable TCP keepalive on the socket to detect dead connections."""
        sock = writer.get_extra_info("socket")
        if sock is None:
            logger.debug("Cannot enable TCP keepalive (no socket)")
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
            elif hasattr(socket, "TCP_KEEPALIVE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, self.tcp_keepalive_idle)

        except OSError as e:
            logger.debug(f"Failed to enable TCP keepalive: {e}")

