# Evidence — rtk-rewrite Hermes adapter

Stable marker: RTK-HERMES-ADAPTER-20260926

This file records how the adapter was verified. It contains no secrets and no
local host paths; paths are relative to this repository.

## Pinned RTK

- Release tag: `v0.50.0`
- Release commit (target_commitish): `1d87b8e719ce0a50c223cd93ca64dd16921f9aec`
- Release URL: https://github.com/rtk-ai/rtk/releases/tag/v0.50.0
- Asset (x86_64 Linux): `rtk-x86_64-unknown-linux-musl.tar.gz`
- Asset SHA-256: `bc2b8902b0d9c796c82ef45f16ae2307e17757afeca5ee156235a3dc7bda5f89`
  (matches the `checksums.txt` published with the release)
- Provenance: https://github.com/rtk-ai/rtk/issues/3797 ; Apache-2.0

The verified binary is staged (gitignored) at `.staging/rtk` and reports
`rtk 0.50.0`. It is **not** committed and does **not** replace any installed
binary.

## Tested Hermes

- Hermes version: 0.21.5
- Tested Hermes commit: `a8a549ebb43a61b2b400b992e956b7304b8fe41a`
- Contract verified in the Hermes tree (read-only): `pre_tool_call` `modify`
  returns `{"action": "modify", "args": {...}}`; Hermes shallow-merges those
  keys into the original tool args, so returning only `{"command": rewritten}`
  preserves `cwd`/`workdir`, `timeout` and `background`.
- Docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks and
  https://hermes-agent.nousresearch.com/docs/developer-guide/plugins

## Difference from the upstream hook

Upstream RTK v0.50.0 `hooks/hermes/rtk-rewrite/__init__.py` assigns
`args["command"] = rewritten` and returns `None`. In the tested Hermes runtime
that mutation does reach tool dispatch. This adapter returns an explicit `modify`
directive instead, which Hermes shallow-merges into the original arguments. Both
rewrote the tested command; the directive is an implementation choice. Upstream
authorship is preserved (see NOTICE).

## Commands run (real results)

### RTK rewrite behaviour (pinned v0.50.0, temp git fixture)

| Input command | Rewritten output | Exit |
| --- | --- | --- |
| `cat a.txt` | `rtk read a.txt` | 3 |
| `git status` | `rtk git status` | 3 |
| `git log --oneline` | `rtk git log --oneline` | 3 |
| `ls -la` | `rtk ls -la` | 3 |
| `head -5 a.txt` | `rtk read a.txt --head-lines 5` | 3 |
| `pwd` | (none) | 1 |
| `echo hello` | (none) | 1 |
| `true` | (none) | 1 |

Observed exit codes: `3` = rewrite available on stdout; `1` = passthrough. The
adapter's documented sets `{0,3}` accepted / `{1,2}` passthrough are retained;
live testing on v0.50.0 observed `1` and `3`.

### Test suite

Run with the Hermes venv python (which provides `hermes_cli` for the dispatch
tests):

```
python tests/run_tests.py all
```

- Unit tests mock `subprocess.run` and cover: unsupported tools, empty args,
  existing rtk commands, rewrite return codes (0/3 accepted, 1/2 passthrough,
  unexpected failure), missing binary, timeout, unexpected exception, the
  modify-directive shape (only `command` returned), subprocess invocation shape
  (no shell, bounded timeout), and `register()` gating.
- Hermes dispatch tests install the plugin into throwaway `HERMES_HOME`s,
  discover it through the real `hermes_cli.plugins` machinery, and dispatch
  through `_dispatch_pre_tool_call_hooks`. They assert the real shallow merge
  preserves `cwd`/`timeout`/`background`, passthrough stays untouched, and
  profile switching A -> B -> A keeps working.
- Integration tests run the real pinned RTK on a throwaway temp git fixture
  (never a user repo) and confirm `cat` -> `rtk read`, `git status` ->
  `rtk git status`, passthrough untouched, the rewritten command is executable
  and produces the same content as the original, and the exit-code semantics
  match the documented contract.

