import asyncio
import logging
import struct
from typing import Optional

from .messages import Requests, Responses, receive_exactly
from ..constants import NBD_OPT_ABORT, NBD_OPT_GO, TRANSMISSION_FLAGS

logger = logging.getLogger(__name__)


class ProtocolHandler:
    """Handles NBD protocol negotiation phase.

    Separates protocol-specific logic from connection management,
    making the code more modular and easier to test.
    """

    def __init__(self, export_size: int):
        self.export_size = export_size

    async def handshake(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Send initial handshake to client.

        Args:
            reader: Stream reader for client connection
            writer: Stream writer for client connection
        """
        writer.write(Responses.handshake())
        await writer.drain()
        logger.debug(f"Sent handshake: {len(Responses.handshake())} bytes")

    async def receive_client_flags(self, reader: asyncio.StreamReader) -> int:
        """Receive and parse client flags.

        Args:
            reader: Stream reader for client connection

        Returns:
            Client flags as integer

        Raises:
            ConnectionError: If unable to read flags
        """
        flags_data = await receive_exactly(reader, 4)
        flags = Requests.client_flags(flags_data)
        logger.debug(f"Received client flags: 0x{flags:08x}")
        return flags

    async def negotiate_export(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> Optional[str]:
        """Handle option negotiation phase.

        Args:
            reader: Stream reader for client connection
            writer: Stream writer for client connection

        Returns:
            Export name if negotiation successful, None if aborted

        Raises:
            ValueError: If protocol error occurs
        """
        header = await receive_exactly(reader, 16)
        option_length = struct.unpack(">QII", header)[2]
        option_data = (
            await receive_exactly(reader, option_length) if option_length > 0 else b""
        )

        option, data = Requests.option_request(header, option_data)

        if option == NBD_OPT_GO:
            return await self._handle_go_option(writer, data)
        elif option == NBD_OPT_ABORT:
            logger.info("Client requested abort")
            return None
        else:
            logger.warning(f"Unsupported option: 0x{option:08x}")
            return None

    async def _handle_go_option(self, writer: asyncio.StreamWriter, data: bytes) -> str:
        """Handle NBD_OPT_GO option.

        Args:
            writer: Stream writer for client connection
            data: Option data containing export name

        Returns:
            Export name extracted from option data
        """
        export_name_length = struct.unpack(">I", data[:4])[0]
        export_name = data[4 : 4 + export_name_length].decode("utf-8")
        logger.info(f"Export name: '{export_name}'")

        writer.write(Responses.info_reply(NBD_OPT_GO, self.export_size, TRANSMISSION_FLAGS))
        await writer.drain()
        size_mb = self.export_size / (1024 * 1024)
        logger.debug(f"Sent NBD_REP_INFO: size={size_mb:.0f}MB, flags=0x{TRANSMISSION_FLAGS:04x}")

        writer.write(Responses.ack_reply(NBD_OPT_GO))
        await writer.drain()
        logger.debug(f"Sent NBD_REP_ACK for option 0x{NBD_OPT_GO:08x}")

        return export_name
