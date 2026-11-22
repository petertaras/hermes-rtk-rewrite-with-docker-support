# Release notes

## 0.1.2

- Use RTK 1.2.1 on the Hermes host and in each backend that uses RTK. The supplied container layer includes the new version.
- RTK now filters more Cargo commands, including commands that select a Rust toolchain or use `--locked`.
- RTK now runs fallback commands after supported global options such as `--verbose`.
- `rtk gain` no longer shows the opt-out reminder next to the Pro line.
- Docker and remote backends still require explicit opt-in.
