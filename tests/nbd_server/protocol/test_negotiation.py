import asyncio
import struct
import unittest

from nbd_server.constants import IHAVEOPT, NBD_OPT_ABORT, NBD_OPT_GO, TRANSMISSION_FLAGS
from nbd_server.protocol.negotiation import NegotiationHandler
from nbd_server.protocol.messages import Responses


class FakeTransport:
    def __init__(self):
        self.written_data = bytearray()

    def write(self, data):
        self.written_data.extend(data)

    def is_closing(self):
        return False

    def close(self):
        pass


class FakeProtocol:
    async def _drain_helper(self):
        pass


class TestNegotiationHandler(unittest.IsolatedAsyncioTestCase):

    async def test_handshake(self):
        handler = NegotiationHandler(export_size=1024 * 1024 * 1024)

        transport = FakeTransport()
        protocol = FakeProtocol()
        writer = asyncio.StreamWriter(transport, protocol, None, asyncio.get_event_loop())

        await handler.handshake(writer)

        expected_data = Responses.handshake()

        self.assertEqual(bytes(transport.written_data), expected_data)

    async def test_receive_client_flags(self):
        handler = NegotiationHandler(export_size=1024 * 1024 * 1024)

        flags_value = 0x00000001
        flags_data = struct.pack(">I", flags_value)

        reader = asyncio.StreamReader()
        reader.feed_data(flags_data)
        reader.feed_eof()

        result = await handler.receive_client_flags(reader)

        self.assertEqual(result, flags_value)

    async def test_negotiate_export_with_nbd_opt_go(self):
        handler = NegotiationHandler(export_size=1024 * 1024 * 1024)

        export_name = "test-disk"
        export_name_bytes = export_name.encode("utf-8")
        export_name_length = len(export_name_bytes)

        option_data = struct.pack(">I", export_name_length) + export_name_bytes
        header = struct.pack(">QII", IHAVEOPT, NBD_OPT_GO, len(option_data))

        reader = asyncio.StreamReader()
        reader.feed_data(header)
        reader.feed_data(option_data)
        reader.feed_eof()

        transport = FakeTransport()
        writer = asyncio.StreamWriter(transport, FakeProtocol(), None, asyncio.get_event_loop())

        result = await handler.negotiate_export(reader, writer)

        self.assertEqual(result, export_name)

        expected_info_reply = Responses.info_reply(NBD_OPT_GO, 1024 * 1024 * 1024, TRANSMISSION_FLAGS)
        expected_ack_reply = Responses.ack_reply(NBD_OPT_GO)
        expected_data = expected_info_reply + expected_ack_reply

        self.assertEqual(bytes(transport.written_data), expected_data)

    async def test_negotiate_export_with_nbd_opt_abort(self):
        handler = NegotiationHandler(export_size=1024 * 1024 * 1024)

        header = struct.pack(">QII", IHAVEOPT, NBD_OPT_ABORT, 0)

        reader = asyncio.StreamReader()
        reader.feed_data(header)
        reader.feed_eof()

        transport = FakeTransport()
        writer = asyncio.StreamWriter(transport, FakeProtocol(), None, asyncio.get_event_loop())

        result = await handler.negotiate_export(reader, writer)

        self.assertIsNone(result)
        self.assertEqual(len(transport.written_data), 0)

    async def test_negotiate_export_with_unsupported_option(self):
        handler = NegotiationHandler(export_size=1024 * 1024 * 1024)

        unsupported_option = 0x12345678
        header = struct.pack(">QII", IHAVEOPT, unsupported_option, 0)

        reader = asyncio.StreamReader()
        reader.feed_data(header)
        reader.feed_eof()

        transport = FakeTransport()
        writer = asyncio.StreamWriter(transport, FakeProtocol(), None, asyncio.get_event_loop())

        result = await handler.negotiate_export(reader, writer)

        self.assertIsNone(result)
        self.assertEqual(len(transport.written_data), 0)
