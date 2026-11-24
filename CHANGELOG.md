# Release notes

## 0.1.2

- This update supports RTK 1.2.1. Use it on the Hermes host and in each backend that uses RTK. The supplied container layer installs it.
- RTK now filters output from more Cargo commands, including commands that select a Rust toolchain.
- Plugin settings are unchanged. Docker and remote backends still require explicit opt-in.