import asyncio
import unittest

from helpers.nbd_client import NBDTestClient
from helpers.s3_utils import cleanup_s3_async
from nbd_server.constants import parse_size
from nbd_server.models import S3Config
from nbd_server.server import NBDServer
from nbd_server.storage.client import ClientManager


def create_test_s3_config() -> S3Config:
    return S3Config(
        endpoint_url="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="nbd-storage",
        region="us-east-1",
    )


class TestNBDServer(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.s3_config = create_test_s3_config()
        self.client_manager = ClientManager(self.s3_config)
        self.export_name = "test-server-export"
        self.block_size = 131072
        self.server_port = 10810

        self.server = NBDServer(
            s3_config=self.s3_config,
            block_size=self.block_size,
            host="localhost",
            port=self.server_port,
            export_size=parse_size("1GB"),
        )

        self.server_task = asyncio.create_task(self.server.run())
        await asyncio.sleep(0.2)

    async def asyncTearDown(self):
        if self.server_task:
            self.server_task.cancel()
            try:
                await self.server_task
            except asyncio.CancelledError:
                pass

        await cleanup_s3_async(
            self.client_manager,
            self.s3_config,
            export_name=self.export_name,
            cleanup_locks=True,
        )

    async def test_server_write_and_read_basic(self):
        client = NBDTestClient(host="localhost", port=self.server_port)

        async with client:
            await client.connect(export_name=self.export_name)

            test_data = b"Hello, NBD Server!"
            await client.pwrite(test_data, 0)

            read_data = await client.pread(len(test_data), 0)
            self.assertEqual(read_data, test_data)

    async def test_server_write_and_read_with_offset(self):
        client = NBDTestClient(host="localhost", port=self.server_port)

        async with client:
            await client.connect(export_name=self.export_name)

            test_data = b"Test data at offset 4096"
            offset = 4096
            await client.pwrite(test_data, offset)

            read_data = await client.pread(len(test_data), offset)
            self.assertEqual(read_data, test_data)

    async def test_server_write_across_blocks(self):
        client = NBDTestClient(host="localhost", port=self.server_port)

        async with client:
            await client.connect(export_name=self.export_name)

            block_10_start = 10 * self.block_size
            offset = block_10_start + self.block_size - 50
            test_data = b"X" * 100

            await client.pwrite(test_data, offset)

            read_data = await client.pread(len(test_data), offset)
            self.assertEqual(read_data, test_data)

    async def test_server_flush_persists_data(self):
        client1 = NBDTestClient(host="localhost", port=self.server_port)

        async with client1:
            await client1.connect(export_name=self.export_name)

            test_data = b"Persistent data after flush"
            offset = 8192
            await client1.pwrite(test_data, offset)
            await client1.flush()

        await asyncio.sleep(0.5)

        client2 = NBDTestClient(host="localhost", port=self.server_port)

        async with client2:
            await client2.connect(export_name=self.export_name)

            read_data = await client2.pread(len(test_data), offset)
            self.assertEqual(read_data, test_data)

    async def test_server_multiple_sequential_operations(self):
        client = NBDTestClient(host="localhost", port=self.server_port)

        async with client:
            await client.connect(export_name=self.export_name)

            data1 = b"First write"
            data2 = b"Second write at different offset"
            data3 = b"Third write"

            await client.pwrite(data1, 0)
            await client.pwrite(data2, 1024)
            await client.pwrite(data3, 2048)

            read1 = await client.pread(len(data1), 0)
            read2 = await client.pread(len(data2), 1024)
            read3 = await client.pread(len(data3), 2048)

            self.assertEqual(read1, data1)
            self.assertEqual(read2, data2)
            self.assertEqual(read3, data3)

    async def test_server_read_unwritten_data_returns_zeros(self):
        client = NBDTestClient(host="localhost", port=self.server_port)

        async with client:
            await client.connect(export_name=self.export_name)

            offset = 16384
            length = 128
            read_data = await client.pread(length, offset)

            self.assertEqual(read_data, b"\x00" * length)

    async def test_server_get_export_size(self):
        client = NBDTestClient(host="localhost", port=self.server_port)

        async with client:
            await client.connect(export_name=self.export_name)

            export_size = client.get_size()
            self.assertEqual(export_size, parse_size("1GB"))


if __name__ == "__main__":
    unittest.main()
