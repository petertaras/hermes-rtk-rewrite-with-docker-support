# Evidence: RTK 1.2.1 and Hermes 0.23.0

## Assessment

The Docker backend gate is still needed.

- Pinned RTK: `v1.2.1`, published 2026-11-19.
- Pinned Hermes: `v0.23.0`, published 2026-11-05.
- RTK still ships `hooks/hermes/rtk-rewrite/__init__.py` without a backend gate.
- That hook runs `rtk rewrite` on the host and changes `args["command"]` for every terminal backend.
- Hermes v0.23.0 has no built-in RTK integration. A search of its Python source found only a scanner test fixture.
- Real Hermes dispatch still accepts RTK rewrites for Docker profiles. It does not check whether the container has RTK.

The RTK update does not change the `rtk rewrite` interface used by this adapter. The hook integrity and compound hook changes apply to other clients that install RTK hooks. The fallback fix changes how explicit `rtk` commands run, not how the adapter requests a rewrite.

This adapter adds the backend opt-in. An unconfigured Docker profile keeps its original commands. An opted-in profile needs RTK inside its container.

Sources:

- https://github.com/rtk-ai/rtk/releases/tag/v1.2.1
- https://github.com/rtk-ai/rtk/blob/d0f0399a5ff8cd1d81325a033f0b2d8864d665f6/hooks/hermes/rtk-rewrite/__init__.py
- https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.0
- https://hermes-agent.nousresearch.com/docs/developer-guide/plugins
- https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks

## Pins

| Component | Version or value |
| --- | --- |
| Adapter | `0.1.2`, runtime code unchanged |
| RTK | `1.2.1` |
| RTK commit | `d0f0399a5ff8cd1d81325a033f0b2d8864d665f6` |
| RTK asset | `rtk-x86_64-unknown-linux-musl.tar.gz` |
| Asset SHA-256 | `75f64525711be935657a6b228d929c24bd9d1ab7d640ea374a8b913e5b5bac05` |
| Hermes | `0.23.0` |
| Hermes commit | `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19` |
| Python | `3.14.9` |
| Container test base | `docker.io/library/node@sha256:18a6d90fd60542269850c80ca03b29b1d9b14d6b221ac7ee8e35b0d33ef904bd` |
| Test image | `localhost/hermes-lab-rtk-test:1.2.1` |

The archive checksum matches the release `checksums.txt`. The staged executable reports `rtk 1.2.1`.
The container layer and CI use the same archive checksum. No binary is committed.

The original automated run used a separate, pinned `0.23.0` source checkout and PM environment.
At that time, the host installation reported Hermes `0.22.7`. That run did not change the host installation or live profiles.
The earlier lab results below cover both host versions with RTK 1.1.0.

## Setup

All paths below are relative to this repository. `.staging/` is ignored by Git.
Use a managed scratch directory for `TMPDIR`. Do not use a live Hermes home.

```bash
mkdir -p .staging/test-home .staging/runtime
export HERMES_HOME="$PWD/.staging/test-home"
export HERMES_RUNTIME_DIR="$PWD/.staging/runtime"
export RTK_TELEMETRY_DISABLED=1

git clone --depth 1 --branch v0.23.0 \
  https://github.com/NousResearch/hermes-agent.git .staging/hermes

test "$(git -C .staging/hermes rev-parse HEAD)" = 2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19
curl -fsSL -o .staging/rtk.tar.gz \
  https://github.com/rtk-ai/rtk/releases/download/v1.2.1/rtk-x86_64-unknown-linux-musl.tar.gz
printf '%s  %s\n' \
  75f64525711be935657a6b228d929c24bd9d1ab7d640ea374a8b913e5b5bac05 \
  .staging/rtk.tar.gz | sha256sum -c -
tar -xzf .staging/rtk.tar.gz -C .staging rtk
.staging/rtk --version

PYTHONPATH="$PWD/.staging/hermes" python3 -m pm.build_env \
  --source "$PWD/.staging/hermes" --out "$PWD/.staging/hermes-venv14" \
  --python "$(command -v python3)" --cache "$PWD/.staging/pm-cache"

docker build -f container/Containerfile \
  --build-arg BASE_IMAGE=docker.io/library/node@sha256:18a6d90fd60542269850c80ca03b29b1d9b14d6b221ac7ee8e35b0d33ef904bd \
  -t localhost/hermes-lab-rtk-test:1.2.1 container/
```

The PM build uses Hermes's frozen lock. Python 3.11 cannot use this release lock, which requires Python 3.14 or later.
No raw `pip` or `uv sync` changes to Hermes environments are needed.

## Test coverage

Run all four suites with the staged RTK binary, pinned Hermes checkout and test image:

```bash
HERMES_TREE="$PWD/.staging/hermes" RTK_TEST_IMAGE=localhost/hermes-lab-rtk-test:1.2.1 \
  .staging/hermes-venv14/bin/python tests/run_tests.py all
```

