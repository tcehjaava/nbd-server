from abc import ABC, abstractmethod


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
