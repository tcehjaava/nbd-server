# Phase 1: Minimal Working NBD Server (Detailed Sub-Phases)

## Philosophy: Socket → Handshake → Options → Commands
Start with TCP echo server, then add NBD protocol layer by layer. Test each layer before moving to the next.

---

## **Phase 1.1: TCP Server + Basic Handshake**
**Goal**: Accept TCP connections and send NBD handshake magic bytes

**Deliverables**:
- `server.py` with TCP socket server (socket.socket, bind, listen, accept)
- Send handshake magic: `NBDMAGIC` (0x4e42444d41474943) + `IHAVEOPT` (0x49484156454F5054)
- Send fixed newstyle flags (NBD_FLAG_FIXED_NEWSTYLE = 0x01)
- Hardcoded: port 10809, log to stdout (print statements)
- Close connection after handshake

**Test**:
```bash
python server.py &
# Use netcat or telnet to connect
nc localhost 10809 | xxd -l 32  # Should see NBDMAGIC bytes
```

**Why first**: Prove we can accept connections and send bytes correctly
**What to skip**: Option parsing, storage, proper logging, multiple clients

---

## **Phase 1.2: Parse Client Options**
**Goal**: Read and parse client option requests (NBD_OPT_GO)

**Deliverables**:
- Parse client handshake flags (after connecting)
- Parse option structure: `option_magic` (IHAVEOPT), `option`, `length`, `data`
- Handle `NBD_OPT_GO` (0x00000007): parse export name from data
- Send dummy reply: `NBD_REP_ACK` (0x1) - just acknowledge
- Print parsed export name to stdout
- Close connection after option

**Test**:
```bash
python server.py &
# nbd-client will fail, but server should print parsed export name
sudo nbd-client localhost 10809 -N test_device  # Will fail, but check server logs
```

**Why second**: Parse client messages before we can respond properly
**What to skip**: Proper option replies, export info, transmission phase

---

## **Phase 1.3: Complete Option Negotiation**
**Goal**: Send proper NBD_REP_INFO and transition to transmission phase

**Deliverables**:
- Send `NBD_REP_INFO` with export info:
  - `NBD_INFO_EXPORT` (0): size=1GB, transmission_flags=0x0003
- Send `NBD_REP_ACK` to confirm NBD_OPT_GO
- Implement `NBD_OPT_ABORT` (0x00000002): close connection gracefully
- Keep connection open after successful negotiation
- Add struct.pack/unpack for all protocol messages

**Test**:
```bash
python server.py &
sudo nbd-client localhost 10809 -N test_device
# Should succeed and show: "Negotiation: ..size = 1024MB"
# nbd-client will wait for commands (server doesn't handle them yet)
# Use Ctrl+C to kill
```

**Why third**: Complete handshake so nbd-client connects successfully
**What to skip**: Actual command handling, storage

---

## **Phase 1.4: Handle READ Commands**
**Goal**: Respond to NBD_CMD_READ with zero bytes

**Deliverables**:
- Parse transmission phase commands: `magic`, `flags`, `type`, `handle`, `offset`, `length`
- Implement `NBD_CMD_READ` (0): return `length` zero bytes
- Send reply: `magic` (0x67446698), `error` (0), `handle`, followed by data
- Implement `NBD_CMD_DISC` (2): close connection gracefully
- Simple dict storage: `{offset: bytes}` (empty for now, just return zeros)

**Test**:
```bash
python server.py &
sudo nbd-client localhost 10809 -N test_device
# Should connect successfully
sudo dd if=/dev/nbd3 bs=4096 count=1 | xxd
# Should read 4096 zero bytes
sudo nbd-client -d /dev/nbd3
```

**Why fourth**: READs are simpler than WRITEs (no data to receive), test response flow
**What to skip**: Real storage, WRITE commands, FLUSH

---

## **Phase 1.5: Handle WRITE Commands**
**Goal**: Store written data and return it on reads

**Deliverables**:
- Implement `NBD_CMD_WRITE` (1):
  - After parsing command, read `length` bytes from socket
  - Store in dict: `storage[offset:offset+length] = data`
- Update `NBD_CMD_READ`:
  - If offset in storage, return stored data
  - Otherwise return zeros
- Send simple reply: `magic`, `error` (0), `handle`

