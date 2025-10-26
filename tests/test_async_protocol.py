import asyncio
import struct

import pytest

from nbd_server.async_protocol import Requests, Responses, receive_exactly
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
async def test_receive_exactly_empty():
    reader = asyncio.StreamReader()
    test_data = b""
    reader.feed_data(test_data)
    reader.feed_eof()

    result = await receive_exactly(reader, 0)

    assert result == test_data


@pytest.mark.asyncio
async def test_receive_exactly_connection_closed():
    reader = asyncio.StreamReader()
    reader.feed_data(b"hel")
    reader.feed_eof()

    with pytest.raises(asyncio.IncompleteReadError):
        await receive_exactly(reader, 5)


def test_handshake_format():
    result = Responses.handshake()

    assert len(result) == 18

    magic, ihaveopt, flags = struct.unpack(">QQH", result)
    assert magic == NBDMAGIC
    assert ihaveopt == IHAVEOPT
    assert flags == NBD_FLAG_FIXED_NEWSTYLE


def test_simple_reply_format():
    error_code = 0
    handle = 12345

    result = Responses.simple_reply(error_code, handle)

    assert len(result) == 16

    magic, error, reply_handle = struct.unpack(">IIQ", result)
    assert magic == NBD_SIMPLE_REPLY_MAGIC
    assert error == error_code
    assert reply_handle == handle


def test_info_reply_format():
    option = 7
    export_size = 1073741824
    transmission_flags = 3

    result = Responses.info_reply(option, export_size, transmission_flags)

    header = result[:20]
    info_data = result[20:]

    magic, reply_option, reply_type, length = struct.unpack(">QIII", header)
    assert magic == NBD_REP_MAGIC
    assert reply_option == option
    assert reply_type == NBD_REP_INFO
    assert length == len(info_data)

    info_type, size, flags = struct.unpack(">HQH", info_data)
    assert info_type == NBD_INFO_EXPORT
    assert size == export_size
    assert flags == transmission_flags


def test_ack_reply_format():
    option = 7

    result = Responses.ack_reply(option)

    assert len(result) == 20

    magic, reply_option, reply_type, length = struct.unpack(">QIII", result)
    assert magic == NBD_REP_MAGIC
    assert reply_option == option
    assert reply_type == NBD_REP_ACK
    assert length == 0


def test_client_flags():
    flags_value = 0x00000001
    data = struct.pack(">I", flags_value)

    result = Requests.client_flags(data)

    assert result == flags_value


def test_option_request_valid():
    option = 7
    length = 10
    option_data = b"testdata12"
    header = struct.pack(">QII", IHAVEOPT, option, length)

    result_option, result_data = Requests.option_request(header, option_data)

    assert result_option == option
    assert result_data == option_data


def test_option_request_invalid_magic():
    invalid_magic = 0x1234567890ABCDEF
    option = 7
    length = 10
    option_data = b"testdata12"
    header = struct.pack(">QII", invalid_magic, option, length)

    with pytest.raises(ValueError) as context:
        Requests.option_request(header, option_data)

    assert "Invalid option magic" in str(context.value)


def test_command_valid():
    flags = 0
    cmd_type = 0
    handle = 12345
    offset = 1024
    length = 4096
    data = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, flags, cmd_type, handle, offset, length)

    result_cmd_type, result_flags, result_handle, result_offset, result_length = (
        Requests.command(data)
    )

    assert result_cmd_type == cmd_type
    assert result_flags == flags
    assert result_handle == handle
    assert result_offset == offset
    assert result_length == length


def test_command_invalid_magic():
    invalid_magic = 0x12345678
    flags = 0
    cmd_type = 0
    handle = 12345
    offset = 1024
    length = 4096
    data = struct.pack(">IHHQQL", invalid_magic, flags, cmd_type, handle, offset, length)

    with pytest.raises(ValueError) as context:
        Requests.command(data)

    assert "Invalid request magic" in str(context.value)
