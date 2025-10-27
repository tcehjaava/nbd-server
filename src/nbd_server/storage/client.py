import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import aioboto3
from botocore.client import BaseClient
from botocore.config import Config

from ..models import S3Config

logger = logging.getLogger(__name__)


class ClientManager:
    """Centralized client manager with connection pooling and reuse for S3 operations."""

    def __init__(self, s3_config: S3Config) -> None:
        self.s3_config = s3_config
        self.session = aioboto3.Session()

        self._boto_config = Config(
            retries={
                'max_attempts': 5,
                'mode': 'adaptive'
            },
            connect_timeout=5,
            read_timeout=60,
            max_pool_connections=s3_config.max_pool_connections
        )

        self._client_config = {
            "endpoint_url": s3_config.endpoint_url,
            "aws_access_key_id": s3_config.access_key,
            "aws_secret_access_key": s3_config.secret_key,
            "region_name": s3_config.region,
            "config": self._boto_config,
        }
        logger.debug(
            f"ClientManager initialized: endpoint={s3_config.endpoint_url}, "
            f"region={s3_config.region}, bucket={s3_config.bucket}, "
            f"max_pool={s3_config.max_pool_connections}, "
            f"retry=adaptive(5), timeout=5s/60s"
        )

    @asynccontextmanager
    async def get_client(self) -> AsyncGenerator[BaseClient, None]:
        """Get an S3 client as an async context manager."""
        async with self.session.client("s3", **self._client_config) as client:
            yield client

    async def close(self) -> None:
        """Cleanup resources if needed."""
        logger.debug("ClientManager closed")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
