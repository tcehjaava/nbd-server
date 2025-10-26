import asyncio
import json
import unittest

from botocore.exceptions import ClientError

from nbd_server.models import S3Config
from nbd_server.storage.client import ClientManager
from nbd_server.storage.lock import LeaseLock


def create_test_s3_config() -> S3Config:
    return S3Config(
        endpoint_url="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="nbd-storage",
        region="us-east-1"
    )


class TestLeaseLock(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.s3_config = create_test_s3_config()
        self.client_manager = ClientManager(self.s3_config)

        async with self.client_manager.get_client() as s3:
            try:
                response = await s3.list_objects_v2(
                    Bucket=self.s3_config.bucket,
                    Prefix="locks/"
                )

                if "Contents" in response:
                    objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]
                    if objects_to_delete:
                        await s3.delete_objects(
                            Bucket=self.s3_config.bucket,
                            Delete={"Objects": objects_to_delete}
                        )
            except ClientError:
                pass

    async def test_acquire_lock_first_time(self):
        export_name = "test-export-first-time"
        connection_id = "conn-123"
        server_id = "server-456"

        lock = LeaseLock(
            export_name=export_name,
            s3_config=self.s3_config,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=30
        )

        try:
            result = await lock.acquire()

            self.assertTrue(result)
            self.assertTrue(lock.is_active)
            self.assertIsNotNone(lock._heartbeat_task)

            async with self.client_manager.get_client() as s3:
                response = await s3.get_object(Bucket=self.s3_config.bucket, Key=lock._get_lock_key())
                self.assertIsNotNone(response)

        finally:
            await lock.release()

    async def test_acquire_already_active_lock_returns_true(self):
        export_name = "test-export-already-active"
        connection_id = "conn-123"
        server_id = "server-456"

        lock = LeaseLock(
            export_name=export_name,
            s3_config=self.s3_config,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=30
        )

        try:
            result = await lock.acquire()
            self.assertTrue(result)
            self.assertTrue(lock.is_active)

            async with self.client_manager.get_client() as s3:
                response = await s3.get_object(Bucket=self.s3_config.bucket, Key=lock._get_lock_key())
                first_lock_data = await response["Body"].read()

            result_second = await lock.acquire()
            self.assertTrue(result_second)
            self.assertTrue(lock.is_active)

            async with self.client_manager.get_client() as s3:
                response = await s3.get_object(Bucket=self.s3_config.bucket, Key=lock._get_lock_key())
                second_lock_data = await response["Body"].read()

            self.assertEqual(first_lock_data, second_lock_data)

        finally:
            await lock.release()

    async def test_acquire_expired_lock_from_different_connection(self):
        export_name = "test-export-expired"
        first_connection_id = "conn-crashed"
        second_connection_id = "conn-new"
        server_id = "server-456"
        lease_duration = 2

        first_lock = LeaseLock(
            export_name=export_name,
            s3_config=self.s3_config,
            connection_id=first_connection_id,
            server_id=server_id,
            lease_duration=lease_duration
        )

        result = await first_lock.acquire()
        self.assertTrue(result)
        self.assertTrue(first_lock.is_active)

        async with self.client_manager.get_client() as s3:
            response = await s3.get_object(Bucket=self.s3_config.bucket, Key=first_lock._get_lock_key())
            first_lock_data = json.loads(await response["Body"].read())
            self.assertEqual(first_lock_data["connection_id"], first_connection_id)

        first_lock.is_active = False
        if first_lock._heartbeat_task and not first_lock._heartbeat_task.done():
            first_lock._heartbeat_task.cancel()
            try:
                await first_lock._heartbeat_task
            except asyncio.CancelledError:
                pass

        await asyncio.sleep(lease_duration + 0.5)

        second_lock = LeaseLock(
            export_name=export_name,
            s3_config=self.s3_config,
            connection_id=second_connection_id,
            server_id=server_id,
            lease_duration=lease_duration
        )

        try:
            result = await second_lock.acquire()
            self.assertTrue(result)
            self.assertTrue(second_lock.is_active)

            async with self.client_manager.get_client() as s3:
                response = await s3.get_object(Bucket=self.s3_config.bucket, Key=second_lock._get_lock_key())
                second_lock_data = json.loads(await response["Body"].read())
                self.assertEqual(second_lock_data["connection_id"], second_connection_id)
                self.assertNotEqual(second_lock_data["timestamp"], first_lock_data["timestamp"])

        finally:
            await second_lock.release()

    async def test_lock_renewal_by_heartbeat(self):
        export_name = "test-export-heartbeat"
        connection_id = "conn-123"
        server_id = "server-456"
        lease_duration = 2

        lock = LeaseLock(
            export_name=export_name,
            s3_config=self.s3_config,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=lease_duration
        )

        try:
            result = await lock.acquire()
            self.assertTrue(result)
            self.assertTrue(lock.is_active)

            async with self.client_manager.get_client() as s3:
                response = await s3.get_object(Bucket=self.s3_config.bucket, Key=lock._get_lock_key())
                first_lock_data = json.loads(await response["Body"].read())

            await asyncio.sleep(lock.renew_interval + 0.5)

            async with self.client_manager.get_client() as s3:
                response = await s3.get_object(Bucket=self.s3_config.bucket, Key=lock._get_lock_key())
                second_lock_data = json.loads(await response["Body"].read())

            self.assertEqual(second_lock_data["connection_id"], first_lock_data["connection_id"])
            self.assertEqual(second_lock_data["server_id"], first_lock_data["server_id"])
            self.assertGreater(second_lock_data["timestamp"], first_lock_data["timestamp"])
            self.assertGreater(second_lock_data["expires_at"], first_lock_data["expires_at"])

        finally:
            await lock.release()

    async def test_acquire_active_lock_by_different_connection_fails(self):
        export_name = "test-export-contention"
        first_connection_id = "conn-123"
        second_connection_id = "conn-456"
        server_id = "server-789"
        lease_duration = 10

        first_lock = LeaseLock(
            export_name=export_name,
            s3_config=self.s3_config,
            connection_id=first_connection_id,
            server_id=server_id,
            lease_duration=lease_duration
        )

        try:
            result = await first_lock.acquire()
            self.assertTrue(result)
            self.assertTrue(first_lock.is_active)

            async with self.client_manager.get_client() as s3:
                response = await s3.get_object(Bucket=self.s3_config.bucket, Key=first_lock._get_lock_key())
                first_lock_data = json.loads(await response["Body"].read())
                self.assertEqual(first_lock_data["connection_id"], first_connection_id)

            second_lock = LeaseLock(
                export_name=export_name,
                s3_config=self.s3_config,
                connection_id=second_connection_id,
                server_id=server_id,
                lease_duration=lease_duration
            )

            result = await second_lock.acquire()
            self.assertFalse(result)
            self.assertFalse(second_lock.is_active)

            async with self.client_manager.get_client() as s3:
                response = await s3.get_object(Bucket=self.s3_config.bucket, Key=first_lock._get_lock_key())
                still_first_lock_data = json.loads(await response["Body"].read())
                self.assertEqual(still_first_lock_data["connection_id"], first_connection_id)
                self.assertEqual(still_first_lock_data["timestamp"], first_lock_data["timestamp"])

        finally:
            await first_lock.release()

    async def test_release_deletes_lock_from_s3(self):
        export_name = "test-export-release"
        connection_id = "conn-123"
        server_id = "server-456"
        lease_duration = 30

        lock = LeaseLock(
            export_name=export_name,
            s3_config=self.s3_config,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=lease_duration
        )

        result = await lock.acquire()
        self.assertTrue(result)
        self.assertTrue(lock.is_active)

        async with self.client_manager.get_client() as s3:
            response = await s3.get_object(Bucket=self.s3_config.bucket, Key=lock._get_lock_key())
            lock_data = json.loads(await response["Body"].read())
            self.assertEqual(lock_data["connection_id"], connection_id)
            self.assertEqual(lock_data["server_id"], server_id)

        await lock.release()

        self.assertFalse(lock.is_active)

        async with self.client_manager.get_client() as s3:
            with self.assertRaises(ClientError) as context:
                await s3.get_object(Bucket=self.s3_config.bucket, Key=lock._get_lock_key())
            self.assertEqual(context.exception.response["Error"]["Code"], "NoSuchKey")

    async def test_lock_as_context_manager(self):
        export_name = "test-export-context-manager"
        connection_id = "conn-789"
        server_id = "server-012"
        lease_duration = 30

        lock = LeaseLock(
            export_name=export_name,
            s3_config=self.s3_config,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=lease_duration
        )

        self.assertFalse(lock.is_active)

        async with lock:
            self.assertTrue(lock.is_active)

            async with self.client_manager.get_client() as s3:
                response = await s3.get_object(Bucket=self.s3_config.bucket, Key=lock._get_lock_key())
                lock_data = json.loads(await response["Body"].read())
                self.assertEqual(lock_data["connection_id"], connection_id)
                self.assertEqual(lock_data["server_id"], server_id)
                self.assertIn("timestamp", lock_data)
                self.assertIn("expires_at", lock_data)
                self.assertIn("hostname", lock_data)
                self.assertIn("pid", lock_data)

        self.assertFalse(lock.is_active)

        async with self.client_manager.get_client() as s3:
            with self.assertRaises(ClientError) as context:
                await s3.get_object(Bucket=self.s3_config.bucket, Key=lock._get_lock_key())
            self.assertEqual(context.exception.response["Error"]["Code"], "NoSuchKey")

    async def test_context_manager_raises_when_lock_unavailable(self):
        export_name = "test-export-context-manager-unavailable"
        first_connection_id = "conn-first"
        second_connection_id = "conn-second"
        server_id = "server-345"
        lease_duration = 30

        first_lock = LeaseLock(
            export_name=export_name,
            s3_config=self.s3_config,
            connection_id=first_connection_id,
            server_id=server_id,
            lease_duration=lease_duration
        )

        second_lock = LeaseLock(
            export_name=export_name,
            s3_config=self.s3_config,
            connection_id=second_connection_id,
            server_id=server_id,
            lease_duration=lease_duration
        )

        try:
            async with first_lock:
                self.assertTrue(first_lock.is_active)

                with self.assertRaises(RuntimeError) as context:
                    async with second_lock:
                        pass

                self.assertIn("Failed to acquire lock", str(context.exception))
                self.assertFalse(second_lock.is_active)

        finally:
            if first_lock.is_active:
                await first_lock.release()


if __name__ == "__main__":
    unittest.main()
