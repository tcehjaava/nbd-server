import unittest

from ..s3_utils import cleanup_s3_async
from nbd_server.models import S3Config
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


class TestS3Storage(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.s3_config = create_test_s3_config()
        self.client_manager = ClientManager(self.s3_config)
        self.export_name = "test-export"
        self.block_size = 131072

        await cleanup_s3_async(
            self.client_manager,
            self.s3_config,
            export_name=self.export_name,
            cleanup_locks=True,
        )

    async def test_create_returns_instance_with_acquired_lock(self):
        connection_id = "conn-123"
        server_id = "server-456"

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
            self.assertIsNotNone(storage)
            self.assertEqual(storage.export_name, self.export_name)
            self.assertEqual(storage.bucket, self.s3_config.bucket)
            self.assertEqual(storage.block_size, self.block_size)
            self.assertEqual(storage.connection_id, connection_id)
            self.assertEqual(storage.server_id, server_id)
            self.assertTrue(storage.lease_lock.is_active)
            self.assertIsNotNone(storage.lease_lock._heartbeat_task)

            async with self.client_manager.get_client() as s3:
                response = await s3.get_object(
                    Bucket=self.s3_config.bucket,
                    Key=storage.lease_lock._get_lock_key()
                )
                self.assertIsNotNone(response)

        finally:
            await storage.release()

    async def test_create_fails_when_export_already_in_use(self):
        first_connection_id = "conn-123"
        second_connection_id = "conn-456"
        server_id = "server-789"

        first_storage = await S3Storage.create(
            export_name=self.export_name,
            s3_config=self.s3_config,
            block_size=self.block_size,
            connection_id=first_connection_id,
            server_id=server_id,
            lease_duration=30,
            s3_client_manager=self.client_manager
        )

        try:
            self.assertIsNotNone(first_storage)
            self.assertTrue(first_storage.lease_lock.is_active)

            with self.assertRaises(RuntimeError) as context:
                await S3Storage.create(
                    export_name=self.export_name,
                    s3_config=self.s3_config,
                    block_size=self.block_size,
                    connection_id=second_connection_id,
                    server_id=server_id,
                    lease_duration=30,
                    s3_client_manager=self.client_manager
                )

            self.assertIn("Failed to acquire lease lock", str(context.exception))
            self.assertIn(self.export_name, str(context.exception))
            self.assertIn("already in use", str(context.exception))

        finally:
            await first_storage.release()

    async def test_write_across_blocks_without_flush_returns_zeros(self):
        connection_id = "conn-write-no-flush"
        server_id = "server-123"

        block_10_start = 10 * self.block_size
        offset = block_10_start + self.block_size - 50
        test_data = b"X" * 100

        storage1 = await S3Storage.create(
            export_name=self.export_name,
            s3_config=self.s3_config,
            block_size=self.block_size,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=30,
            s3_client_manager=self.client_manager
        )

        try:
            await storage1.write(offset, test_data)

            read_data = await storage1.read(offset, len(test_data))
            self.assertEqual(read_data, test_data)

        finally:
            await storage1.release()

        storage2 = await S3Storage.create(
            export_name=self.export_name,
            s3_config=self.s3_config,
            block_size=self.block_size,
            connection_id=f"{connection_id}-2",
            server_id=server_id,
            lease_duration=30,
            s3_client_manager=self.client_manager
        )

        try:
            read_data = await storage2.read(offset, len(test_data))
            self.assertEqual(read_data, b"\x00" * len(test_data))

        finally:
            await storage2.release()

    async def test_write_across_blocks_with_flush_persists_data(self):
        connection_id = "conn-write-flush"
        server_id = "server-456"

        block_10_start = 10 * self.block_size
        offset = block_10_start + self.block_size - 50
        test_data = b"Y" * 100

        storage1 = await S3Storage.create(
            export_name=self.export_name,
            s3_config=self.s3_config,
            block_size=self.block_size,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=30,
            s3_client_manager=self.client_manager
        )

        try:
            await storage1.write(offset, test_data)

            read_data = await storage1.read(offset, len(test_data))
            self.assertEqual(read_data, test_data)

            await storage1.flush()

        finally:
            await storage1.release()

        storage2 = await S3Storage.create(
            export_name=self.export_name,
            s3_config=self.s3_config,
            block_size=self.block_size,
            connection_id=f"{connection_id}-2",
            server_id=server_id,
            lease_duration=30,
            s3_client_manager=self.client_manager
        )

        try:
            read_data = await storage2.read(offset, len(test_data))
            self.assertEqual(read_data, test_data)

        finally:
            await storage2.release()


if __name__ == "__main__":
    unittest.main()
