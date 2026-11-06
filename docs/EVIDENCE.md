# Evidence: RTK 1.1.0 and Hermes 0.23.0

## Assessment

The Docker backend gate is still needed.

- Latest stable RTK: `v1.1.0`, published 2026-10-25.
- Latest stable Hermes: `v0.23.0`, published 2026-11-05.
- RTK still ships `hooks/hermes/rtk-rewrite/__init__.py` without a backend gate.
- That hook runs `rtk rewrite` on the host and changes `args["command"]` for every terminal backend.
- Hermes v0.23.0 has no built-in RTK integration. A search of its Python source found only a scanner test fixture.
- Real Hermes dispatch still accepts RTK rewrites for Docker profiles. It does not check whether the container has RTK.

This adapter adds the backend opt-in. An unconfigured Docker profile keeps its original commands. An opted-in profile needs RTK inside its container.

Sources:

- https://github.com/rtk-ai/rtk/releases/tag/v1.1.0
- https://github.com/rtk-ai/rtk/blob/753a3b0e939f2e4490fc24e3a1984010f40a103c/hooks/hermes/rtk-rewrite/__init__.py
- https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.0
- https://hermes-agent.nousresearch.com/docs/developer-guide/plugins
- https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks

## Pins

| Component | Version or value |
| --- | --- |
| Adapter | `0.1.0`, runtime code unchanged |
| RTK | `1.1.0` |
| RTK commit | `753a3b0e939f2e4490fc24e3a1984010f40a103c` |
| RTK asset | `rtk-x86_64-unknown-linux-musl.tar.gz` |
| Asset SHA-256 | `9454ad1b9489daa57ab0a9f4ebe2faaf4dc82b8a0f26c9e5135151abd7c9378d` |
| Hermes | `0.23.0` |
| Hermes commit | `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19` |
| Python | `3.14.9` |
| Container test base | `docker.io/library/node@sha256:18a6d90fd60542269850c80ca03b29b1d9b14d6b221ac7ee8e35b0d33ef904bd` |
| Test image | `localhost/hermes-lab-rtk-test:1.1.0` |
| Local container engine | Podman `5.7.0`, through the `docker` command |

The archive checksum matches the release `checksums.txt`. The staged executable reports `rtk 1.1.0`.
The container layer and CI use the same archive checksum. No binary is committed.

The host installation reports Hermes `0.22.7`. These tests use the separate, pinned `0.23.0` source checkout and PM environment.
The host installation and live profiles stay unchanged.

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
  https://github.com/rtk-ai/rtk/releases/download/v1.1.0/rtk-x86_64-unknown-linux-musl.tar.gz
printf '%s  %s\n' \
  9454ad1b9489daa57ab0a9f4ebe2faaf4dc82b8a0f26c9e5135151abd7c9378d \
  .staging/rtk.tar.gz | sha256sum -c -
tar -xzf .staging/rtk.tar.gz -C .staging rtk
.staging/rtk --version

PYTHONPATH="$PWD/.staging/hermes" python3 -m pm.build_env \
  --source "$PWD/.staging/hermes" --out "$PWD/.staging/hermes-venv14" \
  --python "$(command -v python3)" --cache "$PWD/.staging/pm-cache"

docker build -f container/Containerfile \
  --build-arg BASE_IMAGE=docker.io/library/node@sha256:18a6d90fd60542269850c80ca03b29b1d9b14d6b221ac7ee8e35b0d33ef904bd \
  -t localhost/hermes-lab-rtk-test:1.1.0 container/
```

The PM build uses Hermes's frozen lock. Python 3.11 cannot use this release lock, which requires Python 3.14 or later.
No raw `pip` or `uv sync` changes to Hermes environments are needed.

The container build returned:

```text
/tmp/rtk.tar.gz: OK
rtk 1.1.0
```

## Test results

All four repository suites passed. No test was skipped.

| Suite | Tests | Result |
| --- | ---: | --- |
| Adapter unit | 36 | OK |
| Real Hermes dispatch | 9 | OK |
| Real RTK integration | 10 | OK |
| Real container and dispatch | 4 | OK |
| Combined | 59 | OK |

The unit, dispatch and integration suites also passed with an empty environment and restricted `PATH=/usr/bin:/bin`:

```bash
for suite in adapter dispatch integration; do
  env -i PATH=/usr/bin:/bin HOME="$PWD/.staging/test-home" \
    HERMES_HOME="$PWD/.staging/test-home/.hermes" TMPDIR="$TMPDIR" \
    HERMES_TREE="$PWD/.staging/hermes" RTK_TELEMETRY_DISABLED=1 \
    .staging/hermes-venv14/bin/python tests/run_tests.py "$suite"
done
```

The combined suite retains the host container engine environment. The local `docker` command wraps Podman.
A restricted PATH selected a different Docker client without access to its socket. That attempt failed, not skipped.
The corrected run uses the working engine and an isolated Hermes home:

```bash
env -u RTK_BIN HERMES_HOME="$PWD/.staging/test-home/.hermes" \
  HERMES_TREE="$PWD/.staging/hermes" \
  RTK_TEST_IMAGE=localhost/hermes-lab-rtk-test:1.1.0 RTK_TELEMETRY_DISABLED=1 \
  .staging/hermes-venv14/bin/python tests/run_tests.py all
```

Coverage includes:

- Default Docker passthrough, explicit opt-in and unknown-backend passthrough.
- Per-call settings and alternating local/Docker profiles without policy leaks.
- Real Hermes hook discovery and shallow merge of `command`, with other arguments preserved.
- Current Hermes plugin-manager cleanup, instead of the removed `unload_plugins` call.
- RTK version, rewrite codes, chained commands, pipeline consumers and redirection safety.
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
| `cat a.txt && git status` | `rtk read a.txt && rtk git status` | 3 |
| `git status \| grep branch` | `git status \| rtk grep branch` | 3 |
| `git status \| cat` | `rtk git status \| cat` | 3 |
| `cat a.txt > copy.txt` | none | 1 |
| `cat a.txt 2>&1` | `rtk read a.txt 2>&1` | 3 |
| `pwd` | none | 1 |
| `rtk bypass cat a.txt` | unchanged | 3 |

The adapter still accepts `{0, 3}` and treats `{1, 2}` as passthrough.
A code of 3 does not always mean the command changed. The adapter compares stdout with the original command.

## Remaining release gate and limits

Tom's separate `hermes-lab` profile test follows this branch handoff. These automated tests do not replace that gate.
A live multiplexed gateway is not part of this test run.

- The container layer supports x86_64 Linux only.
- A profile must opt in only after its target contains a compatible RTK executable.
- The adapter uses Hermes terminal internals. Repeat the dispatch suite after Hermes updates.
- RTK filters output. Use `rtk bypass <command>` for native output, subject to normal tool limits and approvals.
- The host-side rewrite has a two-second timeout and fails open. A missing target binary remains an execution error.

## Rollback

Disable `rtk-rewrite-docker` in the affected profile, or remove `docker` from its backend setting.
The original terminal commands then run without RTK rewrites.
