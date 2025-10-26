import asyncio
import logging
from functools import wraps
from typing import Any, Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_timeout(seconds: float):
    """Decorator to add timeout to async functions.

    Args:
        seconds: Timeout in seconds

    Raises:
        asyncio.TimeoutError: If function execution exceeds timeout

    Usage:
        @with_timeout(5.0)
        async def slow_operation():
            await asyncio.sleep(10)  # Will raise TimeoutError
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)

        return wrapper

    return decorator


async def with_retry(
    coro: Awaitable[T],
    max_attempts: int = 3,
    backoff_base: float = 1.0,
    backoff_multiplier: float = 2.0,
    exceptions: tuple = (Exception,),
) -> T:
    """Execute async coroutine with exponential backoff retry.

    Args:
        coro: Coroutine to execute
        max_attempts: Maximum number of retry attempts
        backoff_base: Initial backoff time in seconds
        backoff_multiplier: Multiplier for exponential backoff
        exceptions: Tuple of exceptions to catch and retry

    Returns:
        Result from successful execution

    Raises:
        Last exception if all retries fail

    Usage:
        result = await with_retry(
            some_async_func(),
            max_attempts=5,
            exceptions=(ClientError,)
        )
    """
    last_exception: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await coro
        except exceptions as e:
            last_exception = e
            if attempt == max_attempts:
                logger.error(f"All {max_attempts} retry attempts failed: {e}")
                raise

            backoff = backoff_base * (backoff_multiplier ** (attempt - 1))
            logger.warning(
                f"Attempt {attempt}/{max_attempts} failed: {e}. " f"Retrying in {backoff:.1f}s..."
            )
            await asyncio.sleep(backoff)

    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected retry loop exit")


class BackgroundTaskManager:
    """Manager for background async tasks with graceful shutdown.

    Provides lifecycle management for background tasks, ensuring proper
    cancellation and cleanup during shutdown.
    """

    def __init__(self):
        self.tasks: list[asyncio.Task] = []
        self._shutdown = False

    def create_task(
        self, coro: Awaitable, name: Optional[str] = None, log_exceptions: bool = True
    ) -> asyncio.Task:
        """Create and track a background task.

        Args:
            coro: Coroutine to run as background task
            name: Optional task name for debugging
            log_exceptions: Whether to log exceptions from task

        Returns:
            Created asyncio.Task
        """
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)

        if log_exceptions:

            def log_task_exception(t: asyncio.Task) -> None:
                try:
                    t.result()
                except asyncio.CancelledError:
                    logger.debug(f"Task {name or t.get_name()} cancelled")
                except Exception as e:
                    logger.exception(f"Background task {name or t.get_name()} failed: {e}")

            task.add_done_callback(log_task_exception)

        return task

    async def shutdown(self, timeout: Optional[float] = 5.0) -> None:
        """Gracefully shutdown all background tasks.

        Args:
            timeout: Maximum time to wait for tasks to complete (None = wait forever)
        """
        if self._shutdown:
            return

        self._shutdown = True
        logger.info(f"Shutting down {len(self.tasks)} background tasks...")

        for task in self.tasks:
            if not task.done():
                task.cancel()

        if timeout is not None:
            try:
                await asyncio.wait_for(asyncio.gather(*self.tasks, return_exceptions=True), timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Some tasks did not complete within {timeout}s timeout")
        else:
            await asyncio.gather(*self.tasks, return_exceptions=True)

        self.tasks.clear()
        logger.info("Background task shutdown complete")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()


async def run_with_heartbeat(
    task_coro: Awaitable[T],
    heartbeat_coro: Awaitable[None],
    heartbeat_interval: float,
) -> T:
    """Run a task with periodic heartbeat in the background.

    Args:
        task_coro: Main task coroutine to execute
        heartbeat_coro: Heartbeat coroutine to run periodically
        heartbeat_interval: Interval between heartbeats in seconds

    Returns:
        Result from main task

    Usage:
        async def heartbeat():
            await renew_lock()

        result = await run_with_heartbeat(
            long_running_task(),
            heartbeat(),
            interval=30.0
        )
    """

    async def heartbeat_loop():
        while True:
            await asyncio.sleep(heartbeat_interval)
            await heartbeat_coro

    async with asyncio.TaskGroup() as tg:
        task = tg.create_task(task_coro)
        tg.create_task(heartbeat_loop())

    return task.result()
