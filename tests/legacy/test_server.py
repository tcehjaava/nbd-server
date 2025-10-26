import socket
import struct
import threading
import unittest

import boto3

from nbd_server.constants import (
    DEFAULT_S3_ACCESS_KEY,
    DEFAULT_S3_BUCKET,
    DEFAULT_S3_ENDPOINT,
    DEFAULT_S3_REGION,
    DEFAULT_S3_SECRET_KEY,
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
from nbd_server.legacy.server import NBDServer
from nbd_server.legacy.storage import S3Storage

NBD_FLAG_C_FIXED_NEWSTYLE = 0x00000001
DEFAULT_TEST_BLOCK_SIZE = 131072


class BaseNBDServerTest(unittest.TestCase):

    def setUp(self):
        self.export_name = "test-server-export"
        self._cleanup_s3_blocks()

    def _cleanup_s3_blocks(self):
        s3_client = boto3.client(
            "s3",
            endpoint_url=DEFAULT_S3_ENDPOINT,
            aws_access_key_id=DEFAULT_S3_ACCESS_KEY,
            aws_secret_access_key=DEFAULT_S3_SECRET_KEY,
            region_name=DEFAULT_S3_REGION,
        )

        prefix = f"blocks/{self.export_name}/"

        try:
            response = s3_client.list_objects_v2(Bucket=DEFAULT_S3_BUCKET, Prefix=prefix)
            if "Contents" in response:
                objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]
                s3_client.delete_objects(
                    Bucket=DEFAULT_S3_BUCKET, Delete={"Objects": objects_to_delete}
                )
        except Exception:
            pass

    def _create_storage(self, block_size=DEFAULT_TEST_BLOCK_SIZE):
        return S3Storage.create(
            export_name=self.export_name,
            endpoint_url=DEFAULT_S3_ENDPOINT,
            access_key=DEFAULT_S3_ACCESS_KEY,
            secret_key=DEFAULT_S3_SECRET_KEY,
            bucket=DEFAULT_S3_BUCKET,
            region=DEFAULT_S3_REGION,
            block_size=block_size,
        )


class TestNBDServerHandshake(BaseNBDServerTest):

    def test_handshake_sent_on_connection(self):
        storage = self._create_storage()
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


class TestNBDServerClientFlags(BaseNBDServerTest):

    def test_parse_client_flags(self):
        storage = self._create_storage()
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


class TestNBDServerNegotiation(BaseNBDServerTest):

    def test_negotiation_with_opt_go(self):
        storage = self._create_storage()
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
        storage = self._create_storage()
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


class TestNBDServerTransmission(BaseNBDServerTest):

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
        storage = self._create_storage()
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
        storage = self._create_storage()
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
        storage = self._create_storage()
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
        storage = self._create_storage()
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