**Test**:
```bash
python server.py &
sudo nbd-client localhost 10809 -N test_device

# Write test data
echo "hello world" | sudo dd of=/dev/nbd3 bs=12 count=1

# Read it back
sudo dd if=/dev/nbd3 bs=12 count=1
# Should see "hello world"

sudo nbd-client -d /dev/nbd3
```

**Why fifth**: Now we have read+write, can test data persistence in memory
**What to skip**: FLUSH, proper offset handling, block alignment

---

## **Phase 1.6: Add FLUSH + InMemoryStorage Class**
**Goal**: Complete command set and refactor storage

**Deliverables**:
- Implement `NBD_CMD_FLUSH` (3): no-op for in-memory (just send success reply)
- Extract storage to `InMemoryStorage` class:
  - `read(offset, length) -> bytes`
  - `write(offset, data)`
  - `flush()` (no-op)
- Use InMemoryStorage in command handlers
- Handle partial reads/writes (data spans multiple offsets)

**Test**:
```bash
python server.py &
sudo nbd-client localhost 10809 -N test_device
# Format filesystem (will trigger many reads/writes/flushes)
sudo mkfs.ext4 /dev/nbd3
# Should succeed without errors
sudo nbd-client -d /dev/nbd3
```

**Why sixth**: Now we have all commands, can format real filesystem
**What to skip**: Production logging, error handling, CLI args

---

## **Phase 1.7: Production Logging + CLI Args**
**Goal**: Add structured logging and configurability

**Deliverables**:
- Replace print statements with `logging` module:
  - DEBUG: All protocol messages (bytes sent/received, offsets, lengths)
  - INFO: Connection lifecycle, option results
  - WARNING: Protocol violations, unexpected situations
  - ERROR: Connection errors, exceptions
- Add CLI arguments with `argparse`:
  - `--port` (default: 10809)
  - `--size` (default: 1GB, parse MB/GB units)
  - `--log-level` (default: INFO)
- Pretty log format: `[%(levelname)s] %(message)s`

**Test**:
```bash
# INFO level (production)
python server.py --log-level INFO

# DEBUG level (development)
python server.py --log-level DEBUG --size 512MB --port 9000
sudo nbd-client localhost 9000 -N test
```

**Why seventh**: Polish working code with production features
**What to skip**: Complex log formatting, log files, config files

---

## **Phase 1.8: Testing + Documentation**
**Goal**: Manual testing and write README

**Deliverables**:
- Test complete workflow:
  1. Start server
  2. Connect nbd-client
  3. Format ext4
  4. Mount filesystem
  5. Create files/directories
  6. Read/write files
  7. Unmount
  8. Disconnect
  9. Verify server logs
- `README.md` with:
  - Overview of NBD server
  - Requirements (Python version, nbd-client, Linux/macOS+Docker)
  - Usage instructions
  - Testing steps (above workflow)
  - Known limitations (in-memory, single client)
- Add error handling:
  - Socket errors
  - Struct unpack errors (invalid protocol)
  - Connection drops

**Test**: Follow README instructions from scratch

**Why last**: Validate the complete system and document it

---

## Key Principles

### ✅ Layer by Layer
- Each sub-phase adds one protocol layer
- Test each layer before moving to next
- Build confidence incrementally

### ✅ Working Code at Every Step
- Phase 1.1: Can connect with netcat
- Phase 1.2: Can parse export name
- Phase 1.3: nbd-client connects successfully
- Phase 1.4: Can read zeros
- Phase 1.5: Can write and read back
- Phase 1.6: Can format filesystem
- Phase 1.7: Production-ready
- Phase 1.8: Fully documented

### ✅ Defer Abstractions
- Phases 1.1-1.5: Functions in single file
- Phase 1.6: Extract storage class (now we know what it needs)
- Phase 1.7: Add logging infrastructure (now we know what to log)

### ✅ Simple Tests
- Each phase has a one-liner test command
- Tests build on previous tests
- Final test is real filesystem operations

## Timeline Estimate

| Sub-Phase | Time | Cumulative | Test |
|-----------|------|------------|------|
| 1.1: TCP + Handshake | 30 min | ✓ netcat | See magic bytes |
| 1.2: Parse options | 45 min | ✓ nbd-client attempt | Server prints export name |
| 1.3: Option reply | 1 hour | ✓ nbd-client connects | Connection stays open |
| 1.4: READ command | 1 hour | ✓ dd read | Read zeros |
| 1.5: WRITE command | 1 hour | ✓ dd write+read | Read back data |
| 1.6: FLUSH + class | 1 hour | ✓ mkfs.ext4 | Format succeeds |
| 1.7: Logging + CLI | 1 hour | ✓ All commands | Clean logs |
| 1.8: Test + docs | 1 hour | ✓ Full workflow | README complete |

