import logging
from abc import ABC, abstractmethod
from typing import Generator

import boto3
from botocore.exceptions import ClientError

from .constants import BLOCK_SIZE

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    def read(self, offset: int, length: int) -> bytes:
        """Read data from storage at offset, returns zero-filled bytes for unwritten regions."""
        pass

    @abstractmethod
    def write(self, offset: int, data: bytes) -> None:
        """Write data to storage at offset."""
        pass

    @abstractmethod
    def flush(self) -> None:
        """Flush any pending writes to ensure data persistence."""
        pass


class InMemoryStorage(StorageBackend):
    """In-memory storage backend using a sparse dictionary."""

    def __init__(self) -> None:
        self.data: dict[int, bytes] = {}

    def read(self, offset: int, length: int) -> bytes:
        result = b""
        for i in range(length):
            byte_offset = offset + i
            if byte_offset in self.data:
                result += self.data[byte_offset]
            else:
                result += b"\x00"
        return result

    def write(self, offset: int, data: bytes) -> None:
        for i in range(len(data)):
            self.data[offset + i] = data[i : i + 1]

    def flush(self) -> None:
        """No-op for in-memory storage as all writes are immediately persisted in memory."""
        pass


class S3Storage(StorageBackend):
    """S3-backed storage using fixed-size blocks."""

    def __init__(
        self,
        export_name: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
        block_size: int = BLOCK_SIZE,
    ) -> None:
        self.export_name = export_name
        self.bucket = bucket
        self.block_size = block_size
        self.dirty_blocks: dict[int, bytes] = {}

        self.s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

        logger.info(f"S3Storage initialized: bucket={bucket}, export={export_name}")

    @classmethod
    def create(
        cls,
        export_name: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
        block_size: int = BLOCK_SIZE,
    ) -> "S3Storage":
        """Create S3Storage instance and ensure bucket exists."""
        instance = cls(
            export_name=export_name,
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            region=region,
            block_size=block_size,
        )
        instance._ensure_bucket_exists()
        return instance

    def _ensure_bucket_exists(self) -> None:
        try:
            self.s3_client.head_bucket(Bucket=self.bucket)
            logger.debug(f"Bucket '{self.bucket}' exists")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchBucket"):
                logger.info(f"Creating bucket '{self.bucket}'")
                self.s3_client.create_bucket(Bucket=self.bucket)
            else:
                logger.error(f"Error checking bucket: {e}")
                raise

    def _get_block_key(self, block_offset: int) -> str:
        block_number = block_offset // self.block_size
        return f"blocks/{self.export_name}/{block_number:08x}"

    def _get_block_offset(self, offset: int) -> int:
        return (offset // self.block_size) * self.block_size

    def _iter_blocks(self, offset: int, length: int) -> Generator[tuple[int, int, int], None, None]:
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

    def read(self, offset: int, length: int) -> bytes:
        result = bytearray()

        for block_offset, offset_in_block, chunk_size in self._iter_blocks(offset, length):
            block_data = self._read_block(block_offset)
            result.extend(block_data[offset_in_block : offset_in_block + chunk_size])

        return bytes(result)

    def _read_block(self, block_offset: int) -> bytes:
        """Read a block, checking dirty_blocks first for read-your-writes consistency."""
        if block_offset in self.dirty_blocks:
            logger.debug(f"Read block from dirty_blocks: block_offset={block_offset}")
            return self.dirty_blocks[block_offset]

        key = self._get_block_key(block_offset)
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            data = response["Body"].read()
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

    def write(self, offset: int, data: bytes) -> None:
        bytes_written = 0

        for block_offset, offset_in_block, chunk_size in self._iter_blocks(offset, len(data)):
            block_data = bytearray(self._read_block(block_offset))
            block_data[offset_in_block : offset_in_block + chunk_size] = data[
                bytes_written : bytes_written + chunk_size
            ]
            self.dirty_blocks[block_offset] = bytes(block_data)
            bytes_written += chunk_size

        logger.debug(
            f"Buffered write: offset={offset}, length={len(data)}, dirty_blocks={len(self.dirty_blocks)}"
        )

    def flush(self) -> None:
        if not self.dirty_blocks:
            logger.debug("No dirty blocks to flush")
            return

        num_blocks = len(self.dirty_blocks)
        logger.info(f"Flushing {num_blocks} dirty blocks to S3")

        for block_offset in list(self.dirty_blocks.keys()):
            block_data = self.dirty_blocks[block_offset]
            key = self._get_block_key(block_offset)

            try:
                self.s3_client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=block_data,
                )
                logger.debug(f"Uploaded block to S3: {key} ({len(block_data)} bytes)")
                del self.dirty_blocks[block_offset]

            except ClientError as e:
                logger.error(f"Failed to upload block {key}: {e}")
                raise

        logger.info(f"Successfully flushed {num_blocks} blocks to S3")