See the [CI results](https://github.com/petertaras/hermes-rtk-rewrite-with-docker-support/actions/workflows/ci.yml).

Coverage includes:

- Default Docker passthrough, explicit opt-in and unknown-backend passthrough.
- Per-call settings and alternating local/Docker profiles without policy leaks.
- Real Hermes hook discovery and shallow merge of `command`, with other arguments preserved.
- Current Hermes plugin-manager cleanup, instead of the removed `unload_plugins` call.
- RTK version, rewrite codes, chained commands, pipeline consumers and redirection safety.
- Cargo toolchain and global options, unknown Cargo options, and explicit RTK fallback after global options.
- Raw `rtk bypass` output and unchanged bypass commands. `rtk proxy` is now a deprecated alias.
- Image version and `RTK_TELEMETRY_DISABLED=1`.
- Real Hermes dispatch to `rtk read`, followed by execution inside a network-disabled, disposable container.

CI builds the pinned Hermes environment through PM, builds the container layer and runs `all`.
The test runner returns non-zero for any skipped test. Missing prerequisites do not count as passing coverage.

## Observed RTK behaviour

| Input | Output | Exit |
| --- | --- | ---: |
| `cat a.txt` | `rtk read a.txt` | 3 |
| `git status` | `rtk git status` | 3 |
| `cargo +stable test` | `rtk cargo +stable test` | 3 |
| `cargo --locked check` | `rtk cargo --locked check` | 3 |
| `cargo --unknown test` | none | 1 |
| `cat a.txt && git status` | `rtk read a.txt && rtk git status` | 3 |
| `git status \| grep branch` | `git status \| rtk grep branch` | 3 |
| `git status \| cat` | `rtk git status \| cat` | 3 |
| `cat a.txt > copy.txt` | none | 1 |
| `cat a.txt 2>&1` | `rtk read a.txt 2>&1` | 3 |
| `pwd` | none | 1 |
| `rtk bypass cat a.txt` | unchanged | 3 |

An explicit `rtk -v --skip-env echo fallback` stays unchanged. It prints `fallback` and exits with code 0.

The adapter still accepts `{0, 3}` and treats `{1, 2}` as passthrough.
A code of 3 does not always mean the command changed. The adapter compares stdout with the original command.

## Hermes-lab profile tests

These historical results use RTK 1.1.0.


Both lab tests passed with plugin revision `b620e4caf44e7ec8db1727561fe2a1fa71ab5d93`.
The 0.1.1 release used the same runtime hook and container layer as those lab tests.

| Task | Hermes | Python | Docker container | Result |
| --- | --- | --- | --- | --- |
| `t_574c3813` | `0.22.7`, `b200f5bc60e3` | `3.11.16` | `hermes-240c75c5` | Pass |
| `t_cb72b2d4` | `0.23.0`, `2bd378ed1b8d` | `3.11.16` | Fresh `hermes-24ab53bc` | Pass |

Both runs used `localhost/hermes-lab-sandbox:rtk1.1.0`, built on `docker.io/nousresearch/hermes-sandbox:desktop`.
The image ID was `sha256:4cc74bca54a8d5bc40057db0bc72c63f5f698f87f4903832318ad362ae4a7365`.
The host rewrite binary and `/usr/local/bin/rtk` in the container both reported `rtk 1.1.0`.

Observed results in both runs:

- Without Docker opt-in, `cat`, `git status` and `pwd` stayed unchanged.
- With Docker opt-in, `cat /workspace/rtk-fixture.txt` became `rtk read /workspace/rtk-fixture.txt`. It returned `container evidence`, exit 0.
- `ls -la /workspace` became `rtk ls -la /workspace`. It returned the fixture listing, exit 0.
- `git status` became `rtk git status`. Outside a Git repository, it returned `Not a git repository`, exit 128, as expected.
- `command -v rtk && rtk --version` returned `/usr/local/bin/rtk` and `rtk 1.1.0`, exit 0.
- `rtk bypass printf %s raw-container-evidence` stayed unchanged and returned `raw-container-evidence`, exit 0.
- No result contained `rtk: command not found`.

Other profiles stayed unchanged. The 0.23.0 retest compared their configuration hashes before and after the run.
The shared host RTK stayed at `0.46.0`. Only the lab used the staged host RTK 1.1.0 binary.
The lab containers ran without a disk quota because the host storage driver did not support per-container disk limits.

The original full CI run also passed all 59 tests, with zero skipped:
https://github.com/petertaras/hermes-rtk-rewrite-with-docker-support/actions/runs/42148926407

## Limits

A live multiplexed gateway is not part of these test runs.

- The container layer supports x86_64 Linux only.
- A profile must opt in only after its target contains a compatible RTK executable.
- The adapter uses Hermes terminal internals. Repeat the dispatch suite after Hermes updates.
- RTK filters output. Use `rtk bypass <command>` for native output, subject to normal tool limits and approvals.
- The host-side rewrite has a two-second timeout and fails open. A missing target binary remains an execution error.

## Rollback

Disable `rtk-rewrite-docker` in the affected profile, or remove `docker` from its backend setting.
The original terminal commands then run without RTK rewrites.
