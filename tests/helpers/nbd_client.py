"""
NBD Test Client

This module provides a test client implementation for the Network Block Device (NBD)
protocol. It is used to test NBD server implementations by establishing connections,
performing handshakes, and executing block device operations (read, write, flush, disconnect).

The client implements the NBD fixed newstyle negotiation protocol.
"""

import asyncio
import struct

from nbd_server.constants import (
    IHAVEOPT,
    NBD_CMD_DISC,
    NBD_CMD_FLUSH,
    NBD_CMD_READ,
    NBD_CMD_WRITE,
    NBD_FLAG_FIXED_NEWSTYLE,
    NBD_OPT_GO,
    NBD_REP_ACK,
    NBD_REP_INFO,
    NBD_REP_MAGIC,
    NBD_REQUEST_MAGIC,
    NBD_SIMPLE_REPLY_MAGIC,
    NBDMAGIC,
)


class NBDTestClient:
    """
    A test client for Network Block Device (NBD) protocol communication.

    This client implements the NBD protocol handshake and basic operations
    (read, write, flush, disconnect) for testing NBD server implementations.
    It supports the fixed newstyle negotiation protocol.
    """

    def __init__(self, host: str = "localhost", port: int = 10809):
        """
        Initialize the NBD test client.

        Args:
            host: The hostname or IP address of the NBD server
            port: The port number the NBD server is listening on
        """
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.export_size = 0  # Size of the export in bytes
        self.transmission_flags = 0  # Flags received during handshake

    async def connect(self, export_name: str = "test-export") -> None:
        """
        Connect to the NBD server and perform the fixed newstyle handshake.

        This method implements the NBD protocol handshake sequence:
        1. Receive server's initial handshake (magic, IHAVEOPT, flags)
        2. Send client flags to confirm fixed newstyle support
        3. Send NBD_OPT_GO option to request the specified export
        4. Receive export info (size and transmission flags)
        5. Receive final acknowledgment

        Args:
            export_name: The name of the NBD export to connect to

        Raises:
            ValueError: If any part of the handshake fails or protocol violations occur
        """
        # Establish TCP connection to the NBD server
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

        # Read initial handshake: NBDMAGIC (8 bytes) + IHAVEOPT (8 bytes) + server_flags (2 bytes)
        handshake = await self.reader.readexactly(18)
        nbdmagic, ihaveopt, server_flags = struct.unpack(">QQH", handshake)

        # Verify the server sent correct magic values
        if nbdmagic != NBDMAGIC:
            raise ValueError(f"Invalid NBD magic: 0x{nbdmagic:016x}")
        if ihaveopt != IHAVEOPT:
            raise ValueError(f"Invalid IHAVEOPT: 0x{ihaveopt:016x}")
        if not (server_flags & NBD_FLAG_FIXED_NEWSTYLE):
            raise ValueError(f"Server doesn't support fixed newstyle: 0x{server_flags:04x}")

        # Send client flags to indicate we support fixed newstyle negotiation
        client_flags = struct.pack(">I", NBD_FLAG_FIXED_NEWSTYLE)
        self.writer.write(client_flags)
        await self.writer.drain()

        # Prepare the NBD_OPT_GO option data: export name length + export name + info request count
        export_name_bytes = export_name.encode("utf-8")
        export_name_length = len(export_name_bytes)

        option_data = struct.pack(">I", export_name_length) + export_name_bytes
        option_data += struct.pack(">H", 0)  # Number of information requests (0 = all)

        # Send NBD_OPT_GO: IHAVEOPT (8 bytes) + option (4 bytes) + data length (4 bytes) + data
        option_request = struct.pack(">QII", IHAVEOPT, NBD_OPT_GO, len(option_data))
        self.writer.write(option_request + option_data)
        await self.writer.drain()

        # Read the NBD_REP_INFO response header
        info_header = await self.reader.readexactly(20)
        rep_magic, _option, reply_type, reply_length = struct.unpack(">QIII", info_header)

        # Verify we received NBD_REP_INFO with correct magic
        if rep_magic != NBD_REP_MAGIC:
            raise ValueError(f"Invalid reply magic: 0x{rep_magic:016x}")
        if reply_type != NBD_REP_INFO:
            raise ValueError(f"Expected NBD_REP_INFO, got: 0x{reply_type:08x}")

        # Read export information: info_type (2 bytes) + export_size (8 bytes) + flags (2 bytes)
        info_data = await self.reader.readexactly(reply_length)
        _info_type, self.export_size, self.transmission_flags = struct.unpack(">HQH", info_data)

        # Read the final NBD_REP_ACK response to confirm connection
        ack_header = await self.reader.readexactly(20)
        rep_magic, _option, reply_type, reply_length = struct.unpack(">QIII", ack_header)

        # Verify the acknowledgment
        if rep_magic != NBD_REP_MAGIC:
            raise ValueError(f"Invalid ack reply magic: 0x{rep_magic:016x}")
        if reply_type != NBD_REP_ACK:
            raise ValueError(f"Expected NBD_REP_ACK, got: 0x{reply_type:08x}")

    async def pwrite(self, data: bytes, offset: int) -> None:
        """
        Write data to the NBD export at the specified offset.

        Args:
            data: The bytes to write to the export
            offset: The byte offset within the export to start writing

        Raises:
            ValueError: If the write operation fails or the server returns an error
        """
        # Generate a unique handle for this request to match with the reply
        handle = 0x123456789ABCDEF0

        # Build NBD write request: magic (4) + flags (2) + type (2) + handle (8) + offset (8) + length (4)
        cmd = struct.pack(
            ">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_WRITE, handle, offset, len(data)
        )
        # Send the command followed by the actual data payload
        self.writer.write(cmd + data)
        await self.writer.drain()

        # Read the simple reply: magic (4 bytes) + error (4 bytes) + handle (8 bytes)
        reply = await self.reader.readexactly(16)
        reply_magic, error, reply_handle = struct.unpack(">IIQ", reply)

        # Verify the reply is valid
        if reply_magic != NBD_SIMPLE_REPLY_MAGIC:
            raise ValueError(f"Invalid reply magic: 0x{reply_magic:08x}")
        if error != 0:
            raise ValueError(f"Write failed with error: {error}")
        if reply_handle != handle:
            raise ValueError(f"Handle mismatch: expected 0x{handle:016x}, got 0x{reply_handle:016x}")

    async def pread(self, length: int, offset: int) -> bytes:
        """
        Read data from the NBD export at the specified offset.

        Args:
            length: The number of bytes to read
            offset: The byte offset within the export to start reading from

        Returns:
            The bytes read from the export

        Raises:
            ValueError: If the read operation fails or the server returns an error
        """
        # Generate a unique handle for this request to match with the reply
        handle = 0xABCDEF0123456789

        # Build NBD read request: magic (4) + flags (2) + type (2) + handle (8) + offset (8) + length (4)
        cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_READ, handle, offset, length)
        self.writer.write(cmd)
        await self.writer.drain()

        # Read the simple reply header: magic (4 bytes) + error (4 bytes) + handle (8 bytes)
        reply = await self.reader.readexactly(16)
        reply_magic, error, reply_handle = struct.unpack(">IIQ", reply)

        # Verify the reply is valid
        if reply_magic != NBD_SIMPLE_REPLY_MAGIC:
            raise ValueError(f"Invalid reply magic: 0x{reply_magic:08x}")
        if error != 0:
            raise ValueError(f"Read failed with error: {error}")
        if reply_handle != handle:
            raise ValueError(f"Handle mismatch: expected 0x{handle:016x}, got 0x{reply_handle:016x}")

        # Read the actual data payload that follows the reply header
        data = await self.reader.readexactly(length)
        return data

    async def flush(self) -> None:
        """
        Flush any pending writes to the NBD export's backing storage.

        This ensures that all previously written data is persisted to disk
        before returning.

        Raises:
            ValueError: If the flush operation fails or the server returns an error
        """
        # Generate a unique handle for this request to match with the reply
        handle = 0x1122334455667788

        # Build NBD flush request: magic (4) + flags (2) + type (2) + handle (8) + offset (8) + length (4)
        # Offset and length are 0 for flush commands
        cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_FLUSH, handle, 0, 0)
        self.writer.write(cmd)
        await self.writer.drain()

        # Read the simple reply: magic (4 bytes) + error (4 bytes) + handle (8 bytes)
        reply = await self.reader.readexactly(16)
        reply_magic, error, reply_handle = struct.unpack(">IIQ", reply)

        # Verify the reply is valid
        if reply_magic != NBD_SIMPLE_REPLY_MAGIC:
            raise ValueError(f"Invalid reply magic: 0x{reply_magic:08x}")
        if error != 0:
            raise ValueError(f"Flush failed with error: {error}")
        if reply_handle != handle:
            raise ValueError(f"Handle mismatch: expected 0x{handle:016x}, got 0x{reply_handle:016x}")

    async def disconnect(self) -> None:
        """
        Send a disconnect command to the NBD server.

        This notifies the server that the client is closing the connection.
        Note that this command does not expect a reply from the server.
        """
        # Build NBD disconnect request: magic (4) + flags (2) + type (2) + handle (8) + offset (8) + length (4)
        # All fields except magic and type are 0 for disconnect
        cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_DISC, 0, 0, 0)
        self.writer.write(cmd)
        await self.writer.drain()

    async def close(self) -> None:
        """
        Close the underlying TCP connection to the NBD server.

        This should be called after disconnect() to cleanly shut down the connection.
        """
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def __aenter__(self) -> "NBDTestClient":
        """
        Async context manager entry point.

        Returns:
            The NBDTestClient instance for use in the context
        """
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        """
        Async context manager exit point.

        Ensures the connection is properly closed, sending a disconnect command
        and closing the TCP connection. Exceptions during disconnect are suppressed
        to allow the close operation to proceed.

        Args:
            _exc_type: Exception type if an exception occurred in the context
            _exc_val: Exception value if an exception occurred in the context
            _exc_tb: Exception traceback if an exception occurred in the context
        """
        try:
            # Attempt to send disconnect command, but don't fail if it errors
            await self.disconnect()
        except Exception:
            pass
        # Always close the connection
        await self.close()

    def get_size(self) -> int:
        """
        Get the size of the NBD export in bytes.

        Returns:
            The export size received during the handshake
        """
        return self.export_size
