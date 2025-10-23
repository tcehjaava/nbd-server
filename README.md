# NBD Server with S3 Backend

Network Block Device (NBD) server implementation with S3-compatible storage backend and Copy-on-Write (COW) support.

## Prerequisites

- Python 3.13+
- Docker and Docker Compose (for MinIO)
- Linux system with NBD kernel module (or Docker container on macOS)

## Installation

1. Clone the repository and navigate to the project directory:
```bash
cd nbd-server
```

2. Create a virtual environment and install dependencies:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
make install-dev
```

3. Start MinIO (S3-compatible storage):
```bash
make docker-up
```

MinIO web console will be available at http://localhost:9001 (user: `minioadmin`, pass: `minioadmin`)

## Running the Server

```bash
make run
```

The NBD server will start on port `10809` by default.

## Testing

### Run tests with coverage:
```bash
make test-cov
```

### Manual testing with nbd-client:

1. Connect to the NBD server:
```bash
sudo nbd-client localhost 10809 -N my_device
```

2. Format the device:
```bash
sudo mkfs.ext4 /dev/nbd0  # Use the device shown by nbd-client
```

3. Mount the filesystem:
```bash
sudo mkdir -p /mnt/nbd
sudo mount /dev/nbd0 /mnt/nbd
```

4. Test read/write:
```bash
echo "Hello NBD!" | sudo tee /mnt/nbd/test.txt
cat /mnt/nbd/test.txt
```

5. Cleanup:
```bash
sudo umount /mnt/nbd
sudo nbd-client -d /dev/nbd0
```

## Development

### Format code:
```bash
make format
```

### Lint code:
```bash
make lint
```

### Type checking:
```bash
make type-check
```

### Clean build artifacts:
```bash
make clean
```

## Architecture

This implementation features:
- **Copy-on-Write (COW)**: Instant disk forking with content-addressable storage
- **S3 Backend**: Durable persistence using S3-compatible storage
- **Async I/O**: Concurrent operations support using asyncio
- **Tiered Caching**: In-memory LRU cache with write-back buffering
- **Distributed Locking**: Prevent multiple servers from writing to same disk

See `docs/requirements.md` for the full project requirements.

## License

MIT License - see LICENSE file for details.
