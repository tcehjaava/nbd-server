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
    def __init__(self, host: str = "localhost", port: int = 10809):
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.export_size = 0
        self.transmission_flags = 0

    async def connect(self, export_name: str = "test-export") -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

        handshake = await self.reader.readexactly(18)
        nbdmagic, ihaveopt, server_flags = struct.unpack(">QQH", handshake)

        if nbdmagic != NBDMAGIC:
            raise ValueError(f"Invalid NBD magic: 0x{nbdmagic:016x}")
        if ihaveopt != IHAVEOPT:
            raise ValueError(f"Invalid IHAVEOPT: 0x{ihaveopt:016x}")
        if not (server_flags & NBD_FLAG_FIXED_NEWSTYLE):
            raise ValueError(f"Server doesn't support fixed newstyle: 0x{server_flags:04x}")

        client_flags = struct.pack(">I", NBD_FLAG_FIXED_NEWSTYLE)
        self.writer.write(client_flags)
        await self.writer.drain()

        export_name_bytes = export_name.encode("utf-8")
        export_name_length = len(export_name_bytes)

        option_data = struct.pack(">I", export_name_length) + export_name_bytes
        option_data += struct.pack(">H", 0)

        option_request = struct.pack(">QII", IHAVEOPT, NBD_OPT_GO, len(option_data))
        self.writer.write(option_request + option_data)
        await self.writer.drain()

        info_header = await self.reader.readexactly(20)
        rep_magic, option, reply_type, reply_length = struct.unpack(">QIII", info_header)

        if rep_magic != NBD_REP_MAGIC:
            raise ValueError(f"Invalid reply magic: 0x{rep_magic:016x}")
        if reply_type != NBD_REP_INFO:
            raise ValueError(f"Expected NBD_REP_INFO, got: 0x{reply_type:08x}")

        info_data = await self.reader.readexactly(reply_length)
        info_type, self.export_size, self.transmission_flags = struct.unpack(">HQH", info_data)

        ack_header = await self.reader.readexactly(20)
        rep_magic, option, reply_type, reply_length = struct.unpack(">QIII", ack_header)

        if rep_magic != NBD_REP_MAGIC:
            raise ValueError(f"Invalid ack reply magic: 0x{rep_magic:016x}")
        if reply_type != NBD_REP_ACK:
            raise ValueError(f"Expected NBD_REP_ACK, got: 0x{reply_type:08x}")

    async def pwrite(self, data: bytes, offset: int) -> None:
        handle = 0x123456789ABCDEF0

        cmd = struct.pack(
            ">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_WRITE, handle, offset, len(data)
        )
        self.writer.write(cmd + data)
        await self.writer.drain()

        reply = await self.reader.readexactly(16)
        reply_magic, error, reply_handle = struct.unpack(">IIQ", reply)

        if reply_magic != NBD_SIMPLE_REPLY_MAGIC:
            raise ValueError(f"Invalid reply magic: 0x{reply_magic:08x}")
        if error != 0:
            raise ValueError(f"Write failed with error: {error}")
        if reply_handle != handle:
            raise ValueError(f"Handle mismatch: expected 0x{handle:016x}, got 0x{reply_handle:016x}")

    async def pread(self, length: int, offset: int) -> bytes:
        handle = 0xABCDEF0123456789

        cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_READ, handle, offset, length)
        self.writer.write(cmd)
        await self.writer.drain()

        reply = await self.reader.readexactly(16)
        reply_magic, error, reply_handle = struct.unpack(">IIQ", reply)

        if reply_magic != NBD_SIMPLE_REPLY_MAGIC:
            raise ValueError(f"Invalid reply magic: 0x{reply_magic:08x}")
        if error != 0:
            raise ValueError(f"Read failed with error: {error}")
        if reply_handle != handle:
            raise ValueError(f"Handle mismatch: expected 0x{handle:016x}, got 0x{reply_handle:016x}")

        data = await self.reader.readexactly(length)
        return data

    async def flush(self) -> None:
        handle = 0x1122334455667788

        cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_FLUSH, handle, 0, 0)
        self.writer.write(cmd)
        await self.writer.drain()

        reply = await self.reader.readexactly(16)
        reply_magic, error, reply_handle = struct.unpack(">IIQ", reply)

        if reply_magic != NBD_SIMPLE_REPLY_MAGIC:
            raise ValueError(f"Invalid reply magic: 0x{reply_magic:08x}")
        if error != 0:
            raise ValueError(f"Flush failed with error: {error}")
        if reply_handle != handle:
            raise ValueError(f"Handle mismatch: expected 0x{handle:016x}, got 0x{reply_handle:016x}")

    async def disconnect(self) -> None:
        cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_DISC, 0, 0, 0)
        self.writer.write(cmd)
        await self.writer.drain()

    async def close(self) -> None:
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def __aenter__(self) -> "NBDTestClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            await self.disconnect()
        except Exception:
            pass
        await self.close()

    def get_size(self) -> int:
        return self.export_size
