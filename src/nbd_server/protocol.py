import socket
import struct
from .constants import (
    NBDMAGIC,
    IHAVEOPT,
    NBD_FLAG_FIXED_NEWSTYLE,
    NBD_REP_MAGIC,
    NBD_REP_ACK,
    NBD_REP_INFO,
    NBD_INFO_EXPORT,
    NBD_SIMPLE_REPLY_MAGIC,
    NBD_REQUEST_MAGIC,
)


def recv_exactly(sock: socket.socket, num_bytes: int) -> bytes:
    """Receive exactly the specified number of bytes from socket, blocking until complete."""
    data = b''
    while len(data) < num_bytes:
        chunk = sock.recv(num_bytes - len(data))
        if not chunk:
            raise ConnectionError("Connection closed while receiving data")
        data += chunk
    return data


class Responses:
    """Server → Client message serialization."""

    @staticmethod
    def handshake() -> bytes:
        """Pack initial handshake with NBD magic, IHAVEOPT, and FIXED_NEWSTYLE flag."""
        return struct.pack('>QQH', NBDMAGIC, IHAVEOPT, NBD_FLAG_FIXED_NEWSTYLE)

    @staticmethod
    def info_reply(option: int, export_size: int, transmission_flags: int) -> bytes:
        """Pack NBD_REP_INFO reply containing export size and transmission flags."""
        info_data = struct.pack('>HQH', NBD_INFO_EXPORT, export_size, transmission_flags)
        info_length = len(info_data)
        reply_header = struct.pack('>QIII', NBD_REP_MAGIC, option, NBD_REP_INFO, info_length)
        return reply_header + info_data

    @staticmethod
    def ack_reply(option: int) -> bytes:
        """Pack NBD_REP_ACK reply to acknowledge successful option processing."""
        return struct.pack('>QIII', NBD_REP_MAGIC, option, NBD_REP_ACK, 0)

    @staticmethod
    def simple_reply(error: int, handle: int) -> bytes:
        """Pack simple reply for transmission phase commands with error code and handle."""
        return struct.pack('>IIQ', NBD_SIMPLE_REPLY_MAGIC, error, handle)


class Requests:
    """Client → Server message deserialization."""

    @staticmethod
    def client_flags(data: bytes) -> int:
        """Unpack client flags from 4 bytes indicating client capabilities."""
        return struct.unpack('>I', data)[0]

    @staticmethod
    def option_request(header: bytes, option_data: bytes) -> tuple[int, bytes]:
        """Unpack option request from 16-byte header, validates magic and returns (option_code, option_data)."""
        magic, option, _ = struct.unpack('>QII', header)
        if magic != IHAVEOPT:
            raise ValueError(f"Invalid option magic: 0x{magic:016x}")
        return option, option_data

    @staticmethod
    def command(data: bytes) -> tuple[int, int, int, int, int]:
        """Unpack transmission phase command from 28 bytes, returns (cmd_type, flags, handle, offset, length)."""
        magic, flags, cmd_type, handle, offset, length = struct.unpack('>IHHQQL', data)
        if magic != NBD_REQUEST_MAGIC:
            raise ValueError(f"Invalid request magic: 0x{magic:08x}")
        return cmd_type, flags, handle, offset, length
