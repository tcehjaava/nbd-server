import unittest

import boto3

from nbd_server.constants import (
    DEFAULT_S3_ACCESS_KEY,
    DEFAULT_S3_BUCKET,
    DEFAULT_S3_ENDPOINT,
    DEFAULT_S3_REGION,
    DEFAULT_S3_SECRET_KEY,
)
from nbd_server.legacy.storage import S3Storage

DEFAULT_TEST_BLOCK_SIZE = 131072  # 128KB


class TestS3Storage(unittest.TestCase):

    def setUp(self):
        self.export_name = "test-export"
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

    def test_s3_write_flush_read_single_block(self):
        storage = self._create_storage()

        test_data = b"Hello, S3 Storage!"
        offset = 0

        storage.write(offset, test_data)
        storage.flush()

        read_data = storage.read(offset, len(test_data))
        self.assertEqual(read_data, test_data)

    def test_s3_write_flush_read_multiple_blocks(self):
        test_block_size = 1024
        storage = self._create_storage(block_size=test_block_size)

        test_data = b"X" * (test_block_size * 2 + 512)
        offset = 0

        storage.write(offset, test_data)
        storage.flush()

        read_data = storage.read(offset, len(test_data))
        self.assertEqual(len(read_data), 2560)
        self.assertEqual(read_data, test_data)

    def test_s3_read_your_writes_before_flush(self):
        storage = self._create_storage()

        test_data = b"Unflushed data in dirty blocks"
        offset = 0

        storage.write(offset, test_data)

        read_data = storage.read(offset, len(test_data))
        self.assertEqual(read_data, test_data)

    def test_s3_read_unwritten_region_returns_zeros(self):
        storage = self._create_storage()

        read_data = storage.read(offset=5000, length=100)

        self.assertEqual(len(read_data), 100)
        self.assertEqual(read_data, b"\x00" * 100)

    def test_s3_write_read_non_aligned_offset_multiple_blocks(self):
        test_block_size = 1024
        storage = self._create_storage(block_size=test_block_size)

        test_data = b"Y" * 2000
        offset = 500

        storage.write(offset, test_data)
        storage.flush()

        read_data = storage.read(offset, len(test_data))
        self.assertEqual(len(read_data), 2000)
        self.assertEqual(read_data, test_data)

    def test_s3_partial_block_read_mixed_regions(self):
        test_block_size = 1024
        storage = self._create_storage(block_size=test_block_size)

        test_data = b"Z" * 50
        storage.write(offset=100, data=test_data)
        storage.flush()

        read_data = storage.read(offset=50, length=200)

        expected = b"\x00" * 50 + test_data + b"\x00" * 100
        self.assertEqual(len(read_data), 200)
        self.assertEqual(read_data, expected)

    def test_s3_unflushed_data_lost_on_restart(self):
        test_data = b"Data that should persist"
        offset = 0

        storage1 = self._create_storage()

        storage1.write(offset, test_data)
        read_data = storage1.read(offset, len(test_data))
        self.assertEqual(read_data, test_data)

        storage2 = self._create_storage()

        read_data = storage2.read(offset, len(test_data))
        self.assertEqual(read_data, b"\x00" * len(test_data))

        storage2.write(offset, test_data)
        storage2.flush()

        storage3 = self._create_storage()

        read_data = storage3.read(offset, len(test_data))
        self.assertEqual(read_data, test_data)


if __name__ == "__main__":
    unittest.main()
