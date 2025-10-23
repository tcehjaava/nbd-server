import socket
import struct
import threading
import unittest

from nbd_server.constants import (
    IHAVEOPT,
    NBD_CMD_DISC,
    NBD_CMD_FLUSH,
    NBD_CMD_READ,
    NBD_CMD_WRITE,
    NBD_FLAG_FIXED_NEWSTYLE,
    NBD_OPT_ABORT,
    NBD_OPT_GO,
    NBD_REP_ACK,
    NBD_REP_INFO,
    NBD_REP_MAGIC,
    NBD_REQUEST_MAGIC,
    NBD_SIMPLE_REPLY_MAGIC,
    NBDMAGIC,
)
from nbd_server.server import NBDServer
from nbd_server.storage import InMemoryStorage

NBD_FLAG_C_FIXED_NEWSTYLE = 0x00000001


class TestNBDServerHandshake(unittest.TestCase):

    def test_handshake_sent_on_connection(self):
        storage = InMemoryStorage()
        server = NBDServer(storage)

        client_socket, server_socket = socket.socketpair()

        def run_server():
            try:
                server._handle_connection(server_socket)
            except Exception:
                pass
            finally:
                server_socket.close()

        try:
            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()

            handshake_data = client_socket.recv(18)
            self.assertEqual(len(handshake_data), 18)

            magic, ihaveopt, flags = struct.unpack(">QQH", handshake_data)
            self.assertEqual(magic, NBDMAGIC)
            self.assertEqual(ihaveopt, IHAVEOPT)
            self.assertEqual(flags, NBD_FLAG_FIXED_NEWSTYLE)

        finally:
            client_socket.close()


class TestNBDServerClientFlags(unittest.TestCase):

    def test_parse_client_flags(self):
        storage = InMemoryStorage()
        server = NBDServer(storage)

        client_socket, server_socket = socket.socketpair()

        def run_server():
            try:
                server._handle_connection(server_socket)
            except Exception:
                pass
            finally:
                server_socket.close()

        try:
            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()

            client_socket.recv(18)

            client_socket.sendall(struct.pack(">I", NBD_FLAG_C_FIXED_NEWSTYLE))

            option_header = struct.pack(">QII", IHAVEOPT, NBD_OPT_ABORT, 0)
            bytes_sent = client_socket.send(option_header)
            self.assertEqual(bytes_sent, 16)

        finally:
            client_socket.close()


class TestNBDServerNegotiation(unittest.TestCase):

    def test_negotiation_with_opt_go(self):
        storage = InMemoryStorage()
        server = NBDServer(storage)

        client_socket, server_socket = socket.socketpair()

        def run_server():
            try:
                server._handle_connection(server_socket)
            except Exception:
                pass
            finally:
                server_socket.close()

        try:
            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()

            client_socket.recv(18)
            client_socket.sendall(struct.pack(">I", NBD_FLAG_C_FIXED_NEWSTYLE))

            export_name = b"test"
            option_data = struct.pack(">I", len(export_name)) + export_name + struct.pack(">H", 0)
            option_header = struct.pack(">QII", IHAVEOPT, NBD_OPT_GO, len(option_data))
            client_socket.sendall(option_header + option_data)

            info_reply = client_socket.recv(32)
            self.assertEqual(len(info_reply), 32)
            magic, option, reply_type, length = struct.unpack(">QIII", info_reply[:20])
            self.assertEqual(magic, NBD_REP_MAGIC)
            self.assertEqual(option, NBD_OPT_GO)
            self.assertEqual(reply_type, NBD_REP_INFO)

            ack_reply = client_socket.recv(20)
            self.assertEqual(len(ack_reply), 20)
            magic, option, reply_type, length = struct.unpack(">QIII", ack_reply)
            self.assertEqual(magic, NBD_REP_MAGIC)
            self.assertEqual(option, NBD_OPT_GO)
            self.assertEqual(reply_type, NBD_REP_ACK)
            self.assertEqual(length, 0)

        finally:
            client_socket.close()

    def test_negotiation_with_opt_abort(self):
        storage = InMemoryStorage()
        server = NBDServer(storage)

        client_socket, server_socket = socket.socketpair()

        def run_server():
            try:
                server._handle_connection(server_socket)
            except Exception:
                pass
            finally:
                server_socket.close()

        try:
            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()

            client_socket.recv(18)
            client_socket.sendall(struct.pack(">I", NBD_FLAG_C_FIXED_NEWSTYLE))

            option_header = struct.pack(">QII", IHAVEOPT, NBD_OPT_ABORT, 0)
            client_socket.sendall(option_header)

            client_socket.settimeout(0.5)
            data = client_socket.recv(1024)
            self.assertEqual(data, b"")

        finally:
            client_socket.close()


