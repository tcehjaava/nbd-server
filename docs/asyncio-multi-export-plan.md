# Asyncio + Multi-Export Implementation Plan

## Overview

This document outlines the implementation plan for adding concurrent connection support and multi-export functionality to the NBD server using Python's asyncio framework.

**Goals:**
1. Support multiple concurrent client connections
2. Support arbitrary export names (virtual disks) dynamically
3. Use asyncio for efficient, scalable I/O operations
4. Maintain clean, testable code architecture

**Why Asyncio?**
- Single-threaded but concurrent (event loop handles multiple connections)
- Efficient resource usage (no thread-per-connection overhead)
- Scalable (can handle 1000s of connections)
- Modern Python pattern with clean async/await syntax
- Non-blocking I/O for both sockets and S3 operations

---

## High-Level Architecture

### Current Architecture (Synchronous)

```
┌─────────────┐
│  NBDServer  │
└─────────────┘
      │
      ├─→ Accept connection (BLOCKS)
      ├─→ Handle handshake (BLOCKS on socket I/O)
      ├─→ Handle commands (BLOCKS on socket + S3 I/O)
      └─→ Close connection

      └─→ Accept next connection... (sequential, one at a time)
```

**Limitations:**
- Only one client at a time
- Wasted CPU cycles while waiting for I/O
- Can't scale to multiple concurrent connections

### New Architecture (Asyncio)

```
┌─────────────────────────┐
│  Asyncio Event Loop     │
│  (manages all I/O)      │
└─────────────────────────┘
      │
      ├─→ [Connection 1: disk1] → reading socket... (YIELDS)
      ├─→ [Connection 2: disk2] → waiting for S3... (YIELDS)
      ├─→ [Connection 3: disk1] → writing to S3... (YIELDS)
      ├─→ [Connection 4: disk3] → processing command... (YIELDS)
      └─→ When I/O ready, resume coroutine → process → yield again
```

**Benefits:**
- Multiple concurrent clients
- CPU used efficiently (no blocking)
- Each connection isolated (local storage variable)
- Scales to 1000s of connections on single thread

---

## Core Components to Refactor

