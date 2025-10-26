import asyncio
import logging

from .messages import Requests, Responses, receive_exactly
from ..storage.base import StorageBackend
from ..constants import NBD_CMD_DISC, NBD_CMD_FLUSH, NBD_CMD_READ, NBD_CMD_WRITE

logger = logging.getLogger(__name__)


class TransmissionHandler:
    """Handles NBD transmission phase commands.

    Separates command processing logic from connection management,
    making the code more modular and testable.
    """

    def __init__(self, storage: StorageBackend):
        self.storage = storage

    async def process_commands(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Process NBD commands in transmission phase.

        Args:
            reader: Stream reader for client connection
            writer: Stream writer for client connection

        Raises:
            ConnectionError: If connection is lost
            ValueError: If protocol error occurs
        """
        while True:
            cmd_data = await receive_exactly(reader, 28)
            cmd_type, flags, handle, offset, length = Requests.command(cmd_data)
            logger.debug(f"Command: type={cmd_type}, offset={offset}, length={length}")

            if cmd_type == NBD_CMD_READ:
                await self._handle_read(writer, handle, offset, length)
            elif cmd_type == NBD_CMD_WRITE:
                await self._handle_write(reader, writer, handle, offset, length)
            elif cmd_type == NBD_CMD_FLUSH:
                await self._handle_flush(writer, handle)
            elif cmd_type == NBD_CMD_DISC:
                logger.info("Client requested disconnect")
                break
            else:
                logger.warning(f"Unsupported command type {cmd_type} received")
                await self._send_error(writer, handle, error_code=1)

    async def _handle_read(
        self, writer: asyncio.StreamWriter, handle: int, offset: int, length: int
    ) -> None:
        """Handle READ command.

        Args:
            writer: Stream writer for client connection
            handle: Command handle for response matching
            offset: Read offset in bytes
            length: Number of bytes to read
        """
        try:
            data = await self.storage.read(offset, length)
            writer.write(Responses.simple_reply(0, handle))
            await writer.drain()
            writer.write(data)
            await writer.drain()
            logger.debug(f"Sent READ reply: {length} bytes")
        except Exception as e:
            logger.error(f"Read error at offset {offset}, length {length}: {e}")
            await self._send_error(writer, handle, error_code=5)  # EIO

    async def _handle_write(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        handle: int,
        offset: int,
        length: int,
    ) -> None:
        """Handle WRITE command.

        Args:
            reader: Stream reader for client connection
            writer: Stream writer for client connection
            handle: Command handle for response matching
            offset: Write offset in bytes
            length: Number of bytes to write
        """
        try:
            write_data = await receive_exactly(reader, length)
            await self.storage.write(offset, write_data)
            writer.write(Responses.simple_reply(0, handle))
            await writer.drain()
            logger.debug(f"Processed WRITE: {length} bytes at offset {offset}")
        except Exception as e:
            logger.error(f"Write error at offset {offset}, length {length}: {e}")
            await self._send_error(writer, handle, error_code=5)  # EIO

    async def _handle_flush(self, writer: asyncio.StreamWriter, handle: int) -> None:
        """Handle FLUSH command.

        Args:
            writer: Stream writer for client connection
            handle: Command handle for response matching
        """
        try:
            await self.storage.flush()
            writer.write(Responses.simple_reply(0, handle))
            await writer.drain()
            logger.debug("Processed FLUSH")
        except Exception as e:
            logger.error(f"Flush error: {e}")
            await self._send_error(writer, handle, error_code=5)  # EIO

    async def _send_error(
        self, writer: asyncio.StreamWriter, handle: int, error_code: int
    ) -> None:
        """Send error response to client.

        Args:
            writer: Stream writer for client connection
            handle: Command handle for response matching
            error_code: NBD error code
        """
        writer.write(Responses.simple_reply(error_code, handle))
        await writer.drain()
        logger.debug(f"Sent error response: code={error_code}, handle={handle}")
