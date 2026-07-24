# cache-store - Design & Roadmap

> ⚠️ **This document describes the planned/target design, not the current state of the project.**
> The current implementation is an early Python MVP (see main [README.md](../README.md) for actual status).
> Everything below - the C++ rewrite, binary protocol, epoll event loop, arena allocator, and
> benchmark numbers - is a design target and roadmap, written before implementation began.
> Treat all performance figures here as **goals**, not measured results.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Binary Protocol Specification](#3-binary-protocol-specification)
4. [Memory Model](#4-memory-model)
5. [Project Structure](#5-project-structure)
6. [Configuration](#6-configuration)
7. [Implementation Roadmap](#7-implementation-roadmap)
8. [Known Limitations & Future Extensions](#8-known-limitations--future-extensions)

---

## 1. Overview

`cache-store` is a minimal in-memory key-value server inspired by the internal design of Redis.
The target implementation is C++ for Linux, built around four engineering principles:

**Zero-copy binary protocol** - requests and responses are fixed-format binary frames read directly into typed memory. No JSON, no HTTP, no string parsing.

**Single-threaded event loop** - one thread handles all connections through `epoll`. No mutexes, no condition variables, no thread pools.

**Arena memory model** - all keys and values live in one contiguous buffer. Heap allocations happen in bulk, not per key. CPU cache sees sequential data.

**Proactive TTL expiration** - a min-heap keeps expired keys ordered by deadline. A background sweep inside the event loop tick evicts them in O(log n) without a separate thread.

The goal is not to replace Redis. The goal is to understand why Redis is designed the way it is - by building the same engine from scratch.

---

## 2. Architecture

### 2.1 Component Diagram

```text
  ┌─────────────────────────────────────────────────────────────────┐
  │                         TCP Clients                             │
  └──────────────────┬──────────────┬──────────────┬────────────────┘
                     │              │              │
                     ▼              ▼              ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                      EventLoop  (epoll)                         │
  │                                                                 │
  │  listen_fd ──EPOLLIN──► on_accept()                             │
  │  client_fd ──EPOLLIN──► on_readable()  ──► ProtocolParser       │
  │  client_fd ──EPOLLOUT─► on_writable()  ──► drain write_buf      │
  │  timer tick           ► expire_tick()  ──► Store                │
  └────────────────────────────────┬────────────────────────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │           Store              │
                    │  set() / get() / del()       │
                    │  expire_tick()               │
                    └──────┬──────────┬────────────┘
                           │          │
              ┌────────────▼──┐  ┌────▼───────────────────┐
              │  Hash Table   │  │    TTL Min-Heap        │
              │  key_hash     │  │  priority_queue<       │
              │    → Entry    │  │    TtlNode>            │
              │  (offsets)    │  │  ordered by expire_at  │
              └────────┬──────┘  └────────────────────────┘
                       │
              ┌────────▼───────────────────────────────┐
              │             Arena Memory               │
              │                                        │
              │  [ key₀ | val₀ | key₁ | val₁ | … ]     │
              │  std::vector<char>  (one allocation)   │
              └────────────────────────────────────────┘
```

### 2.2 Request Lifecycle

```text
recv() → read_buf (per client)
           │
           ▼
    ProtocolParser::parse()
           │
     ┌─────┴───────────────────────┐
     │  INCOMPLETE                 │  wait for more bytes
     │  ERROR                      │  send ERR response, close client
     │  COMPLETE                   │  dispatch to Store
     └─────────────────────────────┘
           │
     Store::set/get/del()
           │
     ResponseBuilder::write_*()
           │
           ▼
    write_buf (per client)
           │
     send() in on_writable()
```

### 2.3 Design Decisions

| Decision                       | Rationale                                                                                                      |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| Single-threaded event loop     | Zero lock contention. One core at 100% beats many cores fighting over a mutex. Redis proves this at scale.     |
| `epoll` level-triggered        | Simpler logic than edge-triggered; we read until `EAGAIN` anyway, making ET's advantage moot at this scale.    |
| Binary protocol                | Fixed-width header → one `memcpy` into a struct. No tokeniser, no allocator, no parser state machine.          |
| Arena allocator                | Eliminates per-key `malloc`/`free`. All data is contiguous → cache prefetcher works optimally.                 |
| Min-heap for TTL               | O(log n) insert and eviction. The heap is only touched on SET and during the sweep tick, never on GET.         |
| Open-addressing hash table     | Flat array → no pointer chasing. Robin Hood probing keeps average probe length ≈ 1.5 even at 70% load.         |
| `uint64_t` key hash as map key | Comparing two 64-bit integers is faster than comparing two heap strings. Hash collision is handled explicitly. |

---

## 3. Binary Protocol Specification

All multi-byte integers are **little-endian**. There is no magic number or version byte in v1 - the command byte implicitly identifies a valid frame.

### 3.1 Minimum Frame Size

The minimum valid frame is a `GET` or `DEL` with a 1-byte key:

```text
1 (CMD) + 4 (key_len) + 1 (key) + 4 (value_len) + 0 (value) + 8 (ttl) = 18 bytes
```

Any frame shorter than 17 bytes is malformed and must be rejected.

### 3.2 Request Frame Layout

```text
Byte offset   Size    Field
───────────   ────    ─────────────────────────────────────────
0             1 B     CMD         uint8_t   command code
1             4 B     key_len     uint32_t  length of key in bytes
5             N B     key         char[]    raw bytes, not null-terminated
5+N           4 B     value_len   uint32_t  0 for GET and DEL
9+N           M B     value       char[]    raw bytes
9+N+M         8 B     ttl         uint64_t  seconds; 0 = persist forever
```

Total frame size: `1 + 4 + key_len + 4 + value_len + 8`

### 3.3 Command Codes

| Code   | Command | value_len | ttl     | Behaviour                                                                                                            |
| ------ | ------- | --------- | ------- | -------------------------------------------------------------------------------------------------------------------- |
| `0x01` | `SET`   | > 0       | any     | Upsert key. If key exists, old value is logically replaced; old arena bytes are left as dead space until compaction. |
| `0x02` | `GET`   | must be 0 | ignored | Retrieve value. Returns `EXPIRED` if TTL has passed, and deletes the key.                                            |
| `0x03` | `DEL`   | must be 0 | ignored | Delete key. Returns `OK` even if key did not exist.                                                                  |

### 3.4 Response Frame Layout

```text
Byte offset   Size    Field
───────────   ────    ─────────────────────────────────────────
0             1 B     STATUS      uint8_t   status code
1             4 B     value_len   uint32_t  0 unless STATUS = 0x00 on GET
5             N B     value       char[]    present only when value_len > 0
```

### 3.5 Status Codes

| Code   | Name        | Sent on                                                    |
| ------ | ----------- | ---------------------------------------------------------- |
| `0x00` | `OK`        | Successful SET, DEL, or GET with a value                   |
| `0x01` | `NOT_FOUND` | GET or DEL on a key that does not exist                    |
| `0x02` | `EXPIRED`   | GET on a key that existed but has passed its TTL           |
| `0x03` | `ERR`       | Malformed frame: unknown CMD, key_len = 0, frame truncated |

### 3.6 Validation Rules

The parser must reject frames where:

- `CMD` is not `0x01`, `0x02`, or `0x03`
- `key_len` is 0 (empty key is not allowed)
- `key_len` > 4096 (guard against memory exhaustion)
- `value_len` > 64 MiB (configurable cap)
- `value_len` > 0 on a `GET` or `DEL` (protocol violation)
- Frame buffer is shorter than the computed frame size (partial frame → `INCOMPLETE`, not `ERROR`)

### 3.7 Partial Read Handling

TCP is a stream protocol. A single `recv()` call may return fewer bytes than one full frame. The parser must handle this gracefully:

```text
Client sends: [frame A (complete 18B)] [frame B (first 6 bytes only)]
              ─────────────────────────────────────────────────────────
recv() #1 → 24 bytes   parse frame A → COMPLETE (consumed=18)
                        parse remaining 6 bytes → INCOMPLETE
recv() #2 → 12 bytes   read_buf now has 18 bytes → parse frame B → COMPLETE
```

The read buffer accumulates bytes across reads. After a successful parse, consumed bytes are dropped from the front. A ring buffer instead of a resizing buffer is a natural later optimisation to avoid the O(n) shift on every parse.

---

## 4. Memory Model

### 4.1 The Problem with Per-Key Allocation

Every heap-allocated string value triggers a `malloc`. For a server doing 100 000 SET/sec, that is 100 000 individual heap allocations per second, each requiring a lock on the global allocator, each returning a pointer that lands at a random address. Cache miss rate approaches 100%.

### 4.2 Arena Allocator

The arena is a single pre-allocated contiguous buffer. All keys and values are appended sequentially. The allocator knows one operation: append.

```text
Arena buffer (contiguous in RAM):

offset:  0        8        20       26       38       52
         │        │        │        │        │        │
         ▼        ▼        ▼        ▼        ▼        ▼
         [session][login   ][token  ][user:42 ][bearer…]
          key₀     value₀   key₁     value₁   key₂
```

The hash table stores entries containing offsets into this buffer. Dereferencing a key or value is a single pointer add, no heap indirection, no cache miss.

### 4.3 Arena Fragmentation & Compaction

When a key is deleted or overwritten, its old bytes in the arena become dead space. The arena never shrinks on its own. Fragmentation is tracked as:

```text
fragmentation_ratio = dead_bytes / used_bytes
```

When the ratio exceeds a configured threshold (default: 0.35), compaction is triggered during the next background tick:

1. Allocate a new buffer of the same capacity.
2. Iterate all live entries in the hash table.
3. Copy each key+value into the new buffer; update offsets in-place.
4. Swap old and new buffers. Reset dead-byte counter to 0.
   Compaction is O(n) where n is total live data bytes - a sequential copy, fast and cache-friendly. It runs inside the event loop, so no requests are processed during it. This is acceptable because compaction is rare.

### 4.4 Memory Layout Summary

```text
Process memory map:

  heap
  ├── Arena buffer         ← ONE allocation. Never reallocated except at compaction.
  ├── Hash table buckets   ← ONE allocation. Doubles (rehash) when load > 0.70.
  ├── TTL heap vector      ← ONE allocation. Grows with key count.
  ├── per-client read_buf  ← one allocation per live connection
  └── per-client write_buf ← one allocation per live connection

  stack
  ├── EventLoop locals     ← epoll event array
  └── ProtocolParser       ← reads via pointers into read_buf (zero copy)
```

---

## 5. Project Structure

Planned high-level layout (subject to change once implementation starts):

```text
cache-store/
├── CMakeLists.txt
├── LICENSE
├── README.md
├── docs/
│   └── DESIGN.md                 ← this document
├── src/                          ← C++ implementation (event loop, protocol, store, arena, hash table)
├── client/                       ← Python client library + benchmark/stress-test scripts
└── tests/                        ← unit tests
```

**Planned dependencies:** Linux (`epoll`), C++20 compiler, CMake, Python 3.9+ for the test client and benchmarks.

---

## 6. Configuration

Planned CLI flags for the C++ server:

| Flag                 | Default | Description                                         |
| -------------------- | ------- | --------------------------------------------------- |
| `--port N`           | `6380`  | TCP port to listen on                               |
| `--arena-mb N`       | `64`    | Arena size in MiB                                   |
| `--expire-ms N`      | `100`   | TTL sweep interval in milliseconds                  |
| `--compact-ratio F`  | `0.35`  | Compact when arena fragmentation exceeds this ratio |
| `--max-clients N`    | `10000` | Maximum simultaneous connections                    |
| `--max-key-bytes N`  | `4096`  | Maximum key size in bytes                           |
| `--max-value-mb N`   | `64`    | Maximum value size in MiB                           |
| `--log-level L`      | `info`  | One of: `debug`, `info`, `warn`, `error`            |
| `--stats-interval N` | `10`    | Log a stats line every N seconds                    |

---

## 7. Implementation Roadmap

Each stage introduces exactly one new concept; stages are meant to be done in order.

| Stage                         | Goal                                       | Key concept introduced                          |
| ----------------------------- | ------------------------------------------ | ----------------------------------------------- |
| 0 - Bare TCP Server           | Accept connections, echo `OK`              | Socket API, TCP handshake                       |
| 1 - epoll Event Loop          | Handle many clients on one thread          | Non-blocking I/O, `epoll`                       |
| 2 - Binary Protocol Parser    | Parse fixed-format binary frames           | Binary serialisation, partial-read buffering    |
| 3 - Naive Store               | Working SET / GET / DEL, correctness first | Wiring a storage layer into the event loop      |
| 4 - Background TTL Expiration | Free memory proactively, not just on GET   | Min-heap, timer-driven work in the event loop   |
| 5 - Arena Allocator           | Eliminate per-key heap allocations         | Arena allocation, cache locality, fragmentation |
| 6 - Custom Hash Table         | Beat `unordered_map` in benchmarks         | Open addressing, Robin Hood probing, tombstones |
| 7 - Stats & Observability     | Server reports its own state               | Instrumentation, counters, hit rate             |
| 8 - Python Client Library     | Clean client for tests and benchmarks      | Reusable protocol client, smoke + TTL tests     |

Each stage has a concrete "done when" criterion (e.g. Stage 1 is done when 1000 idle clients are handled by a single thread; Stage 6 is done when it beats `unordered_map` by ≥15% at 500k keys). Exact acceptance tests and code will be written alongside implementation, not specified in advance here.

---

## 8. Known Limitations & Future Extensions

### 8.1 Anticipated Limitations

| Limitation                | Impact                                 | Notes                                                                             |
| ------------------------- | -------------------------------------- | --------------------------------------------------------------------------------- |
| Linux-only (`epoll`)      | Not portable to macOS or Windows       | Port to `kqueue` for macOS in a future iteration                                  |
| Single-threaded           | Cannot utilise more than one CPU core  | Sufficient for a learning project; `io_uring` or sharding could change this later |
| No persistence            | All data is lost on restart            | By design - this is a pure cache, not a database                                  |
| No authentication         | Any TCP client can read and write      | Run behind a firewall or add a shared-secret handshake in the protocol            |
| TTL resolution is seconds | Cannot expire keys in under one second | Could move to milliseconds with a protocol v2 bump                                |

### 8.2 Future Extensions (not planned for v1)

- **KEYS pattern** - scan and return keys matching a glob pattern
- **MSET / MGET** - batch operations to cut round-trip overhead
- **INCR / DECR** - atomic integer increment/decrement
- **FLUSH** - delete all keys atomically
- **STAT command** - expose live stats over the wire for monitoring
- **`io_uring`** - replace `epoll` for lower syscall overhead at high connection counts
- **Persistence snapshot** - memory-mapped arena for zero-copy save/restore
