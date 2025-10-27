import asyncio
import logging
from typing import AsyncGenerator, Optional

import aiorwlock
from botocore.exceptions import ClientError

from .base import StorageBackend
from .client import ClientManager
from .lock import LeaseLock
from ..models import S3Config

logger = logging.getLogger(__name__)


class S3Storage(StorageBackend):
    """S3-backed storage using fixed-size blocks with distributed locking."""

    def __init__(
        self,
        export_name: str,
        s3_config: S3Config,
        block_size: int,
        connection_id: str,
        server_id: str,
        lease_duration: int = 30,
        s3_client_manager: Optional[ClientManager] = None,
    ) -> None:
        self.export_name = export_name
        self.s3_config_model = s3_config
        self.bucket = s3_config.bucket
        self.block_size = block_size
        self.connection_id = connection_id
        self.server_id = server_id
        self.dirty_blocks: dict[int, bytes] = {}

        self.rwlock = aiorwlock.RWLock()
        self.lease_lock: Optional[LeaseLock] = None

        self.s3_manager = s3_client_manager or ClientManager(s3_config)

        self.lease_lock = LeaseLock(
            export_name=export_name,
            s3_config=s3_config,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=lease_duration,
            s3_client_manager=self.s3_manager,
        )

        logger.info(
            f"S3Storage initialized: bucket={s3_config.bucket}, export={export_name}, "
            f"server={server_id}, connection={connection_id}"
        )

    @classmethod
    async def create(
        cls,
        export_name: str,
        s3_config: S3Config,
        block_size: int,
        connection_id: str,
        server_id: str,
        lease_duration: int = 30,
        s3_client_manager: Optional[ClientManager] = None,
    ) -> "S3Storage":
        """Create S3Storage instance, ensure bucket exists, and acquire lease lock."""
        instance = cls(
            export_name=export_name,
            s3_config=s3_config,
            block_size=block_size,
            connection_id=connection_id,
            server_id=server_id,
            lease_duration=lease_duration,
            s3_client_manager=s3_client_manager,
        )

        if not await instance.lease_lock.acquire():
            raise RuntimeError(
                f"Failed to acquire lease lock for export '{export_name}' "
                f"- export is already in use by another connection"
            )

        await instance._ensure_bucket_exists()

        return instance

    async def _ensure_bucket_exists(self) -> None:
        async with self.s3_manager.get_client() as s3:
            try:
                await s3.head_bucket(Bucket=self.bucket)
                logger.debug(f"Bucket '{self.bucket}' exists")
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code in ("404", "NoSuchBucket"):
                    logger.info(f"Creating bucket '{self.bucket}'")
                    await s3.create_bucket(Bucket=self.bucket)
                else:
                    logger.error(f"Error checking bucket: {e}")
                    raise

    def _get_block_key(self, block_offset: int) -> str:
        block_number = block_offset // self.block_size
        return f"blocks/{self.export_name}/{block_number:08x}"

    def _get_block_offset(self, offset: int) -> int:
        return (offset // self.block_size) * self.block_size

    async def _iter_blocks(
        self, offset: int, length: int
    ) -> AsyncGenerator[tuple[int, int, int], None]:
        """Iterate over blocks that span the given offset and length range.

        Yields tuples of (block_offset, offset_in_block, chunk_size) for each block.
        """
        # Operations may span multiple blocks, so we loop until we've processed all bytes
        bytes_processed = 0
        while bytes_processed < length:
            # Calculate the absolute byte position we're currently processing
            current_offset = offset + bytes_processed

            # Find which block contains this byte (block-aligned offset)
            block_offset = self._get_block_offset(current_offset)

            # Calculate position within the block
            offset_in_block = current_offset - block_offset

            # Determine how many bytes to process in this block:
            # - Either the remaining bytes we need to process (length - bytes_processed)
            # - Or the remaining bytes in this block (block_size - offset_in_block)
            # Whichever is smaller
            chunk_size = min(length - bytes_processed, self.block_size - offset_in_block)

            yield block_offset, offset_in_block, chunk_size
            bytes_processed += chunk_size

    async def read(self, offset: int, length: int) -> bytes:
        """Read data from storage with shared lock for concurrent reads."""
        async with self.rwlock.reader:
            blocks_to_read = []
            async for block_offset, offset_in_block, chunk_size in self._iter_blocks(offset, length):
                blocks_to_read.append((block_offset, offset_in_block, chunk_size))

            tasks = [self._read_block(block_offset) for block_offset, _, _ in blocks_to_read]
            block_data_list = await asyncio.gather(*tasks)

            result = bytearray()
            for (_, offset_in_block, chunk_size), block_data in zip(blocks_to_read, block_data_list):
                result.extend(block_data[offset_in_block : offset_in_block + chunk_size])

            return bytes(result)

    async def _read_block(self, block_offset: int) -> bytes:
        """Read a block, checking dirty_blocks first for read-your-writes consistency."""
        if block_offset in self.dirty_blocks:
            logger.debug(f"Read block from dirty_blocks: block_offset={block_offset}")
            return self.dirty_blocks[block_offset]

        key = self._get_block_key(block_offset)
        async with self.s3_manager.get_client() as s3:
            try:
                response = await s3.get_object(Bucket=self.bucket, Key=key)
                async with response["Body"] as stream:
                    data = await stream.read()
                logger.debug(f"Read block from S3: {key} ({len(data)} bytes)")
                return data
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "NoSuchKey":
                    logger.debug(f"Block not found in S3, returning zeros: {key}")
                    return b"\x00" * self.block_size
                else:
                    logger.error(f"Error reading from S3: {e}")
                    raise

    async def write(self, offset: int, data: bytes) -> None:
        """Write data to storage with exclusive lock for atomic read-modify-write."""
        async with self.rwlock.writer:
            bytes_written = 0

            async for block_offset, offset_in_block, chunk_size in self._iter_blocks(offset, len(data)):
                block_data = bytearray(await self._read_block(block_offset))
                block_data[offset_in_block : offset_in_block + chunk_size] = data[
                    bytes_written : bytes_written + chunk_size
                ]
                self.dirty_blocks[block_offset] = bytes(block_data)
                bytes_written += chunk_size

            logger.debug(
                f"Buffered write: offset={offset}, length={len(data)}, dirty_blocks={len(self.dirty_blocks)}"
            )

    async def flush(self) -> None:
        """Flush dirty blocks to S3 with exclusive lock."""
        async with self.rwlock.writer:
            if not self.dirty_blocks:
                logger.debug("No dirty blocks to flush")
                return

            num_blocks = len(self.dirty_blocks)
            logger.info(f"Flushing {num_blocks} dirty blocks to S3")

            async def upload_block(s3, block_offset: int, block_data: bytes) -> tuple[int, str, Exception | None]:
                key = self._get_block_key(block_offset)
                try:
                    await s3.put_object(
                        Bucket=self.bucket,
                        Key=key,
                        Body=block_data,
                    )
                    logger.debug(f"Uploaded block to S3: {key} ({len(block_data)} bytes)")
                    return block_offset, key, None
                except ClientError as e:
                    logger.error(f"Failed to upload block {key}: {e}")
                    return block_offset, key, e

            async with self.s3_manager.get_client() as s3:
                tasks = [
                    upload_block(s3, block_offset, block_data)
                    for block_offset, block_data in self.dirty_blocks.items()
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                failed_blocks = []
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Unexpected error during flush: {result}")
                        failed_blocks.append(("unknown", result))
                    else:
                        block_offset, key, error = result
                        if error is None:
                            del self.dirty_blocks[block_offset]
                        else:
                            failed_blocks.append((key, error))

                if failed_blocks:
                    error_summary = ", ".join([f"{key}: {err}" for key, err in failed_blocks])
                    raise RuntimeError(
                        f"Failed to flush {len(failed_blocks)} out of {num_blocks} blocks: {error_summary}"
                    )

            logger.info(f"Successfully flushed {num_blocks} blocks to S3")

    async def release(self) -> None:
        """Release the lease lock and cleanup resources."""
        if self.lease_lock:
            await self.lease_lock.release()
            logger.info(f"Released resources for export '{self.export_name}'")
