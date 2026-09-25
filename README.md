# Pith

![status](https://img.shields.io/badge/status-early%20MVP-orange)

Data is a fundamental part of any program. Programs store, read, transform, and transmit data, making the way data is stored an important factor in system performance.

**Pith** is an in-memory key-value store built from the ground up, inspired by how systems like Redis are designed internally. It currently exists as an early-stage MVP implemented in Python, featuriing in-memory data storage and supporting `GET`, `SET`, `DEL` and TTL operations.

The project is not intended to be a mere educational clone of Redis. Instead, it is an independent implementation that explores its own design choices in areas such as the communication protocol, data storage, and server architecture. Python is being used for the MVP phase to validate core concepts and system behavior, with the long-term implementation planned in C++ or Rust.

## Status

🚧 **Early MVP**

The current implementation supports:

- `GET`, `SET` and `DEL` operations
- TTL-based key expiration
- A custom binary TCP protocol
- TCP stream fragmentation and multiple frames in a single stream
- Basic integration tests
- A simple client demonstrating how to interact with the server

Pith is not production-ready and has not been benchmarked yet.

## Roadmap

- [x] Python MVP: `GET` / `SET` / `DEL`, TTL expiration, binary protocol
- [x] Basic tests + usage examples
- [ ] Rewrite in C++ (or in Rust, idk)
- [ ] Arena memory model, custom hash table, epoll on Linux
- [ ] Benchmarking against baseline

Full target architecture, binary protocol spec, and long-term roadmap are documented in [`docs/DESIGN.md`](docs/DESIGN.md) - written as a design doc **before** implementation, so treat it as a plan, not a description of current functionality.

## Project Structure

```bash
pith/
├── server.py          # Pith server and executable entry point
├── client.py          # Example client for interacting with Pith
├── proto.py           # Binary protocol definitions and frame parsing
├── storage.py         # In-memory key-value storage and TTL handling
├── config.yaml        # Server configuration
└── tests/
    └── integration.py # Integration tests for the server and protocol
```

## License

MIT