| Component | Current | After Asyncio | Changes Required |
|-----------|---------|---------------|------------------|
| **server.py** | `socket.accept()` blocks | `asyncio.start_server()` | All methods → `async def`, use `await` |
| **storage.py** | `boto3` (sync) | `aioboto3` (async) | S3 client → async, all ops → `await` |
| **protocol.py** | `socket.recv()` blocks | `reader.read()` yields | `recv_exactly()` → async |
| **main.py** | `server.run()` blocks | `asyncio.run(server.run())` | Add asyncio entry point |
| **tests/** | Sync test functions | `@pytest.mark.asyncio` | Add pytest-asyncio, mark tests |

---

## Multi-Export Implementation

### Concept

Each client can request a different export name (virtual disk):

```python
# Client 1 connects
nbd-client localhost 10809 -N disk1

# Client 2 connects (concurrent!)
nbd-client localhost 10809 -N disk2

# Client 3 connects (same export as Client 1)
nbd-client localhost 10809 -N disk1
```

Each export is isolated in S3:
- `blocks/disk1/00000001` ← disk1's blocks
- `blocks/disk2/00000001` ← disk2's blocks

### How It Works with Asyncio

Each connection is a separate coroutine with local variables:

```python
async def _handle_connection(self, reader, writer):
    # This coroutine runs independently for each connection

    # Get export name from client
    export_name = await self._handle_negotiation(reader, writer)

    # Create storage for THIS connection (local variable)
    storage = await S3Storage.create_async(
        export_name=export_name,  # Could be "disk1", "disk2", etc.
        **self.s3_config
    )

    # Handle commands using this local storage
    await self._handle_transmission(reader, writer, storage)
```

**Key insight:** Each coroutine has its own local `storage` variable, so no sharing/conflict between connections.

---

## Implementation Steps

### Step 1: Add Dependencies

**Update requirements.txt:**
```txt
aioboto3>=12.0.0
types-aioboto3[s3]>=12.0.0
pytest-asyncio>=0.23.0
```

**Install:**
```bash
source venv/bin/activate
pip install aioboto3 types-aioboto3[s3] pytest-asyncio
```

**Test:** `pip list | grep aioboto3` shows installed version

---

### Step 2: Refactor protocol.py (Async Socket I/O)

**Current implementation:**
```python
def recv_exactly(sock: socket.socket, length: int) -> bytes:
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data += chunk
    return data
```

**New async implementation:**
```python
async def recv_exactly(reader: asyncio.StreamReader, length: int) -> bytes:
    """Read exactly `length` bytes from async stream reader."""
    data = await reader.readexactly(length)
    return data
```

**Changes:**
- `sock.recv()` → `reader.readexactly()` (built-in asyncio helper)
- Add `async` keyword
- Add `await` keyword
- Use `asyncio.StreamReader` instead of `socket.socket`

**Files changed:**
- `src/nbd_server/protocol.py` - update `recv_exactly()` function

**Test:** Unit test with mock StreamReader

---

### Step 3: Refactor storage.py (Async S3 Operations)

**Current S3Storage:**
```python
import boto3

class S3Storage:
    def __init__(self, ...):
        self.s3_client = boto3.client('s3', ...)

    def read(self, offset, length):
        response = self.s3_client.get_object(...)  # BLOCKS
        data = response['Body'].read()  # BLOCKS
        return data

    def flush(self):
        for block_offset, data in self.dirty_blocks.items():
            self.s3_client.put_object(...)  # BLOCKS
```

**New async S3Storage:**
```python
import aioboto3

class S3Storage:
    def __init__(self, ...):
        self.session = aioboto3.Session()
        self.s3_config = {
            'endpoint_url': endpoint_url,
            'aws_access_key_id': access_key,
            'aws_secret_access_key': secret_key,
            'region_name': region,
        }

    async def read(self, offset, length):
        async with self.session.client('s3', **self.s3_config) as s3:
            response = await s3.get_object(...)  # YIELDS
            data = await response['Body'].read()  # YIELDS
        return data

    async def flush(self):
        async with self.session.client('s3', **self.s3_config) as s3:
            for block_offset, data in self.dirty_blocks.items():
                await s3.put_object(...)  # YIELDS
```

**Key changes:**
- `boto3` → `aioboto3`
- Store session and config, create client in context manager
- All methods become `async def`
- All S3 operations use `await`
- Use `async with` for S3 client context

**Files changed:**
- `src/nbd_server/storage.py` - refactor S3Storage class

**Test:** Unit tests with moto (supports aioboto3)

---

### Step 4: Refactor server.py (Async Server Loop)

#### 4.1: Update Constructor

**Current:**
```python
def __init__(self, storage: StorageBackend, host, port, export_size):
    self.storage = storage  # Single storage instance
```

**New:**
```python
def __init__(self, s3_config: dict, host, port, export_size, block_size):
    self.s3_config = s3_config  # S3 configuration
    self.export_size = export_size
    self.block_size = block_size
    # No self.storage - created per connection
```

#### 4.2: Update Server Loop

**Current:**
```python
def run(self):
    server_socket = socket.socket(...)
    server_socket.bind((self.host, self.port))
    server_socket.listen(1)

    while True:
        client_socket, addr = server_socket.accept()  # BLOCKS
        try:
            self._handle_connection(client_socket)  # BLOCKS
        finally:
            client_socket.close()
```

**New:**
```python
async def run(self):
    """Start async NBD server and handle concurrent connections."""
    server = await asyncio.start_server(
        self._handle_connection,
        self.host,
        self.port
    )

    logger.info(f"NBD Server listening on {self.host}:{self.port}")

    async with server:
        await server.serve_forever()
```

#### 4.3: Update Connection Handler

**Current:**
```python
def _handle_connection(self, client_socket):
    # Handshake
    client_socket.sendall(Responses.handshake())

    # Negotiation
    export_name = self._handle_negotiation(client_socket)

    # Transmission
    self._handle_transmission(client_socket)
```

**New:**
```python
async def _handle_connection(self, reader, writer):
    """Handle a single async client connection."""
    try:
        # Handshake
        writer.write(Responses.handshake())
        await writer.drain()

        # Parse client flags
        flags_data = await recv_exactly(reader, 4)
        flags = Requests.client_flags(flags_data)

        # Negotiation - get export name
        export_name = await self._handle_negotiation(reader, writer)
        if export_name is None:
            return

        # Create storage for THIS connection
        storage = await S3Storage.create_async(
            export_name=export_name,
            endpoint_url=self.s3_config['endpoint'],
            access_key=self.s3_config['access_key'],
            secret_key=self.s3_config['secret_key'],
            bucket=self.s3_config['bucket'],
            region=self.s3_config['region'],
            block_size=self.block_size,
        )

        logger.info(f"Created storage for export '{export_name}'")

        # Transmission phase
        await self._handle_transmission(reader, writer, storage)

    except ConnectionError as e:
        logger.error(f"Connection error: {e}")
    except Exception as e:
        logger.exception(f"Error handling connection: {e}")
    finally:
        writer.close()
        await writer.wait_closed()
```

#### 4.4: Update Negotiation

**Current:**
```python
def _handle_negotiation(self, client_socket):
    header = recv_exactly(client_socket, 16)
    option_data = recv_exactly(client_socket, option_length)
    # ... parse ...
    client_socket.sendall(Responses.info_reply(...))
    client_socket.sendall(Responses.ack_reply(...))
    return export_name
```

**New:**
```python
async def _handle_negotiation(self, reader, writer):
    """Handle async option negotiation, returns export name."""
    header = await recv_exactly(reader, 16)
    option_length = struct.unpack(">QII", header)[2]
    option_data = await recv_exactly(reader, option_length) if option_length > 0 else b""

    option, data = Requests.option_request(header, option_data)

    if option == NBD_OPT_GO:
        export_name_length = struct.unpack(">I", data[:4])[0]
        export_name = data[4:4 + export_name_length].decode("utf-8")
        logger.info(f"Export name: '{export_name}'")

        # Send replies
        writer.write(Responses.info_reply(option, self.export_size, TRANSMISSION_FLAGS))
        writer.write(Responses.ack_reply(option))
        await writer.drain()

        return export_name

    elif option == NBD_OPT_ABORT:
        logger.info("Client requested abort")
        return None

    else:
        logger.warning(f"Unsupported option: {option}")
        return None
```

#### 4.5: Update Transmission Phase

**Current:**
```python
def _handle_transmission(self, client_socket):
    while True:
        cmd_data = recv_exactly(client_socket, 28)
        cmd_type, handle, offset, length = Requests.command(cmd_data)

        if cmd_type == NBD_CMD_READ:
            self._handle_read(client_socket, handle, offset, length)
        # ... etc ...
```

**New:**
```python
async def _handle_transmission(self, reader, writer, storage):
    """Handle async transmission phase with local storage."""
    while True:
        cmd_data = await recv_exactly(reader, 28)
        cmd_type, flags, handle, offset, length = Requests.command(cmd_data)

        if cmd_type == NBD_CMD_READ:
            await self._handle_read(writer, storage, handle, offset, length)
        elif cmd_type == NBD_CMD_WRITE:
            await self._handle_write(reader, writer, storage, handle, offset, length)
        elif cmd_type == NBD_CMD_FLUSH:
            await self._handle_flush(writer, storage, handle)
        elif cmd_type == NBD_CMD_DISC:
            logger.info("Client disconnecting")
            break
```

#### 4.6: Update Command Handlers

**Current:**
```python
def _handle_read(self, client_socket, handle, offset, length):
    data = self.storage.read(offset, length)
    client_socket.sendall(Responses.simple_reply(handle, 0))
    client_socket.sendall(data)
```

**New:**
```python
async def _handle_read(self, writer, storage, handle, offset, length):
    """Handle async NBD_CMD_READ."""
    data = await storage.read(offset, length)
    writer.write(Responses.simple_reply(handle, 0))
    writer.write(data)
    await writer.drain()

async def _handle_write(self, reader, writer, storage, handle, offset, length):
    """Handle async NBD_CMD_WRITE."""
    data = await recv_exactly(reader, length)
    await storage.write(offset, data)
    writer.write(Responses.simple_reply(handle, 0))
    await writer.drain()

async def _handle_flush(self, writer, storage, handle):
    """Handle async NBD_CMD_FLUSH."""
    await storage.flush()
    writer.write(Responses.simple_reply(handle, 0))
    await writer.drain()
```

**Files changed:**
- `src/nbd_server/server.py` - comprehensive refactor (~200 lines changed)

**Test:** Update all NBDServer tests to be async

---

### Step 5: Update main.py (Asyncio Entry Point)

**Current:**
```python
def main():
    # ... parse args ...

    storage = S3Storage.create(
        export_name=args.export_name,
        ...
    )

    server = NBDServer(storage=storage, ...)
    server.run()  # Blocks forever
```

**New:**
```python
async def async_main():
    """Async entry point for NBD server."""
    # ... parse args (same) ...

    # Build S3 config dict
    s3_config = {
        'endpoint': args.s3_endpoint,
        'access_key': args.s3_access_key,
        'secret_key': args.s3_secret_key,
        'bucket': args.s3_bucket,
        'region': args.s3_region,
    }

    # Create server with S3 config (not storage instance)
    server = NBDServer(
        s3_config=s3_config,
        host=args.host,
        port=args.port,
        export_size=export_size,
        block_size=block_size,
    )

    # Run async server
    await server.run()

def main():
    """Synchronous entry point."""
    load_dotenv()

    parser = argparse.ArgumentParser(...)
    # Remove --export-name argument (no longer needed!)
    parser.add_argument('--host', ...)
    parser.add_argument('--port', ...)
    parser.add_argument('--size', ...)
    # ... S3 args ...

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(...)

    # Run asyncio event loop
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Server stopped")

if __name__ == '__main__':
    main()
```

**Key changes:**
- Remove `--export-name` CLI argument
- Create `s3_config` dict instead of `S3Storage` instance
- Add `async_main()` coroutine
- Use `asyncio.run(async_main())` to start event loop

**Files changed:**
- `main.py` - add async_main, update CLI args

**Test:** `python main.py --help` shows no --export-name

---

### Step 6: Update Tests (pytest-asyncio)

#### 6.1: Add pytest-asyncio Configuration

**pyproject.toml:**
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

#### 6.2: Update Test Fixtures

**Current:**
```python
def test_recv_exactly():
    mock_socket = MagicMock()
    mock_socket.recv.return_value = b"data"
    result = recv_exactly(mock_socket, 4)
    assert result == b"data"
```

**New:**
```python
@pytest.mark.asyncio
async def test_recv_exactly():
    reader = asyncio.StreamReader()
    reader.feed_data(b"data")
    reader.feed_eof()

    result = await recv_exactly(reader, 4)
    assert result == b"data"
```

#### 6.3: Update Storage Tests

**New S3Storage test with aioboto3:**
```python
import pytest
from moto import mock_aws

@pytest.mark.asyncio
@mock_aws
async def test_s3_storage_read_write():
    # Setup mock S3
    session = aioboto3.Session()
    async with session.client('s3', endpoint_url='http://localhost:5000') as s3:
        await s3.create_bucket(Bucket='test-bucket')

    # Create storage
    storage = S3Storage(
        export_name='test',
        endpoint_url='http://localhost:5000',
        access_key='test',
        secret_key='test',
        bucket='test-bucket',
        region='us-east-1',
        block_size=4096,
    )

    # Test write + flush
    await storage.write(0, b"hello")
    await storage.flush()

    # Test read
    data = await storage.read(0, 5)
    assert data == b"hello"
```

#### 6.4: Update Server Tests

```python
@pytest.mark.asyncio
async def test_server_handshake():
    server = NBDServer(
        s3_config={'endpoint': '...', ...},
        host='localhost',
        port=10809,
        export_size=1024*1024*1024,
        block_size=128*1024,
    )

    # Create mock reader/writer
    reader = asyncio.StreamReader()
    writer_mock = MagicMock()

    # Test connection handler
    # ... (detailed test implementation)
```

**Files changed:**
- `tests/test_protocol.py` - add `@pytest.mark.asyncio`
- `tests/test_storage.py` - update for aioboto3
- `tests/test_server.py` - update for async server
- `tests/test_multi_export.py` - NEW FILE for integration tests

**Test:** `make test` all tests pass

---

### Step 7: Integration Testing

Create comprehensive integration test that verifies concurrent connections with different exports:

**tests/test_multi_export.py:**
```python
import pytest
import asyncio
from moto import mock_aws

@pytest.mark.asyncio
@mock_aws
async def test_concurrent_multi_export():
    """Test multiple clients connecting to different exports concurrently."""

    # Start mock S3
    # ... setup ...

    # Start NBD server
    server = NBDServer(s3_config={...}, ...)
    server_task = asyncio.create_task(server.run())

    await asyncio.sleep(0.5)  # Let server start

    try:
        # Simulate 3 concurrent connections
        client1_task = asyncio.create_task(
            simulate_nbd_client(export_name="disk1", data=b"data1")
        )
        client2_task = asyncio.create_task(
            simulate_nbd_client(export_name="disk2", data=b"data2")
        )
        client3_task = asyncio.create_task(
            simulate_nbd_client(export_name="disk1", data=b"data3")
        )

        # Wait for all clients
        await asyncio.gather(client1_task, client2_task, client3_task)

        # Verify S3 isolation
        # blocks/disk1/... should have data1 + data3
        # blocks/disk2/... should have data2
        # ... assertions ...

    finally:
        server_task.cancel()
```

**Manual testing:**
```bash
# Terminal 1: Start server
python main.py

# Terminal 2: Connect client 1
sudo nbd-client localhost 10809 -N disk1
sudo mkfs.ext4 /dev/nbd0
sudo mount /dev/nbd0 /mnt/disk1
echo "client1" | sudo tee /mnt/disk1/test.txt

# Terminal 3: Connect client 2 (concurrent!)
sudo nbd-client localhost 10809 -N disk2
sudo mkfs.ext4 /dev/nbd1
sudo mount /dev/nbd1 /mnt/disk2
echo "client2" | sudo tee /mnt/disk2/test.txt

# Verify isolation
cat /mnt/disk1/test.txt  # Should show "client1"
cat /mnt/disk2/test.txt  # Should show "client2"
```

---

## Code Examples: Before vs After

### Example 1: Server Loop

**Before (Synchronous):**
```python
def run(self):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((self.host, self.port))
    server_socket.listen(1)

    while True:
        client_socket, addr = server_socket.accept()  # BLOCKS until client
        self._handle_connection(client_socket)        # BLOCKS until done
        client_socket.close()
```

**After (Asyncio):**
```python
async def run(self):
    server = await asyncio.start_server(
        self._handle_connection,  # Callback for each connection
        self.host,
        self.port
    )

    async with server:
        await server.serve_forever()  # Event loop handles all connections
```

### Example 2: Reading from Socket

**Before:**
```python
def recv_exactly(sock, length):
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))  # BLOCKS
        data += chunk
    return data
```

**After:**
```python
async def recv_exactly(reader, length):
    data = await reader.readexactly(length)  # YIELDS, non-blocking
    return data
```

### Example 3: S3 Operations

**Before:**
```python
def read(self, offset, length):
    response = self.s3_client.get_object(
        Bucket=self.bucket,
        Key=key
    )  # BLOCKS until S3 responds
    return response['Body'].read()  # BLOCKS
```

**After:**
```python
async def read(self, offset, length):
    async with self.session.client('s3', **self.s3_config) as s3:
        response = await s3.get_object(
            Bucket=self.bucket,
            Key=key
        )  # YIELDS, non-blocking
        data = await response['Body'].read()  # YIELDS
    return data
```

### Example 4: Multi-Export with Local Storage

**Before (broken with concurrency):**
```python
def __init__(self, storage):
    self.storage = storage  # ONE storage for ALL connections

def _handle_connection(self, client_socket):
    # All connections share self.storage - BROKEN!
    data = self.storage.read(0, 100)
```

**After (correct with local variables):**
```python
async def _handle_connection(self, reader, writer):
    export_name = await self._handle_negotiation(reader, writer)

    # Each connection gets its OWN storage (local variable)
    storage = await S3Storage.create_async(
        export_name=export_name,  # "disk1", "disk2", etc.
        **self.s3_config
    )

    # Use local storage for this connection only
    await self._handle_transmission(reader, writer, storage)
```

---

## Testing Strategy

### Unit Tests
- ✅ `test_protocol.py` - async recv_exactly, message parsing
- ✅ `test_storage.py` - async S3Storage with moto
- ✅ `test_server.py` - async server components

### Integration Tests
- ✅ Single client connects, reads/writes, verifies S3 persistence
- ✅ Multiple clients with same export name (concurrent access)
- ✅ Multiple clients with different export names (isolation)
- ✅ Server restart, data persists for all exports

### Manual Tests
- ✅ Connect with nbd-client, format ext4, mount, write files
- ✅ Multiple concurrent nbd-client connections
- ✅ Verify data isolation between exports in MinIO console
- ✅ Kill server, restart, reconnect, verify data persists

### Performance Tests (optional)
- Load test with 100 concurrent connections
- Measure throughput (MB/s) vs synchronous version
- Monitor memory usage with many exports

---

## Success Criteria

After implementation, we should have:

### Core Requirements
- ✅ Server handles multiple concurrent connections
- ✅ Each connection can request different export name
- ✅ Exports are isolated in S3 (separate namespaces)
- ✅ All NBD protocol operations work (read, write, flush, disconnect)
- ✅ Data persists across server restarts

### Code Quality
- ✅ All async operations use proper async/await
- ✅ No blocking I/O operations in async code
- ✅ Clean error handling for connection/S3 errors
- ✅ Comprehensive test coverage (>80%)
- ✅ All tests pass

### Documentation
- ✅ README updated with asyncio architecture
- ✅ Multi-export usage examples
- ✅ Code includes method-level docstrings
- ✅ Clear logging for debugging

### Functionality
- ✅ Can run: `python main.py` (no --export-name needed)
- ✅ Can connect: `nbd-client localhost 10809 -N any-name-here`
- ✅ Can mount multiple exports simultaneously
- ✅ Data isolated between exports
- ✅ Performance: handles 10+ concurrent connections smoothly

---

## Common Pitfalls to Avoid

### 1. Mixing Sync and Async
❌ **Wrong:**
```python
async def my_func():
    data = storage.read(0, 100)  # sync call in async function
```

✅ **Right:**
```python
async def my_func():
    data = await storage.read(0, 100)  # await async call
```

### 2. Forgetting `await`
❌ **Wrong:**
```python
async def my_func():
    reader.readexactly(10)  # Returns coroutine object, doesn't execute!
```

✅ **Right:**
```python
async def my_func():
    data = await reader.readexactly(10)  # Actually executes
```

### 3. Blocking Calls in Async Code
❌ **Wrong:**
```python
async def my_func():
    time.sleep(1)  # BLOCKS event loop!
```

✅ **Right:**
```python
async def my_func():
    await asyncio.sleep(1)  # Yields control
```

### 4. Not Using Context Managers for S3
❌ **Wrong:**
```python
async def read(self):
    s3 = self.session.client('s3')
    response = await s3.get_object(...)  # Client not properly closed
```

✅ **Right:**
```python
async def read(self):
    async with self.session.client('s3', **config) as s3:
        response = await s3.get_object(...)  # Auto-closes
```

### 5. Shared State Between Connections
❌ **Wrong:**
```python
async def _handle_connection(self, reader, writer):
    self.current_storage = create_storage()  # Shared state!
```

✅ **Right:**
```python
async def _handle_connection(self, reader, writer):
    storage = await create_storage()  # Local variable
    await self._handle_transmission(reader, writer, storage)
```

---

## Timeline Estimate

| Step | Task | Time Estimate |
|------|------|---------------|
| 1 | Add dependencies (aioboto3, pytest-asyncio) | 15 min |
| 2 | Refactor protocol.py (recv_exactly) | 30 min |
| 3 | Refactor storage.py (aioboto3) | 2 hours |
| 4 | Refactor server.py (asyncio) | 3 hours |
| 5 | Update main.py (CLI + entry point) | 30 min |
| 6 | Update tests (pytest-asyncio) | 2 hours |
| 7 | Integration tests + manual testing | 2 hours |
| **Total** | | **~10-11 hours** |

---

## Next Steps

1. Review this plan thoroughly
2. Set up development environment (aioboto3, pytest-asyncio)
3. Start with Step 1 (dependencies)
4. Work through steps sequentially
5. Test after each step
6. Final integration testing
7. Update documentation

---

## References

- [asyncio documentation](https://docs.python.org/3/library/asyncio.html)
- [aioboto3 documentation](https://aioboto3.readthedocs.io/)
- [pytest-asyncio documentation](https://pytest-asyncio.readthedocs.io/)
- [NBD protocol specification](https://github.com/NetworkBlockDevice/nbd/blob/master/doc/proto.md)
