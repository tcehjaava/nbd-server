# NBD Server Demo Guide (macOS)

## Prerequisites

- Docker Desktop running
- MinIO already running (port 9000/9001)

## Step 1: Start the NBD Server

Terminal 1:
```bash
make run
```

Server starts on `localhost:10809`.

## Step 2: Start Linux Container

Terminal 2:
```bash
docker run -it --rm --privileged --network host ubuntu:22.04 bash
```

## Step 3: Install NBD Tools

In the container:
```bash
apt-get update
apt-get install -y nbd-client
```

## Step 4: Connect to NBD Server

```bash
nbd-client host.docker.internal 10809 -N demo_device
```

Output shows: `Connected /dev/nbd0`

## Step 5: Write Block Data

Write data directly to the block device with flush:
```bash
# Write at the beginning (conv=fsync triggers NBD_CMD_FLUSH)
echo "Hello from NBD server!" | dd of=/dev/nbd0 bs=512 count=1 conv=fsync

# Write random data at offset (oflag=sync triggers NBD_CMD_FLUSH)
dd if=/dev/urandom of=/dev/nbd0 bs=1M count=10 seek=100 oflag=sync

# Read back the first block
dd if=/dev/nbd0 bs=512 count=1 | cat
```

## Step 6: Disconnect

```bash
nbd-client -d /dev/nbd0
exit
```

## Step 7: Verify Persistence in S3

Check MinIO: http://localhost:9001 (login: minioadmin/minioadmin)

Navigate to `nbd-storage` bucket → `blocks/demo_device/` to see stored blocks.

## Step 8: Reconnect and Verify Data

Start new container:
```bash
docker run -it --rm --privileged --network host ubuntu:22.04 bash
```

In container:
```bash
apt-get update && apt-get install -y nbd-client

# Connect to same export
nbd-client host.docker.internal 10809 -N demo_device

# Read the same data back
dd if=/dev/nbd0 bs=512 count=1 | cat
```

Data persists! ✓ This proves read/write/flush operations work with S3 backend.

## Demo Multiple Exports

Each export is independent:
```bash
# Export 1
nbd-client host.docker.internal 10809 -N export1
echo "Export 1 data" | dd of=/dev/nbd0 bs=512 count=1 conv=fsync

# Export 2 (in another terminal/container)
nbd-client host.docker.internal 10809 -N export2
echo "Export 2 data" | dd of=/dev/nbd1 bs=512 count=1 conv=fsync
```

Check MinIO - each export has its own S3 prefix under `blocks/`.

## Cleanup

```bash
# In container
nbd-client -d /dev/nbd0
exit

# On Mac
make stop
```
