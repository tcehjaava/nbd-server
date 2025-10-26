# NBD Server with S3 Backend

## Philosophy: Build → Test → Refactor → Extend
Start with the simplest working implementation, then incrementally improve. No abstractions until we have real code to abstract.

---

## **Phase 1: Minimal Working NBD Server** ✅ COMPLETE
**Goal**: Get a basic NBD server accepting connections and responding to read/write/flush

**Completed**:
- ✅ NBD protocol implementation (handshake, NBD_OPT_GO, NBD_OPT_ABORT)
- ✅ Command handlers (READ, WRITE, FLUSH, DISC)
- ✅ Synchronous socket handling
- ✅ Works with `nbd-client` and filesystem mount
- ✅ Comprehensive test coverage

**Status**: Fully implemented and tested

---

## **Phase 2: Add S3 Persistence** ✅ COMPLETE
**Goal**: Replace in-memory dict with S3 storage (boto3)

**Completed**:
- ✅ S3Storage backend with boto3
- ✅ Block-based storage (128KB blocks)
- ✅ Key structure: `blocks/{export_name}/{block_offset}`
- ✅ Proper flush semantics (dirty block buffering)
- ✅ Data persists across server restarts
- ✅ Integration with MinIO
- ✅ Unit and integration tests

**Status**: Fully implemented and tested

---

## **Phase 3+4: Asyncio + Multi-Export Support** 🚧 IN PROGRESS
**Goal**: Support concurrent clients and multiple independent virtual disks using asyncio

**Rationale**: Combining Phases 3 and 4 because:
- Multi-export requires concurrent connection handling
- Asyncio naturally handles per-connection local storage
- More efficient to implement together than sequentially
- Avoids intermediate refactoring

**Deliverables**:
- Refactor server to asyncio (asyncio.start_server)
- Refactor storage to aioboto3 (async S3 operations)
- Support arbitrary export names dynamically
- Each connection creates storage for requested export
- Multiple concurrent clients with different exports
- S3 namespace isolation per export
- Comprehensive async tests

**See detailed plan**: `docs/asyncio-multi-export-plan.md`

**Status**: Planning complete, implementation in progress

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

### ✅ Self-Documenting Code
- No comments in code - code is readable by itself
- Use clear variable names, function names, and structure
- Let the code speak for itself

## Timeline Estimate

| Phase | Time | Status |
|-------|------|--------|
| 1: NBD Protocol | ~1 day | ✅ Complete |
| 2: S3 Persistence | ~1 day | ✅ Complete |
| 3+4: Asyncio + Multi-export | ~10-11 hours | 🚧 In Progress |
| 5: COW | ~2 days | ⏳ Future |
| 6-7: Caching | ~1-2 days | ⏳ Future |
| 8-10: Locking + Polish + Tests | ~2-3 days | ⏳ Future |

**Current Status**: Core requirements (Phases 1-2) complete. Working on asyncio + multi-export.

**After Phase 3+4**: All 5 core requirements will be complete, production-ready server.

## Testing Per Phase
- Manual testing with `nbd-client` after each phase
- Add automated tests incrementally
- Each phase must maintain previous functionality
