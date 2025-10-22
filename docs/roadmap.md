# NBD Server with S3 Backend

## Philosophy: Build → Test → Refactor → Extend
Start with the simplest working implementation, then incrementally improve. No abstractions until we have real code to abstract.

---

## **Phase 1: Minimal Working NBD Server**
**Goal**: Get a basic NBD server accepting connections and responding to read/write/flush

**Deliverables**:
- Single file (`server.py`) with hardcoded protocol handling
- Parse handshake, NBD_OPT_GO, basic request/response
- In-memory dict storage (offset → bytes)
- Synchronous socket handling (one client)
- Make it work with `nbd-client` and mount a filesystem

**Why first**: Prove we understand the protocol and can connect real clients
**What to skip**: Abstractions, multiple exports, async, perfect error handling

---

## **Phase 2: Add S3 Persistence**
**Goal**: Replace in-memory dict with S3 storage (boto3)

**Deliverables**:
- Replace dict with S3 PutObject/GetObject calls
- Simple key structure: `blocks/{export_name}/{block_offset}`
- Fixed block size (e.g., 4KB blocks)
- Proper flush implementation (ensure S3 writes complete)
- Test persistence (restart server, data survives)

**Why second**: Core requirement - S3 backend, still simple and monolithic
**What to skip**: Content-addressable storage, caching, abstractions

---

## **Phase 3: Multi-Export Support**
**Goal**: Support multiple independent virtual disks

**Deliverables**:
- Accept different export names in handshake
- Store export metadata in S3 (size, created timestamp)
- Namespace S3 keys by export name
- Handle multiple exports from one server instance

**Why third**: Core requirement, natural extension of Phase 2
**What to skip**: Don't refactor storage layer yet

---

## **Phase 4: Refactor to Async + Multi-Client**
**Goal**: Support concurrent clients with asyncio

**Deliverables**:
- Refactor socket handling to asyncio
- Use aioboto3 for S3 operations
- Support multiple concurrent client connections
- Extract protocol parsing to functions (now we know what we need)
- Basic tests for concurrent access

**Why fourth**: Now we have working code to refactor, and we know what needs to be async
**What to skip**: Perfect abstractions - just make existing code async

---

## **Phase 5: Content-Addressable Storage + COW**
**Goal**: Store blocks by hash for deduplication and instant forking

**Deliverables**:
- Change S3 key structure: `blocks/{sha256}` for data, `exports/{name}/manifest` for mappings
- Compute SHA256 of blocks, store once
- Add fork/snapshot operation (copy manifest, share blocks)
- Copy-on-write semantics (write creates new block hash)
- Simple reference counting

**Why fifth**: Major feature, but now we understand our S3 access patterns
**What to skip**: Sophisticated GC - simple ref counting is fine

---

## **Phase 6: Add Read Caching**
**Goal**: LRU cache for frequently accessed blocks

**Deliverables**:
- In-memory LRU cache (use `functools.lru_cache` or simple dict)
- Cache S3 reads
- Cache invalidation on writes
- Add metrics (hit rate, miss rate)

**Why sixth**: Performance optimization, doesn't change functionality
**What to skip**: Write-back caching (adds complexity)

---

## **Phase 7: Add Write Buffering**
**Goal**: Batch S3 writes for performance

**Deliverables**:
- Buffer writes in memory
- Flush buffer on: NBD_CMD_FLUSH, buffer full, or timeout
- Ensure flush semantics maintained
- Add write coalescing (combine adjacent writes)

**Why seventh**: Performance optimization, natural extension of caching
**What to skip**: Complex write-back policies

---

## **Phase 8: Distributed Locking**
**Goal**: Prevent concurrent writes to same export

**Deliverables**:
- S3-based lock (use PutObject with conditions or DynamoDB)
- Acquire lock on export open, hold during session
- Heartbeat mechanism to detect dead servers
- Graceful lock release on disconnect

**Why eighth**: Advanced feature, we now understand our S3 patterns well
**What to skip**: Sophisticated lease management - simple timeout is fine

---

## **Phase 9: Production Hardening**
**Goal**: Configuration, logging, error handling, IOPS limiting

**Deliverables**:
- Config file support (YAML/JSON)
- Environment variable configuration
- Structured logging with levels
- Per-connection IOPS rate limiting
- Proper error handling throughout
- Graceful shutdown

**Why ninth**: Polish existing features for production use
**What to skip**: Over-engineered config systems

---

## **Phase 10: Comprehensive Testing**
**Goal**: Test coverage, integration tests, stress tests

**Deliverables**:
- Unit tests for key functions (protocol parsing, storage operations)
- Integration tests with MinIO
- Filesystem tests (ext4 mount, file operations)
- Stress tests (many clients, large I/O, long running)
- Fuzz testing for protocol parser
- Document test procedures

**Why last**: Validate the complete system

---

## Key Principles

### ✅ More Incremental
- Start with monolithic working code, not abstractions
- Extract patterns only after we see them repeated
- Each phase extends working code, not scaffolding

### ✅ Less Over-Engineering
- No storage interface until Phase 4-5 (when we actually need it)
- No premature protocol data classes (parse bytes directly first)
- Build abstractions when refactoring, not upfront

### ✅ Faster Time to Working Software
- Phase 1 = working NBD server (~1 day)
- Phase 2 = S3 persistence (~1 day)
- Phase 3 = multi-export (~0.5 day)
- After 3 phases, core requirements are met

### ✅ Better Learning
- See the actual problems before solving them
- Understand real complexity before abstracting
- Make informed decisions based on working code

## Timeline Estimate

| Phase | Time | Cumulative |
|-------|------|------------|
| 1-3: Core (NBD + S3 + Multi-export) | ~2-3 days | Minimum viable product ✅ |
| 4: Async refactor | ~1 day | Production-ready basics |
| 5: COW | ~2 days | Advanced feature |
| 6-7: Caching | ~1-2 days | Performance |
| 8-10: Locking + Polish + Tests | ~2-3 days | Production complete |

**Total: ~8-10 days of focused work**

## Testing Per Phase
- Manual testing with `nbd-client` after each phase
- Add automated tests incrementally
- Each phase must maintain previous functionality
