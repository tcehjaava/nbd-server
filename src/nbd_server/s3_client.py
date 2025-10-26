import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import aioboto3
from botocore.client import BaseClient
from botocore.config import Config

from .models import S3Config

logger = logging.getLogger(__name__)


class S3ClientManager:
    """Centralized S3 client manager with connection pooling and reuse.

    Provides a shared aioboto3 session and client management across
    multiple components (storage, lease lock, etc.) to avoid duplicate
    session creation and improve resource utilization.
    """

    def __init__(self, s3_config: S3Config) -> None:
        self.s3_config = s3_config
        self.session = aioboto3.Session()

        self._boto_config = Config(
            retries={
                'max_attempts': 5,
                'mode': 'adaptive'
            },
            connect_timeout=5,
            read_timeout=60
        )

        self._client_config = {
            "endpoint_url": s3_config.endpoint_url,
            "aws_access_key_id": s3_config.access_key,
            "aws_secret_access_key": s3_config.secret_key,
            "region_name": s3_config.region,
            "config": self._boto_config,
        }
        logger.debug(
            f"S3ClientManager initialized: endpoint={s3_config.endpoint_url}, "
            f"region={s3_config.region}, bucket={s3_config.bucket}, "
            f"retry=adaptive(5), timeout=5s/60s"
        )

    @asynccontextmanager
    async def get_client(self) -> AsyncGenerator[BaseClient, None]:
        """Get an S3 client as an async context manager.

        Yields:
            BaseClient: An aioboto3 S3 client instance

        Usage:
            async with manager.get_client() as s3:
                await s3.get_object(Bucket=bucket, Key=key)
        """
        async with self.session.client("s3", **self._client_config) as client:
            yield client

    @asynccontextmanager
    async def get_resource(self) -> AsyncGenerator:
        """Get an S3 resource as an async context manager.

        Yields:
            S3 resource instance for higher-level operations

        Usage:
            async with manager.get_resource() as s3:
                bucket = await s3.Bucket('my-bucket')
        """
        async with self.session.resource("s3", **self._client_config) as resource:
            yield resource

    async def close(self) -> None:
        """Cleanup resources if needed.

        aioboto3 sessions don't require explicit cleanup, but this method
        provides a hook for future resource management needs.
        """
        logger.debug("S3ClientManager closed")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
