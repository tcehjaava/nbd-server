# NBD Server with S3 Backend

Network Block Device (NBD) server implementation with S3-compatible storage backend.

## Features

- **NBD Protocol**: Fixed newstyle handshake with full command support (READ, WRITE, FLUSH, DISC)
- **S3 Storage**: Async block storage backend supporting MinIO, AWS S3, and S3-compatible services
- **Block-Based Architecture**: 128KB blocks with sparse storage (only non-zero blocks stored in S3)
- **Multi-Connection**: Async server handling multiple simultaneous clients with per-export isolation
- **Write Buffering**: In-memory dirty block caching with explicit flush semantics for performance
- **Parallel I/O**: Concurrent async read/write operations with S3 connection pooling
- **Distributed Locking**: S3-based lease locks with automatic heartbeat renewal for export exclusivity
- **Network Reliability**: TCP keepalive probes with graceful connection error handling and recovery

## Quick Start

```bash
make install-local  # Install dependencies and start MinIO
make run            # Run server
```

Run `make help` for all available commands.

## Configuration

Configure via CLI arguments or environment variables with `NBD_` prefix:

```bash
python main.py --help  # View all options
```

For local development:
```bash
cp .env.example .env  # Customize settings
```

## License

MIT License