**Total: ~7-8 hours of focused work** (one solid day)

## Success Criteria

After Phase 1 complete:
- ✅ Single `server.py` file (~400-500 lines)
- ✅ Connects with nbd-client
- ✅ Formats and mounts ext4
- ✅ File operations work
- ✅ Production logging
- ✅ Documented in README
- ✅ Ready for Phase 2 (add S3)

## Protocol Reference

### NBD Protocol Constants
```
Handshake:
  NBDMAGIC = 0x4e42444d41474943 ("NBDMAGIC")
  IHAVEOPT = 0x49484156454f5054 ("IHAVEOPT")
  NBD_FLAG_FIXED_NEWSTYLE = 0x01

Options:
  NBD_OPT_ABORT = 0x02
  NBD_OPT_GO = 0x07

Replies:
  NBD_REP_ACK = 0x01
  NBD_REP_INFO = 0x03
  NBD_INFO_EXPORT = 0x00

Commands:
  NBD_CMD_READ = 0
  NBD_CMD_WRITE = 1
  NBD_CMD_DISC = 2
  NBD_CMD_FLUSH = 3

Reply Magic:
  NBD_SIMPLE_REPLY_MAGIC = 0x67446698
```

### Message Structures

**Handshake (server → client)**:
```python
struct.pack('>QQH',
    0x4e42444d41474943,  # NBDMAGIC
    0x49484156454f5054,  # IHAVEOPT
    0x0001               # NBD_FLAG_FIXED_NEWSTYLE
)
```

**Client Flags (client → server)**:
```python
flags = struct.unpack('>I', socket.recv(4))[0]
```

**Option Request (client → server)**:
```python
magic, option, length = struct.unpack('>QII', socket.recv(16))
data = socket.recv(length)  # export name
```

**Option Reply - NBD_REP_INFO (server → client)**:
```python
# Reply header
struct.pack('>QIII',
    0x3e889045565a9,     # Reply magic
    option,              # Echo option
    NBD_REP_INFO,        # Reply type
    length               # Length of info data
)

# NBD_INFO_EXPORT data
struct.pack('>HQH',
    NBD_INFO_EXPORT,     # Info type
    export_size,         # Size in bytes
    transmission_flags   # Flags (0x0003)
)
```

**Option Reply - NBD_REP_ACK (server → client)**:
```python
struct.pack('>QIII',
    0x3e889045565a9,     # Reply magic
    option,              # Echo option
    NBD_REP_ACK,         # Reply type
    0                    # No data
)
```

**Command Request (client → server)**:
```python
magic, flags, cmd_type, handle, offset, length = struct.unpack('>IHHQQL', socket.recv(28))
# If cmd_type == NBD_CMD_WRITE, then read length bytes of data
```

**Simple Reply (server → client)**:
```python
struct.pack('>IIQ',
    0x67446698,          # Reply magic
    error,               # 0 for success
    handle               # Echo handle from request
)
# If cmd_type was NBD_CMD_READ, send length bytes of data after reply
```

## Common Pitfalls

1. **Byte Order**: NBD uses big-endian (`>` in struct format)
2. **Magic Numbers**: Double-check all magic numbers - protocol is strict
3. **Handle Echo**: Must echo the exact handle from request in reply
4. **Data After Reply**: For reads, data comes AFTER the reply structure
5. **Data Before Reply**: For writes, data comes in request, reply has no data
6. **Socket Buffering**: Use `socket.recv_exactly()` or loop to get all bytes
7. **Offset/Length**: Handle arbitrary offsets, not just block-aligned

## Helpful Debug Commands

```bash
# See raw bytes sent by server
nc localhost 10809 | xxd | head

# Monitor server with strace
strace -e trace=network python server.py

# Test specific offset read
sudo dd if=/dev/nbd3 bs=1 count=10 skip=1000 | xxd

# Test specific offset write
echo "test" | sudo dd of=/dev/nbd3 bs=4 count=1 seek=1000

# Monitor NBD traffic
sudo tcpdump -i lo port 10809 -X
```
