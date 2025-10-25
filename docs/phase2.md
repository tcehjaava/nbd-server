# Phase 2: S3 Persistence Implementation

## Overview
Replace `InMemoryStorage` with `S3Storage` that persists all block data to MinIO (S3-compatible storage). The implementation will be **synchronous** (using boto3) to keep it simple and aligned with the current architecture.

---

## Architecture Design

### Block-Based Storage
- **Block Size**: 4KB (4096 bytes) - standard filesystem block size
- **Storage Strategy**:
  - Split the virtual disk into fixed 4KB blocks
  - Each block is stored as a separate S3 object
  - Only store non-zero blocks (sparse storage for efficiency)

### S3 Key Structure
```
nbd-storage/
  └── blocks/
      └── {export_name}/
          ├── 0000000000000000  (block at offset 0)
          ├── 0000000000001000  (block at offset 4096)
          ├── 0000000000002000  (block at offset 8192)
          └── ...
```
- Key format: `blocks/{export_name}/{offset:016x}` (offset in hex, zero-padded to 16 chars)
- Example: `blocks/my_device/0000000000001000` = block starting at byte 4096

### MinIO Configuration
```python
S3_ENDPOINT = "http://localhost:9000"
S3_ACCESS_KEY = "minioadmin"
S3_SECRET_KEY = "minioadmin"
S3_BUCKET = "nbd-storage"
S3_REGION = "us-east-1"
BLOCK_SIZE = 4096
```

---

## Implementation Steps

### Step 1: Add S3 Configuration (constants.py)
**File**: `src/nbd_server/constants.py`

Add new constants:
```python
# S3 Storage Configuration
DEFAULT_S3_ENDPOINT = "http://localhost:9000"
DEFAULT_S3_ACCESS_KEY = "minioadmin"
DEFAULT_S3_SECRET_KEY = "minioadmin"
DEFAULT_S3_BUCKET = "nbd-storage"
DEFAULT_S3_REGION = "us-east-1"
BLOCK_SIZE = 4096  # 4KB blocks
```

---

### Step 2: Implement S3Storage Class (storage.py)
**File**: `src/nbd_server/storage.py`

Add new `S3Storage` class:
```python
import boto3
from botocore.exceptions import ClientError

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
        block_size: int = 4096
    ):
        # Initialize boto3 S3 client
        # Store export_name for key prefix
        # Store block_size
        # Create bucket if it doesn't exist

    def read(self, offset: int, length: int) -> bytes:
        # Calculate which blocks are needed
        # For each block:
        #   - Try to fetch from S3
        #   - If not found, return zeros
        # Combine blocks and extract the exact byte range

    def write(self, offset: int, data: bytes) -> None:
        # Buffer writes in memory (self._dirty_blocks)
        # Split data into blocks
        # Store in dirty blocks dict
        # Actual S3 writes happen on flush()

    def flush(self) -> None:
        # Upload all dirty blocks to S3
        # Use PutObject for each block
        # Clear dirty blocks dict after successful upload

    def _get_block_key(self, block_offset: int) -> str:
        # Return "blocks/{export_name}/{offset:016x}"
```

**Key Design Decisions**:
- **Write buffering**: Buffer writes in memory, flush to S3 on `flush()` command
- **Sparse storage**: Only store blocks that contain non-zero data
- **Read strategy**: Fetch only the blocks needed for the read range
- **Error handling**: Return zeros for missing blocks (sparse file semantics)

---

### Step 3: Update main.py with CLI Arguments
**File**: `main.py`

Replace hardcoded values with `argparse`:
```python
import argparse
from src.nbd_server import NBDServer, InMemoryStorage, S3Storage

def main():
    parser = argparse.ArgumentParser(description="NBD Server")

    # Storage backend selection
    parser.add_argument(
        "--storage",
        choices=["memory", "s3"],
        default="memory",
        help="Storage backend (default: memory)"
    )

    # S3 configuration (only used if --storage=s3)
    parser.add_argument("--s3-endpoint", default="http://localhost:9000")
    parser.add_argument("--s3-access-key", default="minioadmin")
    parser.add_argument("--s3-secret-key", default="minioadmin")
    parser.add_argument("--s3-bucket", default="nbd-storage")
    parser.add_argument("--s3-region", default="us-east-1")
    parser.add_argument("--export-name", default="default")

    # Server configuration
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=10809)
    parser.add_argument("--size", default="1GB", help="Export size (e.g., 512MB, 1GB)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")

    args = parser.parse_args()

    # Parse size (handle MB/GB suffixes)
    # Create storage backend based on --storage flag
    # Create and run server
```

---

### Step 4: Unit Tests for S3Storage
**File**: `tests/test_s3_storage.py` (NEW)

Use `moto` library to mock S3 operations:
```python
import pytest
from moto import mock_aws
import boto3

@mock_aws
def test_s3_storage_write_and_read():
    # Create mock S3 bucket
    # Create S3Storage instance
    # Write data
    # Flush
    # Read back data
    # Assert data matches

@mock_aws
def test_s3_storage_sparse_reads():
    # Test reading unwritten blocks returns zeros

@mock_aws
def test_s3_storage_cross_block_write():
    # Test write that spans multiple 4KB blocks

@mock_aws
def test_s3_storage_flush_semantics():
    # Verify data only persists after flush()
```

