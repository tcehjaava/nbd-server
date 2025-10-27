import asyncio
import socket
import struct
import unittest

from helpers.s3_utils import cleanup_s3_async
from nbd_server.constants import (
    NBD_CMD_DISC,
    NBD_CMD_FLUSH,
    NBD_CMD_READ,
    NBD_CMD_WRITE,
    NBD_REQUEST_MAGIC,
    NBD_SIMPLE_REPLY_MAGIC,
)
from nbd_server.models import S3Config
from nbd_server.protocol.commands import TransmissionHandler
from nbd_server.storage.client import ClientManager
from nbd_server.storage.s3 import S3Storage


def create_test_s3_config() -> S3Config:
    return S3Config(
        endpoint_url="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="nbd-storage",
        region="us-east-1"
    )


class TestTransmissionHandler(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.s3_config = create_test_s3_config()
        self.client_manager = ClientManager(self.s3_config)
        self.export_name = "test-commands-export"
        self.block_size = 131072

        await cleanup_s3_async(
            self.client_manager,
            self.s3_config,
            export_name=self.export_name,
            cleanup_locks=True,
        )

    async def test_cmd_read(self):
        connection_id = "conn-read-test"
        server_id = "server-read"

        storage = await S3Storage.create(
            export_name=self.export_name,
            s3_config=self.s3_config,
            block_size=self.block_size,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=30,
            s3_client_manager=self.client_manager
        )

        try:
            test_data = b"Hello, NBD World!"
            test_offset = 1024
            test_handle = 0x123456789ABCDEF0

            await storage.write(test_offset, test_data)
            await storage.flush()

            handler = TransmissionHandler(storage=storage)

            client_sock, server_sock = socket.socketpair()

            def send_commands():
                read_cmd = struct.pack(
                    ">IHHQQL",
                    NBD_REQUEST_MAGIC,
                    0,
                    NBD_CMD_READ,
                    test_handle,
                    test_offset,
                    len(test_data)
                )
                client_sock.sendall(read_cmd)

                reply = client_sock.recv(16)
                reply_magic, error, reply_handle = struct.unpack(">IIQ", reply)
                assert reply_magic == NBD_SIMPLE_REPLY_MAGIC
                assert error == 0
                assert reply_handle == test_handle

                data = client_sock.recv(len(test_data))
                assert data == test_data

                disc_cmd = struct.pack(
                    ">IHHQQL",
                    NBD_REQUEST_MAGIC,
                    0,
                    NBD_CMD_DISC,
                    0,
                    0,
                    0
                )
                client_sock.sendall(disc_cmd)

            async def run_handler():
                reader, writer = await asyncio.open_connection(sock=server_sock)
                try:
                    await handler.process_commands(reader, writer)
                finally:
                    writer.close()
                    await writer.wait_closed()

            loop = asyncio.get_event_loop()
            await asyncio.gather(
                run_handler(),
                loop.run_in_executor(None, send_commands)
            )

        finally:
            client_sock.close()
            server_sock.close()
            await storage.release()

    async def test_cmd_write(self):
        connection_id = "conn-write-test"
        server_id = "server-write"

        storage = await S3Storage.create(
            export_name=self.export_name,
            s3_config=self.s3_config,
            block_size=self.block_size,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=30,
            s3_client_manager=self.client_manager
        )

        try:
            test_data = b"Write test data"
            test_offset = 2048
            test_handle = 0xABCDEF0123456789

            handler = TransmissionHandler(storage=storage)

            client_sock, server_sock = socket.socketpair()

            def send_commands():
                write_cmd = struct.pack(
                    ">IHHQQL",
                    NBD_REQUEST_MAGIC,
                    0,
                    NBD_CMD_WRITE,
                    test_handle,
                    test_offset,
                    len(test_data)
                ) + test_data
                client_sock.sendall(write_cmd)

                reply = client_sock.recv(16)
                reply_magic, error, reply_handle = struct.unpack(">IIQ", reply)
                assert reply_magic == NBD_SIMPLE_REPLY_MAGIC
                assert error == 0
                assert reply_handle == test_handle

                disc_cmd = struct.pack(
                    ">IHHQQL",
                    NBD_REQUEST_MAGIC,
                    0,
                    NBD_CMD_DISC,
                    0,
                    0,
                    0
                )
                client_sock.sendall(disc_cmd)

            async def run_handler():
                reader, writer = await asyncio.open_connection(sock=server_sock)
                try:
                    await handler.process_commands(reader, writer)
                finally:
                    writer.close()
                    await writer.wait_closed()

            loop = asyncio.get_event_loop()
            await asyncio.gather(
                run_handler(),
                loop.run_in_executor(None, send_commands)
            )

            read_data = await storage.read(test_offset, len(test_data))
            self.assertEqual(read_data, test_data)

        finally:
            client_sock.close()
            server_sock.close()
            await storage.release()

    async def test_cmd_flush(self):
        connection_id = "conn-flush-test"
        server_id = "server-flush"

        storage = await S3Storage.create(
            export_name=self.export_name,
            s3_config=self.s3_config,
            block_size=self.block_size,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=30,
            s3_client_manager=self.client_manager
        )

        try:
            test_handle = 0x1122334455667788

            handler = TransmissionHandler(storage=storage)

            client_sock, server_sock = socket.socketpair()

            def send_commands():
                flush_cmd = struct.pack(
                    ">IHHQQL",
                    NBD_REQUEST_MAGIC,
                    0,
                    NBD_CMD_FLUSH,
                    test_handle,
                    0,
                    0
                )
                client_sock.sendall(flush_cmd)

                reply = client_sock.recv(16)
                reply_magic, error, reply_handle = struct.unpack(">IIQ", reply)
                assert reply_magic == NBD_SIMPLE_REPLY_MAGIC
                assert error == 0
                assert reply_handle == test_handle

                disc_cmd = struct.pack(
                    ">IHHQQL",
                    NBD_REQUEST_MAGIC,
                    0,
                    NBD_CMD_DISC,
                    0,
                    0,
                    0
                )
                client_sock.sendall(disc_cmd)

            async def run_handler():
                reader, writer = await asyncio.open_connection(sock=server_sock)
                try:
                    await handler.process_commands(reader, writer)
                finally:
                    writer.close()
                    await writer.wait_closed()

            loop = asyncio.get_event_loop()
            await asyncio.gather(
                run_handler(),
                loop.run_in_executor(None, send_commands)
            )

        finally:
            client_sock.close()
            server_sock.close()
            await storage.release()


if __name__ == "__main__":
    unittest.main()
