# cache-store

![status](https://img.shields.io/badge/status-early%20MVP-orange)

An in-memory key-value store built from the ground up, inspired by how systems like Redis are designed internally. Currently an early Python MVP implementing `GET` / `SET` / `DEL` with TTL; a planned move to C++ will add a custom binary protocol and an epoll-based event loop.

## Status

🚧 **Early MVP** - core commands and TTL are being built out in Python first to validate the design before the C++ rewrite. Not production-ready, not benchmarked yet.

## Roadmap

- [ ] Python MVP: `GET` / `SET` / `DEL` + TTL expiration
- [ ] Basic tests + usage examples
- [ ] Rewrite in C++: custom binary protocol, `epoll` event loop
- [ ] Arena memory model, custom hash table
- [ ] Benchmarking against baseline

Full target architecture, binary protocol spec, and long-term roadmap are documented in [`docs/DESIGN.md`](docs/DESIGN.md) - written as a design doc **before** implementation, so treat it as a plan, not a description of current functionality.

## License

MIT
