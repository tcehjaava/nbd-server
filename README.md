# NBD Server with S3 Backend

Network Block Device (NBD) server implementation with S3-compatible storage backend.

## Key Design Decisions

- **NBD Protocol Implementation**: Implemented the fixed newstyle handshake which supports arbitrary export names. Each export name gets its own independent S3 storage namespace, allowing multiple isolated block devices on the same server.

- **128KB Block Size Choice**: Selected 128KB blocks because S3 latency is nearly identical for small (8KB) vs larger (128KB) objects - both take 10-60ms. This reduces API calls by 16x when writing 1MB of data (8 S3 PUTs instead of 128), significantly improving throughput and reducing costs.

- **Write Buffering Strategy**: WRITE commands buffer data to an in-memory cache (`dirty_blocks`) and return immediately for low latency. FLUSH commands act as a durability barrier - they upload all buffered dirty blocks to S3 and only return after the data is confirmed durable. This is similar to how fsync() works in filesystems.

- **Distributed Locking with S3**: Implemented lease-based locks stored in S3 to prevent multiple connections from writing to the same export simultaneously. Each lock has a 30-second lease duration with automatic 15-second renewal via background heartbeat tasks to maintain ownership.

- **Lock Acquisition with Conditional Writes**: Uses S3's conditional write operations (`IfNoneMatch` for creation, `IfMatch` for updates) combined with S3's strong consistency model. This guarantees that when two clients try to acquire the same export lock simultaneously, only one will succeed.

- **Concurrent Read Operations**: Implemented read-write locks (RWLock) at the storage layer, allowing multiple READ operations to execute concurrently while WRITE operations get exclusive access. This enables atomic read-modify-write sequences without data races.

- **Parallel S3 Uploads**: During FLUSH operations, dirty blocks are uploaded to S3 in parallel using asyncio.gather with a pool of 10 concurrent connections. This provides approximately 128MB/s theoretical throughput (10 connections × 128KB blocks × 100 requests/sec).

- **Network Failure Detection**: Configured TCP keepalive probes (60 seconds idle, 10 second intervals, 6 probe attempts) to detect dead connections. Combined with the 30-second lease expiration, disconnected clients release their export locks within approximately 120 seconds without requiring manual intervention.

- **Sparse Storage Optimization**: Only stores non-zero blocks in S3, with missing blocks returning zeros on read. This makes large sparse files extremely efficient - for example, a 10GB empty file consumes 0 bytes of S3 storage.

- **Asynchronous Architecture**: Built on Python asyncio for non-blocking I/O, allowing the server to handle multiple client connections simultaneously without threading overhead. Each client connection gets its own isolated storage instance.

- **Read-Your-Writes Consistency**: Before fetching blocks from S3, the read path first checks the in-memory dirty blocks cache. This ensures that data written in the current session is immediately visible on subsequent reads, even before FLUSH is called.

- **S3 Resilience and Retry Logic**: Configured boto3 with adaptive retry mode (up to 5 attempts with exponential backoff) and appropriate timeouts (5 seconds for connection, 60 seconds for reads). This handles transient S3 failures gracefully without surfacing errors to clients.

- **Graceful Error Handling**: All connection handling code uses try-finally blocks to ensure locks are always released and resources cleaned up, even when exceptions occur. This prevents resource leaks and ensures exports don't get stuck in locked state after client crashes.

## Potential Enhancements

- **Copy-on-Write (CoW)**: Store metadata per export with parent reference. On fork, create new export pointing to parent (instant operation). On read, check export's blocks first, then fall back to parent chain. On write, copy block from parent if exists, then write to current export. Enables constant-time forking with shared storage for unchanged blocks.

- **Read Caching Layer**: Add in-memory LRU cache for frequently accessed blocks. On read miss, fetch from S3 and populate cache. On flush, move dirty blocks to cache.

- **Block-Level Locking with Concurrent Commands**: Track which blocks are locked by in-flight commands. Execute non-overlapping commands in parallel within a single client connection.

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
