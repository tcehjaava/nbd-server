import asyncio
import socket
import struct
import unittest

import pytest

from nbd_server.constants import (
    IHAVEOPT,
    NBD_FLAG_FIXED_NEWSTYLE,
    NBD_INFO_EXPORT,
    NBD_REP_ACK,
    NBD_REP_INFO,
    NBD_REP_MAGIC,
    NBD_REQUEST_MAGIC,
    NBD_SIMPLE_REPLY_MAGIC,
    NBDMAGIC,
)
from nbd_server.protocol import Requests, Responses, recv_exactly, receive_exactly


class TestRecvExactly(unittest.TestCase):

    def test_recv_exactly_single_chunk(self):
        sender, receiver = socket.socketpair()

        try:
            test_data = b"hello"
            sender.sendall(test_data)

            result = recv_exactly(receiver, 5)

            self.assertEqual(result, test_data)
        finally:
            sender.close()
            receiver.close()

    def test_recv_exactly_empty(self):
        sender, receiver = socket.socketpair()

        try:
            test_data = b""
            sender.sendall(test_data)

            result = recv_exactly(receiver, 0)

            self.assertEqual(result, test_data)
        finally:
            sender.close()
            receiver.close()

    def test_recv_exactly_large_data(self):
        sender, receiver = socket.socketpair()

        try:
            test_data = b"The quick brown fox jumps over the lazy dog! 1234567890 @#$%^&*()_+-=[]{}|;:,.<>?/~`"
            sender.sendall(test_data)

            result = recv_exactly(receiver, len(test_data))

            self.assertEqual(result, test_data)
        finally:
            sender.close()
            receiver.close()


class TestResponses(unittest.TestCase):

    def test_handshake_format(self):
        result = Responses.handshake()

        self.assertEqual(len(result), 18)

        magic, ihaveopt, flags = struct.unpack(">QQH", result)
        self.assertEqual(magic, NBDMAGIC)
        self.assertEqual(ihaveopt, IHAVEOPT)
        self.assertEqual(flags, NBD_FLAG_FIXED_NEWSTYLE)

    def test_simple_reply_format(self):
        error_code = 0
        handle = 12345

        result = Responses.simple_reply(error_code, handle)

        self.assertEqual(len(result), 16)

        magic, error, reply_handle = struct.unpack(">IIQ", result)
        self.assertEqual(magic, NBD_SIMPLE_REPLY_MAGIC)
        self.assertEqual(error, error_code)
        self.assertEqual(reply_handle, handle)

    def test_info_reply_format(self):
        option = 7
        export_size = 1073741824
        transmission_flags = 3

        result = Responses.info_reply(option, export_size, transmission_flags)

        header = result[:20]
        info_data = result[20:]

        magic, reply_option, reply_type, length = struct.unpack(">QIII", header)
        self.assertEqual(magic, NBD_REP_MAGIC)
        self.assertEqual(reply_option, option)
        self.assertEqual(reply_type, NBD_REP_INFO)
        self.assertEqual(length, len(info_data))

        info_type, size, flags = struct.unpack(">HQH", info_data)
        self.assertEqual(info_type, NBD_INFO_EXPORT)
        self.assertEqual(size, export_size)
        self.assertEqual(flags, transmission_flags)

    def test_ack_reply_format(self):
        option = 7

        result = Responses.ack_reply(option)

        self.assertEqual(len(result), 20)

        magic, reply_option, reply_type, length = struct.unpack(">QIII", result)
        self.assertEqual(magic, NBD_REP_MAGIC)
        self.assertEqual(reply_option, option)
        self.assertEqual(reply_type, NBD_REP_ACK)
        self.assertEqual(length, 0)


class TestRequests(unittest.TestCase):

    def test_client_flags(self):
        flags_value = 0x00000001
        data = struct.pack(">I", flags_value)

        result = Requests.client_flags(data)

        self.assertEqual(result, flags_value)

    def test_option_request_valid(self):
        option = 7
        length = 10
        option_data = b"testdata12"
        header = struct.pack(">QII", IHAVEOPT, option, length)

        result_option, result_data = Requests.option_request(header, option_data)

        self.assertEqual(result_option, option)
        self.assertEqual(result_data, option_data)

    def test_option_request_invalid_magic(self):
        invalid_magic = 0x1234567890ABCDEF
        option = 7
        length = 10
        option_data = b"testdata12"
        header = struct.pack(">QII", invalid_magic, option, length)

        with self.assertRaises(ValueError) as context:
            Requests.option_request(header, option_data)

        self.assertIn("Invalid option magic", str(context.exception))

    def test_command_valid(self):
        flags = 0
        cmd_type = 0
        handle = 12345
        offset = 1024
        length = 4096
        data = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, flags, cmd_type, handle, offset, length)

        result_cmd_type, result_flags, result_handle, result_offset, result_length = (
            Requests.command(data)
        )

        self.assertEqual(result_cmd_type, cmd_type)
        self.assertEqual(result_flags, flags)
        self.assertEqual(result_handle, handle)
        self.assertEqual(result_offset, offset)
        self.assertEqual(result_length, length)

    def test_command_invalid_magic(self):
        invalid_magic = 0x12345678
        flags = 0
        cmd_type = 0
        handle = 12345
        offset = 1024
        length = 4096
        data = struct.pack(">IHHQQL", invalid_magic, flags, cmd_type, handle, offset, length)

        with self.assertRaises(ValueError) as context:
            Requests.command(data)

        self.assertIn("Invalid request magic", str(context.exception))


@pytest.mark.asyncio
async def test_receive_exactly_single_chunk():
    reader = asyncio.StreamReader()
    test_data = b"hello"
    reader.feed_data(test_data)
    reader.feed_eof()

    result = await receive_exactly(reader, 5)

    assert result == test_data


@pytest.mark.asyncio
async def test_receive_exactly_multiple_chunks():
    reader = asyncio.StreamReader()
    test_data = b"The quick brown fox jumps over the lazy dog!"
    reader.feed_data(test_data)
    reader.feed_eof()

    result = await receive_exactly(reader, len(test_data))

    assert result == test_data


@pytest.mark.asyncio
async def test_receive_exactly_connection_closed():
    reader = asyncio.StreamReader()
    reader.feed_data(b"hel")
    reader.feed_eof()

    with pytest.raises(asyncio.IncompleteReadError):
        await receive_exactly(reader, 5)


if __name__ == "__main__":
    unittest.main()