---

### Step 5: Integration Tests with MinIO
**File**: `tests/test_s3_integration.py` (NEW)

Real MinIO integration tests:
```python
import pytest
import boto3

@pytest.fixture
def minio_client():
    # Connect to real MinIO instance
    # Assumes MinIO is running via docker-compose

def test_s3_storage_with_minio(minio_client):
    # Test full read/write/flush cycle with real MinIO

def test_persistence_across_restarts(minio_client):
    # Write data, flush, create new S3Storage instance
    # Verify data persists
```

**Note**: These tests require MinIO to be running (`make docker-up`)

---

### Step 6: Update README Documentation
**File**: `README.md`

Add new section:
```markdown
## Storage Backends

### In-Memory Storage (Default)
python main.py --storage memory

### S3 Storage (MinIO)
# Start MinIO first
make docker-up

# Run with S3 backend
python main.py \
  --storage s3 \
  --export-name my_device \
  --s3-endpoint http://localhost:9000 \
  --s3-access-key minioadmin \
  --s3-secret-key minioadmin

# Test persistence
sudo nbd-client localhost 10809 -N my_device
sudo mkfs.ext4 /dev/nbd0
sudo mount /dev/nbd0 /mnt/nbd
echo "test" | sudo tee /mnt/nbd/file.txt
sudo umount /mnt/nbd
sudo nbd-client -d /dev/nbd0

# Restart server and verify data persists
python main.py --storage s3 --export-name my_device
sudo nbd-client localhost 10809 -N my_device
sudo mount /dev/nbd0 /mnt/nbd
cat /mnt/nbd/file.txt  # Should show "test"
```

---

## Testing Strategy

### Manual Testing Checklist
1. ✅ Start MinIO: `make docker-up`
2. ✅ Run server with S3: `python main.py --storage s3 --export-name test_device`
3. ✅ Connect nbd-client: `sudo nbd-client localhost 10809 -N test_device`
4. ✅ Format: `sudo mkfs.ext4 /dev/nbd0`
5. ✅ Mount: `sudo mount /dev/nbd0 /mnt/nbd`
6. ✅ Write files: Create files, directories
7. ✅ Verify MinIO: Check MinIO console (http://localhost:9001) - should see blocks
8. ✅ Disconnect: `sudo nbd-client -d /dev/nbd0`
9. ✅ Restart server with same export name
10. ✅ Reconnect and verify data persists

### Automated Testing
```bash
make test          # Run unit tests (with moto)
make test-cov      # Run with coverage report
make test-integration  # Run integration tests (requires MinIO)
```

---

## Expected Files Changed/Created

### Modified Files
- `src/nbd_server/constants.py` - Add S3 config constants
- `src/nbd_server/storage.py` - Add S3Storage class (~100 lines)
- `src/nbd_server/__init__.py` - Export S3Storage
- `main.py` - Add CLI arguments (~50 lines)
- `README.md` - Document S3 usage

### New Files
- `tests/test_s3_storage.py` - Unit tests with moto
- `tests/test_s3_integration.py` - Integration tests with MinIO

---

## Success Criteria

After implementation, we should be able to:
1. ✅ Start server with S3 backend
2. ✅ Mount an NBD device and format it
3. ✅ Write files and verify they appear in MinIO
4. ✅ Restart server and verify data persists
5. ✅ All tests pass (unit + integration)
6. ✅ Code coverage remains > 80%

---

## Timeline Estimate
- **Step 1-2**: S3Storage implementation - 2-3 hours
- **Step 3**: CLI arguments - 1 hour
- **Step 4-5**: Tests - 2 hours
- **Step 6**: Documentation - 30 minutes
- **Testing & Debugging**: 1-2 hours

**Total**: ~6-8 hours (1 full workday)

---

## Open Questions for Discussion

1. **Block Size**: Is 4KB acceptable? (Standard filesystem block size, good balance between S3 API calls and memory)
2. **Export Names**: Should we support multiple exports from one server instance in this phase, or single export per server?
3. **Write Buffering**: Should writes be buffered until flush(), or immediate? (Buffering is more efficient but adds complexity)
4. **Error Handling**: How should we handle S3 connectivity errors? Retry logic?
5. **CLI Defaults**: Are the MinIO defaults (localhost:9000, minioadmin/minioadmin) acceptable?

---

## Key Principles

### ✅ Keep It Synchronous
- Use boto3 (not aioboto3) to match current server architecture
- Defer async refactoring to Phase 4
- One client at a time is acceptable for now

### ✅ Simple Block Storage
- Fixed 4KB blocks
- No compression or deduplication yet (that's Phase 5 with COW)
- Straightforward key naming scheme

### ✅ Sparse Storage
- Only store non-zero blocks in S3
- Return zeros for missing blocks on read
- Reduces S3 storage costs and API calls

### ✅ Flush Semantics
- Buffer writes in memory
- Only persist to S3 on explicit flush() call
- This matches NBD protocol requirements

---

## Next Steps After Phase 2

Once S3 persistence is working:
- **Phase 3**: Multi-export support (multiple independent virtual disks)
- **Phase 4**: Async refactor with aioboto3 for concurrent clients
- **Phase 5**: Content-addressable storage for Copy-on-Write (COW)
