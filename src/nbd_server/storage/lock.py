import asyncio
import json
import logging
import os
import socket
import time
from typing import Optional

from botocore.exceptions import ClientError

from ..models import S3Config
from .client import ClientManager

logger = logging.getLogger(__name__)


class LeaseLock:
    """Distributed lease-based lock using S3 conditional writes for exclusive access to exports."""

    def __init__(
        self,
        export_name: str,
        s3_config: S3Config,
        connection_id: str,
        server_id: str,
        lease_duration: int = 30,
        s3_client_manager: Optional[ClientManager] = None,
    ):
        self.export_name = export_name
        self.bucket = s3_config.bucket
        self.connection_id = connection_id
        self.server_id = server_id
        self.lease_duration = lease_duration
        self.renew_interval = lease_duration / 2
        self.is_active = False
        self._heartbeat_task: Optional[asyncio.Task] = None

        self.s3_manager = s3_client_manager or ClientManager(s3_config)

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
        """Acquire exclusive lock for the export, returns True if successful."""
        if self.is_active:
            logger.debug(f"Lock already active for '{self.export_name}'")
            return True

        lock_key = self._get_lock_key()

        async with self.s3_manager.get_client() as s3:
            try:
                response = await s3.get_object(Bucket=self.bucket, Key=lock_key)
                body = await response["Body"].read()
                existing_lock = json.loads(body)

                if time.time() > existing_lock.get("expires_at", 0):
                    old_etag = response["ETag"].strip('"')
                    await self._put_lock(s3, lock_key, IfMatch=old_etag)
                    logger.info(f"Acquired expired lock for '{self.export_name}'")
                    self.is_active = True
                    self._start_heartbeat()
                    return True

                expires_in = existing_lock.get("expires_at", 0) - time.time()
                logger.warning(f"Export '{self.export_name}' locked (expires in {expires_in:.1f}s)")
                return False

            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                    await self._put_lock(s3, lock_key, IfNoneMatch="*")
                    logger.info(f"Acquired lock for '{self.export_name}'")
                    self.is_active = True
                    self._start_heartbeat()
                    return True
                logger.error(f"Error checking lock: {e}")
                raise

    async def _put_lock(self, s3, lock_key: str, **conditions) -> None:
        """Write lock data to S3 with optional conditional checks."""
        lock_data = self._create_lock_data()

        try:
            await s3.put_object(
                Bucket=self.bucket,
                Key=lock_key,
                Body=json.dumps(lock_data),
                **conditions,
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code in ("PreconditionFailed", "ConditionalRequestConflict"):
                logger.error(f"Race condition detected for '{self.export_name}': {error_code}")
                raise
            logger.error(f"Error writing lock: {e}")
            raise

    def _start_heartbeat(self) -> None:
        """Start the heartbeat task to periodically renew the lock."""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Periodically renew the lock to maintain ownership with automatic retry on transient errors."""
        lock_key = self._get_lock_key()
        consecutive_failures = 0
        max_failures = 3

        logger.info(f"Started heartbeat for '{self.export_name}' (interval={self.renew_interval}s)")

        try:
            while self.is_active:
                await asyncio.sleep(self.renew_interval)
                if not self.is_active:
                    break

                try:
                    async with self.s3_manager.get_client() as s3:
                        await self._put_lock(s3, lock_key)
                    consecutive_failures = 0
                except Exception as e:
                    consecutive_failures += 1
                    backoff = min(2 ** (consecutive_failures - 1), 8)
                    logger.warning(
                        f"Lock renewal failed (attempt {consecutive_failures}/{max_failures}): {e}. "
                        f"Retrying in {backoff}s"
                    )

                    if consecutive_failures >= max_failures:
                        logger.error(f"Max failures reached for '{self.export_name}', terminating heartbeat")
                        break

                    await asyncio.sleep(backoff)

        except asyncio.CancelledError:
            logger.info(f"Heartbeat cancelled for '{self.export_name}'")
        except Exception as e:
            logger.exception(f"Heartbeat error for '{self.export_name}': {e}")
        finally:
            if self.is_active:
                logger.critical(f"Heartbeat terminated unexpectedly for '{self.export_name}', lock no longer held")
                self.is_active = False

    async def release(self) -> None:
        """Release the lock."""
        self.is_active = False

        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        try:
            async with self.s3_manager.get_client() as s3:
                await s3.delete_object(Bucket=self.bucket, Key=self._get_lock_key())
            logger.info(f"Released lock for '{self.export_name}'")
        except ClientError as e:
            logger.error(f"Error releasing lock: {e}")

    async def __aenter__(self):
        if not await self.acquire():
            raise RuntimeError(
                f"Failed to acquire lock for export '{self.export_name}'"
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()
