import asyncio
import json
import logging
import os
import socket
import time
import uuid
from typing import Optional

import aioboto3
from botocore.exceptions import ClientError

from .models import S3Config

logger = logging.getLogger(__name__)


class S3LeaseLock:
    """Distributed lease-based lock using S3 conditional writes.

    Provides exclusive access to an export across multiple server instances
    and connections using S3 as the coordination layer.
    """

    def __init__(
        self,
        export_name: str,
        s3_config: S3Config,
        connection_id: str,
        server_id: str,
        lease_duration: int = 30,
    ):
        self.export_name = export_name
        self.bucket = s3_config.bucket
        self.connection_id = connection_id
        self.server_id = server_id
        self.lease_duration = lease_duration
        self.is_active = False
        self._heartbeat_task: Optional[asyncio.Task] = None

        self.session = aioboto3.Session()
        self.s3_client_config = {
            "endpoint_url": s3_config.endpoint_url,
            "aws_access_key_id": s3_config.access_key,
            "aws_secret_access_key": s3_config.secret_key,
            "region_name": s3_config.region,
        }

    def _get_lock_key(self) -> str:
        return f"locks/{self.export_name}/lock.json"

    def _create_lock_data(self) -> dict:
        return {
            "server_id": self.server_id,
            "connection_id": self.connection_id,
            "timestamp": time.time(),
            "expires_at": time.time() + self.lease_duration,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
        }

    async def acquire(self) -> bool:
        """Acquire exclusive lock for the export.

        Returns:
            True if lock acquired successfully, False otherwise
        """
        lock_key = self._get_lock_key()

        async with self.session.client("s3", **self.s3_client_config) as s3:
            try:
                response = await s3.get_object(Bucket=self.bucket, Key=lock_key)
                body = await response["Body"].read()
                existing_lock = json.loads(body)

                if (
                    existing_lock.get("connection_id") == self.connection_id
                    and existing_lock.get("server_id") == self.server_id
                ):
                    logger.info(
                        f"Reacquiring our own lock for '{self.export_name}' "
                        f"(connection={self.connection_id})"
                    )
                    await self._renew_lock(s3, lock_key)
                    self.is_active = True
                    self._start_heartbeat()
                    return True

                if time.time() > existing_lock.get("expires_at", 0):
                    logger.warning(
                        f"Lock expired for '{self.export_name}' "
                        f"(previous holder: server={existing_lock.get('server_id')}, "
                        f"connection={existing_lock.get('connection_id')}), acquiring..."
                    )
                    old_etag = response["ETag"].strip('"')
                    await self._steal_lock(s3, lock_key, old_etag)
                    self.is_active = True
                    self._start_heartbeat()
                    return True
                else:
                    logger.error(
                        f"Export '{self.export_name}' is locked by "
                        f"server={existing_lock.get('server_id')}, "
                        f"connection={existing_lock.get('connection_id')} "
                        f"(expires in {existing_lock.get('expires_at') - time.time():.1f}s)"
                    )
                    return False

            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "NoSuchKey":
                    logger.info(f"No existing lock for '{self.export_name}', creating new lock")
                    await self._create_lock(s3, lock_key)
                    self.is_active = True
                    self._start_heartbeat()
                    return True
                else:
                    logger.error(f"Error checking lock: {e}")
                    raise

    async def _create_lock(self, s3, lock_key: str) -> None:
        """Create a new lock using conditional write (If-None-Match)."""
        lock_data = self._create_lock_data()

        try:
            await s3.put_object(
                Bucket=self.bucket,
                Key=lock_key,
                Body=json.dumps(lock_data),
                IfNoneMatch="*",
            )
            logger.info(
                f"Acquired lock for '{self.export_name}' "
                f"(server={self.server_id}, connection={self.connection_id})"
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "PreconditionFailed":
                logger.error(
                    f"Failed to create lock for '{self.export_name}' - "
                    f"race condition detected"
                )
                raise
            else:
                logger.error(f"Error creating lock: {e}")
                raise

    async def _steal_lock(self, s3, lock_key: str, old_etag: str) -> None:
        """Steal an expired lock using conditional write (If-Match)."""
        lock_data = self._create_lock_data()

        try:
            await s3.put_object(
                Bucket=self.bucket,
                Key=lock_key,
                Body=json.dumps(lock_data),
                IfMatch=old_etag,
            )
            logger.info(
                f"Stole expired lock for '{self.export_name}' "
                f"(server={self.server_id}, connection={self.connection_id})"
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "PreconditionFailed":
                logger.error(
                    f"Failed to steal lock for '{self.export_name}' - "
                    f"race condition detected"
                )
                raise
            else:
                logger.error(f"Error stealing lock: {e}")
                raise

    async def _renew_lock(self, s3, lock_key: str) -> None:
        """Renew the lock by updating its expiry time."""
        lock_data = self._create_lock_data()

        await s3.put_object(
            Bucket=self.bucket,
            Key=lock_key,
            Body=json.dumps(lock_data),
        )
        logger.debug(f"Renewed lock for '{self.export_name}'")

    def _start_heartbeat(self) -> None:
        """Start the heartbeat task to periodically renew the lock."""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Periodically renew the lock to maintain ownership."""
        lock_key = self._get_lock_key()
        renew_interval = self.lease_duration / 2

        logger.info(
            f"Started heartbeat for '{self.export_name}' "
            f"(interval={renew_interval}s, lease={self.lease_duration}s)"
        )

        try:
            while self.is_active:
                await asyncio.sleep(renew_interval)

                if not self.is_active:
                    break

                try:
                    async with self.session.client("s3", **self.s3_client_config) as s3:
                        await self._renew_lock(s3, lock_key)
                except Exception as e:
                    logger.error(f"Failed to renew lock for '{self.export_name}': {e}")
                    break

        except asyncio.CancelledError:
            logger.info(f"Heartbeat cancelled for '{self.export_name}'")
        except Exception as e:
            logger.exception(f"Heartbeat error for '{self.export_name}': {e}")

    async def release(self) -> None:
        """Release the lock."""
        self.is_active = False

        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        lock_key = self._get_lock_key()

        try:
            async with self.session.client("s3", **self.s3_client_config) as s3:
                await s3.delete_object(Bucket=self.bucket, Key=lock_key)
            logger.info(
                f"Released lock for '{self.export_name}' "
                f"(server={self.server_id}, connection={self.connection_id})"
            )
        except ClientError as e:
            logger.error(f"Error releasing lock for '{self.export_name}': {e}")

    async def __aenter__(self):
        if not await self.acquire():
            raise RuntimeError(
                f"Failed to acquire lock for export '{self.export_name}'"
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()
