# Replit Take Home: Cloud Block Device

## Overview

At Replit we provide on-demand sandboxes for AI coding agents to build web apps for our users. The sandbox is a Linux environment with a persisted workspace volume. The volume is backed by an in-house Network Block Device (NBD) server which stores the filesystem contents in S3 compatible storage. This allows repls to have an arbitrarily large filesystem that is lazily loaded from cheap storage and can be forked in constant time.

You will be tasked with implementing an NBD server which persists the contents of the storage device to S3 compatible storage. The NBD protocol is available [here](https://github.com/NetworkBlockDevice/nbd/blob/master/doc/proto.md). You may use NBD libraries like libnbd to implement the server or you can implement the protocol yourself as its pretty simple. You only need to support the NBD handshake, read, write, and flush.

## What is a Network Block Device?

A network block device is a virtual storage device, conceptually its just a bunch of bytes which can be read/written at specified offsets and has a way to flush writes to non-volatile storage.

- **read** takes in an offset and length and should return the contents of the block device
- **write** takes in an offset and an array of bytes and should write the bytes to the block device
- **flush** should ensure that any completed writes before the flush are persisted to non-volatile storage

After implementing, you will submit your solution for us and we will schedule a call to go over the solution and spend some time extending the solution. Please come prepared to demonstrate the server's functionality and to give a walk through of the code.

## Requirements

1. Server must support the NBD protocol read, write, and flush operations.
2. Server only needs to implement fixed newstyle handshake and NBD_OPT_GO / NBD_OPT_ABORT
3. Server must support arbitrary export names, which are all persisted independently of each other.
4. Server must durably persist all data to S3 compatible storage.
5. Server must properly implement flush semantics: all completed writes prior to the flush operation should be persisted to S3 storage.

## Additional Functionality (Optional)

You are encouraged to add additional functionality beyond the base requirements, here's a list of ideas for inspiration, but you can also come up with your own additional functionality:

- Support forking/duplicating disks
- Prevent multiple servers from writing to the same disk in S3 at the same time
- Add tiered caching support to amortize the cost of S3 reads & writes
- Support concurrent read/write operations
- Add IOPS limits per connection
- Fuzz testing

## Notes

- You are welcome, and encouraged, to use AI assisted tooling, but make sure you understand the code and can explain the architecture & implementation.
- For testing you can use mocked S3 storage based on the filesystem or run a local S3 compatible storage server like minio.
- If you're on macOS you can use a privileged docker container for testing out the NBD server.

## Testing Your NBD Server

A Network Block Device is a virtual block storage device that operates over a TCP connection. The Linux kernel ships with a built in NBD client. You can use the `nbd-client` CLI to tell the kernel to connect to your NBD server. The `nbd-client` command will tell you the path to the block device, which will be of the form `/dev/nbdXX`. You can then format the device using your favorite filesystem and mount it for testing.

To test persistence, unmount the filesystem, disconnect from NBD, restart your server and then connect to the same export, mount the filesystem, and make sure the contents of the disk are still there.

### Example commands:

```bash
$ sudo nbd-client localhost 10809 -N test_device
Negotiation: ..size = 1024MB
Connected /dev/nbd3

$ sudo mkfs.ext4 /dev/nbd3
mke2fs 1.47.2 (1-Jan-2025)
Creating filesystem with 262144 4k blocks and 65536 inodes
Filesystem UUID: 73890a37-128a-4c64-aa15-e6a18ee695ef
Superblock backups stored on blocks:
        32768, 98304, 163840, 229376

Allocating group tables: done
Writing inode tables: done
Creating journal (8192 blocks): done
Writing superblocks and filesystem accounting information: done

$ sudo mount /dev/nbd3 /mnt

$ # test out filesystem by reading & writing files to /mnt

...

# Cleanup
$ sudo umount /mnt

$ sudo nbd-client -d /dev/nbd3
```
