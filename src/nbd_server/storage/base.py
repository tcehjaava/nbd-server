from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    async def read(self, offset: int, length: int) -> bytes:
        """Read data from storage at offset, returns zero-filled bytes for unwritten regions."""
        pass

    @abstractmethod
    async def write(self, offset: int, data: bytes) -> None:
        """Write data to storage at offset."""
        pass

    @abstractmethod
    async def flush(self) -> None:
        """Flush any pending writes to ensure data persistence."""
        pass
