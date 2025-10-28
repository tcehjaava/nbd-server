import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import aioboto3
from botocore.client import BaseClient
from botocore.config import Config

from ..models import S3Config

logger = logging.getLogger(__name__)


class ClientManager:
    """Centralized client manager with shared connection pooling for S3 operations.

    This manager maintains a single long-lived S3 client that is shared across
    all connections, enabling true connection pool reuse and reducing overhead.
    """

    def __init__(self, s3_config: S3Config) -> None:
        self.s3_config = s3_config
        self.session = aioboto3.Session()
        self._client: Optional[BaseClient] = None
        self._client_context = None

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

    async def _ensure_client(self) -> None:
        """Ensure the shared S3 client is initialized."""
        if self._client is None:
            self._client_context = self.session.client("s3", **self._client_config)
            self._client = await self._client_context.__aenter__()
            logger.debug(
                f"Shared S3 client created with connection pool size: "
                f"{self.s3_config.max_pool_connections}"
            )

    @asynccontextmanager
    async def get_client(self) -> AsyncGenerator[BaseClient, None]:
        """Get the shared S3 client.

        This returns the same long-lived client instance for all callers,
        enabling true connection pool sharing across all operations.
        """
        await self._ensure_client()
        yield self._client

    async def close(self) -> None:
        """Cleanup the shared S3 client and resources."""
        if self._client is not None and self._client_context is not None:
            try:
                await self._client_context.__aexit__(None, None, None)
                logger.debug("Shared S3 client closed")
            except Exception as e:
                logger.warning(f"Error closing S3 client: {e}")
            finally:
                self._client = None
                self._client_context = None

    async def __aenter__(self):
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
