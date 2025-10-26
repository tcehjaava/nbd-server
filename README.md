# NBD Server with S3 Backend

Network Block Device (NBD) server implementation with S3-compatible storage backend.

## Features

- ✅ **NBD Protocol**: Complete implementation of NBD fixed newstyle handshake
- ✅ **S3 Storage Backend**: Durable block storage using S3-compatible services (MinIO, AWS S3, etc.)
- ✅ **Block-based Storage**: Efficient 128KB block size with sparse storage support
- ✅ **Write Buffering**: Dirty block caching with explicit flush semantics
- ✅ **Cloud-Native**: Configuration via CLI arguments or environment variables
- 🚧 **Concurrent Connections**: Asyncio-based architecture for multiple simultaneous clients (in progress)
- 🚧 **Multi-Export Support**: Arbitrary virtual disk names with S3 namespace isolation (in progress)

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

## Configuration

The server can be configured using **CLI arguments** or **environment variables**. CLI arguments take precedence over environment variables.

### CLI Arguments

```bash
python main.py --help
```

Available options:
- `--export-name NAME`: Export name (required if not set via NBD_EXPORT_NAME)
- `--host HOST`: Server host (default: localhost)
- `--port PORT`: Server port (default: 10809)
- `--size SIZE`: Export size, e.g., 512MB, 1GB, 2TB (default: 1GB)
- `--s3-endpoint URL`: S3 endpoint URL (default: http://localhost:9000)
- `--s3-access-key KEY`: S3 access key (default: minioadmin)
- `--s3-secret-key KEY`: S3 secret key (default: minioadmin)
- `--s3-bucket NAME`: S3 bucket name (default: nbd-storage)
- `--s3-region REGION`: S3 region (default: us-east-1)
- `--log-level {DEBUG,INFO,WARNING,ERROR}`: Logging level (default: INFO)

### Environment Variables

All configuration can be set via environment variables with `NBD_` prefix:

```bash
export NBD_EXPORT_NAME=my-disk
export NBD_HOST=0.0.0.0
export NBD_PORT=10809
export NBD_SIZE=1GB
export NBD_S3_ENDPOINT=http://localhost:9000
export NBD_S3_ACCESS_KEY=minioadmin
export NBD_S3_SECRET_KEY=minioadmin
export NBD_S3_BUCKET=nbd-storage
export NBD_S3_REGION=us-east-1
export NBD_LOG_LEVEL=INFO
```

### Local Development (.env file)

For local development, copy `.env.example` to `.env` and customize:

```bash
cp .env.example .env
# Edit .env with your configuration
```

The `.env` file is automatically loaded when running the server.

## Running the Server

### Basic Usage

```bash
# Start MinIO first
make docker-up

# Run server with export name
python main.py --export-name my-disk

# Or use make command with environment variables
export NBD_EXPORT_NAME=my-disk
make run
```

### With Custom Configuration

```bash
python main.py \
  --export-name my-disk \
  --s3-endpoint http://localhost:9000 \
  --size 2GB \
  --log-level DEBUG
```

### Using Environment Variables

```bash
# Set configuration in .env file
cp .env.example .env
# Edit .env with your settings

# Run server
python main.py
```

### Docker Deployment

```bash
docker run -d \
  -p 10809:10809 \
  -e NBD_EXPORT_NAME=my-disk \
  -e NBD_HOST=0.0.0.0 \
  -e NBD_S3_ENDPOINT=http://minio:9000 \
  -e NBD_S3_ACCESS_KEY=minioadmin \
  -e NBD_S3_SECRET_KEY=minioadmin \
  nbd-server
```

### Kubernetes Deployment

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: nbd-config
data:
  NBD_EXPORT_NAME: "my-disk"
  NBD_S3_ENDPOINT: "http://minio.storage.svc:9000"
  NBD_S3_BUCKET: "nbd-storage"
  NBD_HOST: "0.0.0.0"
  NBD_PORT: "10809"
  NBD_SIZE: "1GB"
---
apiVersion: v1
kind: Secret
metadata:
  name: nbd-s3-credentials
type: Opaque
stringData:
  NBD_S3_ACCESS_KEY: "minioadmin"
  NBD_S3_SECRET_KEY: "minioadmin"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nbd-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app: nbd-server
  template:
    metadata:
      labels:
        app: nbd-server
    spec:
      containers:
      - name: nbd-server
        image: nbd-server:latest
        ports:
        - containerPort: 10809
        envFrom:
        - configMapRef:
            name: nbd-config
        env:
        - name: NBD_S3_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: nbd-s3-credentials
              key: NBD_S3_ACCESS_KEY
        - name: NBD_S3_SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: nbd-s3-credentials
              key: NBD_S3_SECRET_KEY
```

## Testing

### Run tests with coverage:
```bash
make test-cov
```

### Manual testing with nbd-client:

1. **Connect to the NBD server:**
```bash
sudo nbd-client localhost 10809 -N my-disk
# Note the device path, e.g., /dev/nbd0
```

2. **Format the device:**
```bash
sudo mkfs.ext4 /dev/nbd0
```

3. **Mount the filesystem:**
```bash
sudo mkdir -p /mnt/nbd
sudo mount /dev/nbd0 /mnt/nbd
```

4. **Test read/write:**
```bash
echo "Hello NBD!" | sudo tee /mnt/nbd/test.txt
cat /mnt/nbd/test.txt
```

5. **Cleanup:**
```bash
sudo umount /mnt/nbd
sudo nbd-client -d /dev/nbd0
```

### Testing S3 Persistence

Test that data persists across server restarts:

```bash
# 1. Start server with S3 backend
export NBD_EXPORT_NAME=persist-test
python main.py &
SERVER_PID=$!

# 2. Connect and write data
sudo nbd-client localhost 10809 -N persist-test
sudo mkfs.ext4 /dev/nbd0
sudo mount /dev/nbd0 /mnt/nbd
echo "persistent data" | sudo tee /mnt/nbd/file.txt

# 3. Disconnect
sudo umount /mnt/nbd
sudo nbd-client -d /dev/nbd0

# 4. Stop server
kill $SERVER_PID

# 5. Restart server with same export name
python main.py &

# 6. Reconnect and verify data persists
sudo nbd-client localhost 10809 -N persist-test
sudo mount /dev/nbd0 /mnt/nbd
cat /mnt/nbd/file.txt  # Should show "persistent data"

# 7. Cleanup
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

### S3 Storage Backend

The S3Storage backend provides:
- **Block-based storage**: 128KB blocks for efficient I/O
- **Sparse storage**: Only non-zero blocks are stored in S3
- **Write buffering**: Writes are buffered in memory (dirty blocks)
- **Explicit flush**: Data is persisted to S3 only on NBD_CMD_FLUSH
- **Read-your-writes consistency**: Dirty blocks are checked before S3 reads
- **S3 compatibility**: Works with MinIO, AWS S3, and other S3-compatible services

### NBD Protocol Support

The server implements the NBD fixed newstyle handshake and supports:
- `NBD_OPT_GO`: Export negotiation
- `NBD_OPT_ABORT`: Connection abort
- `NBD_CMD_READ`: Read blocks
- `NBD_CMD_WRITE`: Write blocks
- `NBD_CMD_FLUSH`: Flush pending writes
- `NBD_CMD_DISC`: Disconnect

## Project Structure

```
nbd-server/
├── src/nbd_server/
│   ├── __init__.py
│   ├── constants.py      # NBD protocol constants
│   ├── protocol.py       # Protocol message parsing
│   ├── server.py         # NBD server implementation
│   └── storage.py        # S3 storage backend
├── tests/                # Unit tests
├── docs/                 # Documentation
├── main.py              # Entry point
├── Makefile             # Development commands
├── docker-compose.yml   # MinIO setup
└── requirements.txt     # Dependencies
```

## Roadmap

See `docs/roadmap.md` for the full development roadmap. Current status:

- ✅ Phase 1: Minimal working NBD server
- ✅ Phase 2: S3 persistence
- 🚧 Phase 3+4: Asyncio + Multi-export support (in progress)
  - See `docs/asyncio-multi-export-plan.md` for detailed implementation plan
- 🔲 Phase 5: Content-addressable storage + Copy-on-Write
- 🔲 Phase 6-7: Read caching and write buffering optimization
- 🔲 Phase 8: Distributed locking
- 🔲 Phase 9: Production hardening
- 🔲 Phase 10: Comprehensive testing

**Note**: Phases 3 and 4 are being implemented together for efficiency. Once complete, the server will support multiple concurrent clients with arbitrary export names (virtual disks).

## Requirements

See `docs/requirements.md` for the full project requirements.

## License

MIT License - see LICENSE file for details.