class TestNBDServerTransmission(unittest.TestCase):

    def _negotiate_and_enter_transmission(self, client_socket):
        client_socket.recv(18)
        client_socket.sendall(struct.pack(">I", NBD_FLAG_C_FIXED_NEWSTYLE))

        export_name = b"test"
        option_data = struct.pack(">I", len(export_name)) + export_name + struct.pack(">H", 0)
        option_header = struct.pack(">QII", IHAVEOPT, NBD_OPT_GO, len(option_data))
        client_socket.sendall(option_header + option_data)

        client_socket.recv(32)
        client_socket.recv(20)

    def test_transmission_cmd_read(self):
        storage = InMemoryStorage()
        test_data = b"hello world"
        storage.write(0, test_data)
        server = NBDServer(storage)

        client_socket, server_socket = socket.socketpair()

        def run_server():
            try:
                server._handle_connection(server_socket)
            except Exception:
                pass
            finally:
                server_socket.close()

        try:
            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()

            self._negotiate_and_enter_transmission(client_socket)

            handle = 12345
            offset = 0
            length = len(test_data)
            cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_READ, handle, offset, length)
            client_socket.sendall(cmd)

            reply = client_socket.recv(16)
            magic, error, reply_handle = struct.unpack(">IIQ", reply)
            self.assertEqual(magic, NBD_SIMPLE_REPLY_MAGIC)
            self.assertEqual(error, 0)
            self.assertEqual(reply_handle, handle)

            data = client_socket.recv(length)
            self.assertEqual(data, test_data)

        finally:
            client_socket.close()

    def test_transmission_cmd_write(self):
        storage = InMemoryStorage()
        server = NBDServer(storage)

        client_socket, server_socket = socket.socketpair()

        def run_server():
            try:
                server._handle_connection(server_socket)
            except Exception:
                pass
            finally:
                server_socket.close()

        try:
            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()

            self._negotiate_and_enter_transmission(client_socket)

            handle = 12346
            offset = 0
            test_data = b"test write"
            length = len(test_data)
            cmd = struct.pack(
                ">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_WRITE, handle, offset, length
            )
            client_socket.sendall(cmd + test_data)

            reply = client_socket.recv(16)
            magic, error, reply_handle = struct.unpack(">IIQ", reply)
            self.assertEqual(magic, NBD_SIMPLE_REPLY_MAGIC)
            self.assertEqual(error, 0)
            self.assertEqual(reply_handle, handle)

            read_data = storage.read(offset, length)
            self.assertEqual(read_data, test_data)

        finally:
            client_socket.close()

    def test_transmission_cmd_flush(self):
        storage = InMemoryStorage()
        server = NBDServer(storage)

        client_socket, server_socket = socket.socketpair()

        def run_server():
            try:
                server._handle_connection(server_socket)
            except Exception:
                pass
            finally:
                server_socket.close()

        try:
            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()

            self._negotiate_and_enter_transmission(client_socket)

            handle = 12347
            cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_FLUSH, handle, 0, 0)
            client_socket.sendall(cmd)

            reply = client_socket.recv(16)
            magic, error, reply_handle = struct.unpack(">IIQ", reply)
            self.assertEqual(magic, NBD_SIMPLE_REPLY_MAGIC)
            self.assertEqual(error, 0)
            self.assertEqual(reply_handle, handle)

        finally:
            client_socket.close()

    def test_transmission_cmd_disc(self):
        storage = InMemoryStorage()
        server = NBDServer(storage)

        client_socket, server_socket = socket.socketpair()

        def run_server():
            try:
                server._handle_connection(server_socket)
            except Exception:
                pass
            finally:
                server_socket.close()

        try:
            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()

            self._negotiate_and_enter_transmission(client_socket)

            handle = 12348
            cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_DISC, handle, 0, 0)
            client_socket.sendall(cmd)

            client_socket.settimeout(0.5)
            data = client_socket.recv(1024)
            self.assertEqual(data, b"")

        finally:
            client_socket.close()


if __name__ == "__main__":
    unittest.main()
