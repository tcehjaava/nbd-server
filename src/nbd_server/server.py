import logging
import socket
import struct

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
from .protocol import Requests, Responses, recv_exactly
from .storage import StorageBackend

logger = logging.getLogger(__name__)


class NBDServer:
    """NBD (Network Block Device) server."""

    def __init__(
        self,
        storage: StorageBackend,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        export_size: int = parse_size(DEFAULT_EXPORT_SIZE),
    ):
        """Initialize NBD server with storage backend and configuration."""
        self.storage = storage
        self.host = host
        self.port = port
        self.export_size = export_size

    def run(self) -> None:
        """Start the NBD server and listen for connections."""
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.host, self.port))
        server_socket.listen(1)

        logger.info(f"NBD Server listening on {self.host}:{self.port}")
        logger.info("Waiting for connections... (Press Ctrl+C to stop)")

        try:
            while True:
                client_socket, client_address = server_socket.accept()
                logger.info(f"Connection from {client_address}")

                try:
                    self._handle_connection(client_socket)
                except ConnectionError as e:
                    logger.error(f"Connection error: {e}")
                except ValueError as e:
                    logger.error(f"Protocol error: {e}")
                except Exception as e:
                    logger.exception(f"Unexpected error handling connection: {e}")
                finally:
                    client_socket.close()
                    logger.info("Connection closed")

        except KeyboardInterrupt:
            logger.info("Shutting down server...")
        finally:
            server_socket.close()
            logger.info("Server stopped")

    def _handle_connection(self, client_socket: socket.socket) -> None:
        """Handle a single client connection through negotiation and transmission phases."""
        # Handshake phase
        client_socket.sendall(Responses.handshake())
        logger.debug(f"Sent handshake: {len(Responses.handshake())} bytes")

        # Parse client flags
        flags_data = recv_exactly(client_socket, 4)
        flags = Requests.client_flags(flags_data)
        logger.debug(f"Received client flags: 0x{flags:08x}")

        # Negotiation phase
        export_name = self._handle_negotiation(client_socket)
        if export_name is None:
            return

        # Transmission phase
        logger.info("Negotiation complete, entering transmission phase")
        self._handle_transmission(client_socket)

    def _handle_negotiation(self, client_socket: socket.socket) -> str | None:
        """Handle option negotiation phase, returns export name or None if aborted."""
        header = recv_exactly(client_socket, 16)
        option_length = struct.unpack(">QII", header)[2]
        option_data = recv_exactly(client_socket, option_length) if option_length > 0 else b""

        option, data = Requests.option_request(header, option_data)

        if option == NBD_OPT_GO:
            export_name_length = struct.unpack(">I", data[:4])[0]
            export_name = data[4 : 4 + export_name_length].decode("utf-8")
            logger.info(f"Export name: '{export_name}'")

            # Send info reply
            client_socket.sendall(
                Responses.info_reply(option, self.export_size, TRANSMISSION_FLAGS)
            )
            size_mb = self.export_size / (1024 * 1024)
            logger.debug(
                f"Sent NBD_REP_INFO: size={size_mb:.0f}MB, flags=0x{TRANSMISSION_FLAGS:04x}"
            )

            # Send ack reply
            client_socket.sendall(Responses.ack_reply(option))
            logger.debug(f"Sent NBD_REP_ACK for option 0x{option:08x}")

            return export_name

        elif option == NBD_OPT_ABORT:
            logger.info("Client requested abort")
            return None

        else:
            logger.warning(f"Unsupported option: 0x{option:08x}")
            return None

    def _handle_transmission(self, client_socket: socket.socket) -> None:
        """Handle transmission phase command loop."""
        while True:
            cmd_data = recv_exactly(client_socket, 28)
            cmd_type, flags, handle, offset, length = Requests.command(cmd_data)
            logger.debug(f"Command: type={cmd_type}, offset={offset}, length={length}")

            if cmd_type == NBD_CMD_READ:
                self._handle_read(client_socket, handle, offset, length)
            elif cmd_type == NBD_CMD_WRITE:
                self._handle_write(client_socket, handle, offset, length)
            elif cmd_type == NBD_CMD_FLUSH:
                self._handle_flush(client_socket, handle)
            elif cmd_type == NBD_CMD_DISC:
                logger.info("Client requested disconnect")
                break
            else:
                logger.warning(f"Unsupported command type {cmd_type} received")
                client_socket.sendall(Responses.simple_reply(1, handle))

    def _handle_read(
        self, client_socket: socket.socket, handle: int, offset: int, length: int
    ) -> None:
        """Handle READ command: read from storage and send data to client."""
        data = self.storage.read(offset, length)
        client_socket.sendall(Responses.simple_reply(0, handle))
        client_socket.sendall(data)
        logger.debug(f"Sent READ reply: {length} bytes")

    def _handle_write(
        self, client_socket: socket.socket, handle: int, offset: int, length: int
    ) -> None:
        """Handle WRITE command: receive data from client and write to storage."""
        write_data = recv_exactly(client_socket, length)
        self.storage.write(offset, write_data)
        client_socket.sendall(Responses.simple_reply(0, handle))
        logger.debug(f"Processed WRITE: {length} bytes at offset {offset}")

    def _handle_flush(self, client_socket: socket.socket, handle: int) -> None:
        """Handle FLUSH command: flush storage and send acknowledgment."""
        self.storage.flush()
        client_socket.sendall(Responses.simple_reply(0, handle))
        logger.debug("Processed FLUSH")
