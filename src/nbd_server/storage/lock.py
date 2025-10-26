import asyncio
import json
import logging
import os
import socket
import time
from enum import Enum
from typing import Optional, Dict, Any, Tuple

from botocore.exceptions import ClientError

from ..models import S3Config
from .client import ClientManager

logger = logging.getLogger(__name__)


class LockState(Enum):
    """Lock state tracking."""
    INACTIVE = "inactive"
    ACTIVE = "active"
    LOST = "lost"


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

        self._state = LockState.INACTIVE
        self._heartbeat_task: Optional[asyncio.Task] = None
        self.s3_manager = s3_client_manager or ClientManager(s3_config)

    @property
    def is_active(self) -> bool:
        """Check if lock is currently active."""
        return self._state == LockState.ACTIVE

    @is_active.setter
    def is_active(self, value: bool) -> None:
        """Set lock active state (for backward compatibility with tests)."""
        if value:
            self._state = LockState.ACTIVE
        else:
            self._state = LockState.INACTIVE

    def _get_lock_key(self) -> str:
        """Get S3 key for the lock object."""
        return f"locks/{self.export_name}/lock.json"

    def _create_lock_data(self, timestamp: Optional[float] = None) -> Dict[str, Any]:
        """Create fresh lock data for initial acquisition."""
        timestamp = timestamp or time.time()
        return {
            "server_id": self.server_id,
            "connection_id": self.connection_id,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "timestamp": timestamp,
            "expires_at": timestamp + self.lease_duration,
        }

    def _owns_lock(self, lock_data: Dict[str, Any]) -> bool:
        """Check if this instance owns the lock."""
        return (lock_data.get("server_id") == self.server_id and
                lock_data.get("connection_id") == self.connection_id)

    def _is_expired(self, lock_data: Dict[str, Any]) -> bool:
        """Check if lock has expired."""
        return time.time() > lock_data.get("expires_at", 0)

    async def _fetch_lock(self, s3) -> Optional[Tuple[Dict[str, Any], str]]:
        """Fetch current lock data and ETag, or None if doesn't exist."""
        try:
            response = await s3.get_object(Bucket=self.bucket, Key=self._get_lock_key())
            body = await response["Body"].read()
            return (json.loads(body), response["ETag"].strip('"'))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                return None
            raise

    async def _write_lock_conditional(self, s3, lock_data: Dict[str, Any], **conditions) -> bool:
        """Write lock with conditional check, return False on race condition."""
        try:
            await s3.put_object(
                Bucket=self.bucket,
                Key=self._get_lock_key(),
                Body=json.dumps(lock_data),
                **conditions,
            )
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict"):
                return False
            raise

    async def _delete_lock_conditional(self, s3, etag: str) -> bool:
        """Delete lock with ETag condition, return False on race condition."""
        try:
            await s3.delete_object(Bucket=self.bucket, Key=self._get_lock_key(), IfMatch=etag)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code in ("PreconditionFailed", "ConditionalRequestConflict", "NoSuchKey"):
                return error_code == "NoSuchKey"
            raise

    async def acquire(self) -> bool:
        """Acquire exclusive lock for the export."""
        if self._state == LockState.ACTIVE:
            return True

        async with self.s3_manager.get_client() as s3:
            lock_result = await self._fetch_lock(s3)

            # Case 1: No lock exists - create new one
            if lock_result is None:
                if await self._write_lock_conditional(s3, self._create_lock_data(), IfNoneMatch="*"):
                    logger.info(f"Acquired lock for '{self.export_name}'")
                    self._state = LockState.ACTIVE
                    self._start_heartbeat()
                    return True
                return False

            # Case 2: Lock exists - check if expired
            lock_data, etag = lock_result

            if not self._is_expired(lock_data):
                expires_in = lock_data.get("expires_at", 0) - time.time()
                logger.warning(f"Export '{self.export_name}' locked (expires in {expires_in:.1f}s)")
                return False

            # Case 3: Lock expired - take it over
            if await self._write_lock_conditional(s3, self._create_lock_data(), IfMatch=etag):
                logger.info(f"Acquired expired lock for '{self.export_name}'")
                self._state = LockState.ACTIVE
                self._start_heartbeat()
                return True

            return False

    async def _renew_lock(self) -> bool:
        """Renew the lock by updating timestamp if we still own it."""
        async with self.s3_manager.get_client() as s3:
            lock_result = await self._fetch_lock(s3)
            if not lock_result:
                return False

            lock_data, etag = lock_result
            if not self._owns_lock(lock_data):
                return False

            current_time = time.time()
            lock_data["timestamp"] = current_time
            lock_data["expires_at"] = current_time + self.lease_duration

            return await self._write_lock_conditional(s3, lock_data, IfMatch=etag)

    def _start_heartbeat(self) -> None:
        """Start the heartbeat task to periodically renew the lock."""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Periodically renew the lock to maintain ownership."""
        consecutive_failures = 0
        max_failures = 3

        try:
            while self._state == LockState.ACTIVE:
                await asyncio.sleep(self.renew_interval)
                if self._state != LockState.ACTIVE:
                    break

                try:
                    if not await self._renew_lock():
                        logger.error(f"Lost lock ownership for '{self.export_name}'")
                        self._state = LockState.LOST
                        break
                    consecutive_failures = 0
                except Exception as e:
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures:
                        logger.error(f"Lock renewal failed for '{self.export_name}': {e}")
                        self._state = LockState.LOST
                        break
                    await asyncio.sleep(min(2 ** (consecutive_failures - 1), 8))

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Heartbeat error for '{self.export_name}': {e}")
            self._state = LockState.LOST
        finally:
            if self._state == LockState.ACTIVE:
                logger.critical(f"Heartbeat terminated unexpectedly for '{self.export_name}'")
                self._state = LockState.LOST

    async def release(self) -> None:
        """Release the lock by deleting it if we still own it."""
        was_active = self._state == LockState.ACTIVE
        self._state = LockState.INACTIVE

        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if not was_active:
            return

        try:
            async with self.s3_manager.get_client() as s3:
                lock_result = await self._fetch_lock(s3)
                if not lock_result:
                    return

                lock_data, etag = lock_result
                if self._owns_lock(lock_data) and await self._delete_lock_conditional(s3, etag):
                    logger.info(f"Released lock for '{self.export_name}'")
        except ClientError as e:
            logger.error(f"Error releasing lock: {e}")

    async def __aenter__(self):
        """Async context manager entry."""
        if not await self.acquire():
            raise RuntimeError(f"Failed to acquire lock for export '{self.export_name}'")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.release()