## Known limits

- The adapter adds a subprocess call (bounded at 2s) to every `terminal`
  command; there is no guarantee of net speedup. Measure for your workload.
- RTK rewrites change command output (filtering/summarising). Workflows that
  depend on exact native output should disable the plugin or exclude the
  command.
- The adapter runs on the Hermes host. For Docker/SSH execution targets, the
  rewritten command still invokes `rtk` on the target, so a compatible binary
  must also be on the target's PATH. Missing target binaries cause execution
  failure, not host-side fail-open. Verify both environments.
- `RTK_BIN` is honoured as an override; when unset the adapter resolves `rtk`
  from `PATH`. This selects only the host-side rewrite executable, not the
  executable used by the resulting command on its execution target.

## Clean-environment verification

The suite is run with `RTK_BIN` unset, a restricted `PATH=/usr/bin:/bin`, an
isolated `HOME`/`HERMES_HOME`, and the pinned Hermes checkout with its own frozen
venv, so nothing on the development machine can mask a missing dependency:

```bash
mkdir -p .staging/test-home .staging/tmp
env -i PATH=/usr/bin:/bin HOME="$PWD/.staging/test-home" \
  HERMES_HOME="$PWD/.staging/test-home/.hermes" TMPDIR="$PWD/.staging/tmp" \
  HERMES_TREE="$PWD/.staging/hermes" \
  .staging/hermes/.venv/bin/python tests/run_tests.py all
```

Skipped tests make the runner exit non-zero: a missing Hermes tree or RTK binary
is reported as missing coverage, never as a pass. CI installs the pinned Hermes
checkout using `uv==0.12.5` and `uv sync --frozen --no-dev --python 3.11`.

**Public release remains blocked pending verification on a different Hermes
instance.** A clean CI checkout or same-host isolated homes are not that gate.

## Rollback recipe

1. `hermes plugins disable rtk-rewrite` — stops the hook firing.
2. Or remove the plugin directory from the Hermes home.
3. Or unset `RTK_BIN` and remove `rtk` from `PATH` — the adapter then fails
   open and every command runs unrewritten.

No live Hermes state is modified by installing or removing this plugin beyond
the plugin registry entry.


## Terminal backend gate and container layer

Cause: the rewritten command runs on the terminal backend, which may have no `rtk`.
In a Docker-backed profile, 52 of 839 recorded terminal results contained
`rtk: command not found` (31 exit 127, 16 exit 0 with a failed segment, 5 other).

Change: `__init__.py` rewrites only when the active backend is allowed (default
`local`; per-profile `plugins.entries.rtk-rewrite.settings.backends`), and skips
when the backend is unknown. Backend is resolved through
`tools.terminal_tool._get_env_config()` with `tools.terminal_scope.terminal_env()`
as fallback, not `os.environ`. `container/Containerfile` adds RTK v0.50.0
(SHA-256 `bc2b8902b0d9c796c82ef45f16ae2307e17757afeca5ee156235a3dc7bda5f89`).

Results:

- Full suite, empty environment with restricted PATH: **50 tests, OK, no skips**
  (36 unit, 9 dispatch, 5 integration).
- Negative control: the 5 new dispatch tests against the adapter without the gate gave 3
  failures (docker profile rewritten by default, ambient env deciding the backend,
  local/docker alternation leaking).
- Finding: dispatch tests must bind the profile's terminal policy
  (`tools.terminal_scope.set_terminal_scope(build_profile_terminal_scope(home))`),
  as the gateway does. Without it Hermes uses a process-wide bridge that latches on
  the first home, and the tests give misleading results.
- Image: built on a Playwright/Node 22 base; `rtk --version` = 0.50.0,
  `rtk rewrite "cat a.txt"` = `rtk read a.txt` (exit 3), `rtk read a.txt` works,
  telemetry reports "never asked / not enabled".
- `hermes plugins validate`: passed, security scan safe.

Not yet verified: behaviour in a live multiplexed gateway, and on a different Hermes
instance (the public-release gate still applies).
