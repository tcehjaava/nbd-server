import aioboto3
import pytest

from nbd_server.async_storage import S3Storage
from nbd_server.models import S3Config
from nbd_server.constants import (
    DEFAULT_S3_ACCESS_KEY,
    DEFAULT_S3_BUCKET,
    DEFAULT_S3_ENDPOINT,
    DEFAULT_S3_REGION,
    DEFAULT_S3_SECRET_KEY,
)

DEFAULT_TEST_BLOCK_SIZE = 131072  # 128KB


@pytest.fixture
async def cleanup_s3():
    export_name = "test-export"

    async def _cleanup():
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=DEFAULT_S3_ENDPOINT,
            aws_access_key_id=DEFAULT_S3_ACCESS_KEY,
            aws_secret_access_key=DEFAULT_S3_SECRET_KEY,
            region_name=DEFAULT_S3_REGION,
        ) as s3:
            prefix = f"blocks/{export_name}/"
            try:
                response = await s3.list_objects_v2(Bucket=DEFAULT_S3_BUCKET, Prefix=prefix)
                if "Contents" in response:
                    objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]
                    await s3.delete_objects(
                        Bucket=DEFAULT_S3_BUCKET, Delete={"Objects": objects_to_delete}
                    )
            except Exception:
                pass

    await _cleanup()
    yield
    await _cleanup()


async def create_storage(export_name="test-export", block_size=DEFAULT_TEST_BLOCK_SIZE):
    s3_config = S3Config(
        endpoint_url=DEFAULT_S3_ENDPOINT,
        access_key=DEFAULT_S3_ACCESS_KEY,
        secret_key=DEFAULT_S3_SECRET_KEY,
        bucket=DEFAULT_S3_BUCKET,
        region=DEFAULT_S3_REGION,
    )
    return await S3Storage.create(
        export_name=export_name,
        s3_config=s3_config,
        block_size=block_size,
    )


@pytest.mark.asyncio
async def test_s3_write_flush_read_single_block(cleanup_s3):
    storage = await create_storage()

    test_data = b"Hello, S3 Storage!"
    offset = 0

    await storage.write(offset, test_data)
    await storage.flush()

    read_data = await storage.read(offset, len(test_data))
    assert read_data == test_data


@pytest.mark.asyncio
async def test_s3_write_flush_read_multiple_blocks(cleanup_s3):
    test_block_size = 1024
    storage = await create_storage(block_size=test_block_size)

    test_data = b"X" * (test_block_size * 2 + 512)
    offset = 0

    await storage.write(offset, test_data)
    await storage.flush()

    read_data = await storage.read(offset, len(test_data))
    assert len(read_data) == 2560
    assert read_data == test_data


@pytest.mark.asyncio
async def test_s3_read_your_writes_before_flush(cleanup_s3):
    storage = await create_storage()

    test_data = b"Unflushed data in dirty blocks"
    offset = 0

    await storage.write(offset, test_data)

    read_data = await storage.read(offset, len(test_data))
    assert read_data == test_data


@pytest.mark.asyncio
async def test_s3_read_unwritten_region_returns_zeros(cleanup_s3):
    storage = await create_storage()

    read_data = await storage.read(offset=5000, length=100)

    assert len(read_data) == 100
    assert read_data == b"\x00" * 100


@pytest.mark.asyncio
async def test_s3_write_read_non_aligned_offset_multiple_blocks(cleanup_s3):
    test_block_size = 1024
    storage = await create_storage(block_size=test_block_size)

    test_data = b"Y" * 2000
    offset = 500

    await storage.write(offset, test_data)
    await storage.flush()

    read_data = await storage.read(offset, len(test_data))
    assert len(read_data) == 2000
    assert read_data == test_data


@pytest.mark.asyncio
async def test_s3_partial_block_read_mixed_regions(cleanup_s3):
    test_block_size = 1024
    storage = await create_storage(block_size=test_block_size)

    test_data = b"Z" * 50
    await storage.write(offset=100, data=test_data)
    await storage.flush()

    read_data = await storage.read(offset=50, length=200)

    expected = b"\x00" * 50 + test_data + b"\x00" * 100
    assert len(read_data) == 200
    assert read_data == expected


@pytest.mark.asyncio
async def test_s3_unflushed_data_lost_on_restart(cleanup_s3):
    test_data = b"Data that should persist"
    offset = 0

    storage1 = await create_storage()

    await storage1.write(offset, test_data)
    read_data = await storage1.read(offset, len(test_data))
    assert read_data == test_data

    storage2 = await create_storage()

    read_data = await storage2.read(offset, len(test_data))
    assert read_data == b"\x00" * len(test_data)

    await storage2.write(offset, test_data)
    await storage2.flush()

    storage3 = await create_storage()

    read_data = await storage3.read(offset, len(test_data))
    assert read_data == test_data
