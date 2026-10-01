# hermes-rtk-rewrite with Docker support

A Hermes Agent plugin adapter that rewrites `terminal` tool commands through
[RTK](https://github.com/rtk-ai/rtk) before they execute.

This is a **thin bridge only**. All command-compression/rewrite logic lives in
the RTK binary (`rtk rewrite`). This plugin does not reimplement any of it; it
invokes the binary and hands the result back to Hermes through the
`pre_tool_call` modify contract.

- **Adapter version:** 0.1.0 (independent of the RTK executable version)
- **Pinned RTK:** v0.50.0 (tag `v0.50.0`, commit `1d87b8e719ce0a50c223cd93ca64dd16921f9aec`)
- **Tested Hermes commit:** `a8a549ebb43a61b2b400b992e956b7304b8fe41a` (Hermes 0.21.5)
- **License:** Apache-2.0 (see `LICENSE` and `NOTICE`)

**Validation candidate:** keep this repository private until the plugin has
been verified on a different Hermes instance. CI and isolated test homes on
the development machine do not satisfy that release gate.

---

## Docker and remote backends: the problem and our fix

### The problem

The adapter rewrites a command on the **Hermes host**, but the rewritten command
runs on the **terminal backend**. With a Docker (or SSH, Modal, ...) backend that is
a different machine, and it usually has no `rtk`:

```
Hermes host:    cat a.txt        ->  rtk rewrite  ->  rtk read a.txt
Docker backend: bash: rtk: command not found        (exit 127)
```

The host-side fail-open behaviour does not help, because the failure happens after
the rewrite. We saw this in a real deployment with a Docker-backed profile: 52 of
839 terminal results contained `rtk: command not found`. 31 of those exited 127 and
needed a retry. 16 exited 0, because later parts of a compound command carried on
after one part failed, so some output was silently missing.

A second trap: it is tempting to decide the backend from `os.environ["TERMINAL_ENV"]`.
When one Hermes process serves several profiles (the multiplexed gateway), terminal
policy is kept per profile and deliberately not mirrored into the process
environment, so the variable describes the launch profile, not the profile whose
command is running.

### Our fix

1. **Backend gate.** Before calling `rtk`, the adapter asks Hermes which backend will
   run this command, through the same per-profile terminal policy the terminal tool
   uses. It rewrites only when that backend is allowed. The default is `local` only.
   If the backend cannot be determined, nothing is rewritten. You lose some token
   savings, never a working command.
2. **Per-profile opt-in.** A profile whose execution environment does contain `rtk`
   opts in:

   ```yaml
   # <profile>/config.yaml
   plugins:
     entries:
       rtk-rewrite:
         settings:
           backends: [local, docker]   # or "all"
   ```

   Settings are read on every call, per profile. A profile without this block stays
   local-only.
3. **A container layer that adds `rtk`.** [`container/Containerfile`](container/Containerfile)
   adds the pinned, checksum-verified static binary (RTK v0.50.0, x86_64 Linux) on top
   of any base image:

   ```bash
   docker build -f container/Containerfile \
     --build-arg BASE_IMAGE=localhost/my-image:1.0 \
     -t localhost/my-image:1.0-rtk0.50.0 container/
   ```

   Then point the profile at the new image (`terminal.docker_image`) and add the
   opt-in above. RTK telemetry is switched off inside the image
   (`RTK_TELEMETRY_DISABLED=1`).

**Roll out in this order:** new image, then the opt-in, then a fresh session (a
running container keeps its old image). Without the image, leave the opt-in off:
the gate then protects you, and the plugin behaves as it does on a local backend.

### Installing from GitHub for a Docker-backed agent

Expect two prompts that are not Docker problems but will stop a scripted install.
Install into the profile that owns the Docker backend, pinned to a commit:

```bash
hermes -p <profile> plugins install PeterTaras/hermes-rtk-rewrite-with-docker-support \
  --ref <40-char-commit-sha> --force --enable
```

1. **The plugin scanner returns a `caution` verdict and blocks the install**
   ("community source"). The findings are pattern matches on this repository's own
   files: the checksum check in CI (`echo "<sha256>  file" | sha256sum -c`), the
   intentional `subprocess.run([rtk, "rewrite", ...])` in `__init__.py`, test fixtures
   that run `git` and `rtk` on a temporary repository, and README prose that mentions
   `os.environ`. Read the findings yourself, then override with `--force`. Always pin
   `--ref` to an exact commit, so what you reviewed is what you install.
2. **A dependency consent prompt appears even though there are no dependencies.**
   Hermes treats any `pyproject.toml` as declaring Python dependencies and asks
   "Prepare these with Hermes through PM now? [y/N]". In a non-interactive shell
   (an agent's terminal tool, CI, cron) the install fails with
   `Reinstall declined: dependency install skipped (non-interactive)`, and
   `--no-deps` cannot replace an already active plugin. Run the install from a real
   terminal and answer `y`, or give the command a pseudo-terminal, for example:

   ```bash
   printf 'y\ny\n' | script -qec "hermes -p <profile> plugins install ... --force --enable" /dev/null
   ```

   The adapter installs nothing into the Hermes environment (`dependencies = []`).

After installing: set the profile's `terminal.docker_image` to an image that contains
`rtk`, add the `backends` opt-in, and remove the profile's existing container so the
next session starts from the new image. Verify with a plain command that RTK rewrites,
such as `git status`: a rewritten run prints RTK's compact format. Compound commands
(`cd x && git status | head`) are not rewritten by RTK, so they prove nothing.

### How it was verified

- Unit tests cover the gate, the setting parser and backend detection, including
  "unknown backend never rewrites" and per-call settings reads.
- Dispatch tests run the real Hermes plugin machinery with each profile's terminal
  policy bound the way the gateway binds it. They include the multiplexer case
  (process environment says `local`, the profile says `docker`) and
  local/docker/local/docker alternation without leakage.
- Negative control: the new Docker tests fail against the adapter without the gate (3 failures), so they
  detect the bug.
- The image was built and run: `rtk 0.50.0`, `rtk rewrite "cat a.txt"` returns
  `rtk read a.txt` (exit 3), and `rtk read` works inside the container.

### Limits

- The container layer supports **x86_64 Linux** only (static musl release asset).
- Backend detection uses Hermes internals (`tools.terminal_tool`, `tools.terminal_scope`),
  which are not a documented plugin surface. If they change, detection returns
  "unknown" and the plugin stops rewriting. It fails safe, and it is a reason to rerun
  the dispatch tests after a Hermes update.
- An opted-in profile whose image lacks `rtk` will fail exactly as before. The
  adapter does not probe the target.
- Compound commands, heredocs, already-prefixed `rtk` commands and commands without
  RTK filters are not rewritten by RTK itself, which is why failures were limited.

---

## Hermes hook integration

Upstream RTK v0.50.0 ships a Hermes hook
(`hooks/hermes/rtk-rewrite/__init__.py`) whose `pre_tool_call` callback does:

```python
rewritten = result.stdout.strip()
if rewritten and rewritten != command:
    args["command"] = rewritten   # mutates the hook's args dict
# the callback returns None
```

In the Hermes runtime tested here, that mutation **does** reach tool dispatch:
Hermes passes the same argument dict to the hook and uses it when the callback
returns no `modify` directive. An isolated dispatcher test confirmed that the
upstream-style hook rewrites `cat a.txt` to `rtk read a.txt`.

This adapter instead returns `{"action": "modify", "args": {"command":
rewritten}}`. Hermes shallow-merges that directive into the original tool
arguments, preserving other fields (`cwd`/`workdir`, `timeout`, `background`).
Both approaches rewrote the tested command; the explicit directive is an
implementation choice, **not** a necessary fix for a non-functional upstream
hook.

Upstream authorship is preserved (see `NOTICE`). This adapter also supports an
`RTK_BIN` override and a bounded rewrite timeout.

---

## Install / enable / update / uninstall

The plugin is a flat directory: `plugin.yaml` + `__init__.py` at the repo root.
Install it as a user plugin by copying the directory into your Hermes home:

```bash
# From this repo root:
mkdir -p ~/.hermes/plugins/rtk-rewrite
cp plugin.yaml __init__.py ~/.hermes/plugins/rtk-rewrite/
```

Enable it (plugins are opt-in):

```bash
hermes plugins enable rtk-rewrite
hermes plugins list
```

Update: replace the two files with the new release and re-enable if needed.

Uninstall:

```bash
hermes plugins disable rtk-rewrite
# then remove the directory:
rm -rf ~/.hermes/plugins/rtk-rewrite
```

### RTK binary requirement

The adapter needs the `rtk` binary on `PATH` on the **Hermes host**. It does
not download or bundle a binary. To select a specific executable for the
host-side rewrite subprocess, set `RTK_BIN` to its absolute path. This override
does not change how the resulting command locates `rtk` on its execution target.

> **Docker / SSH targets:** the adapter rewrites only for backends a
> profile has opted in to (default: `local`). The target needs a compatible `rtk`
> on its `PATH` before you opt in. See
> [Docker and remote backends](#docker-and-remote-backends-the-problem-and-our-fix).

---

## Adapter vs RTK

| Concern | RTK | This adapter |
| --- | --- | --- |
| Command compression/rewrite logic | Yes (`rtk rewrite`) | No |
| Invoking the binary | — | Yes |
| Hermes `pre_tool_call` integration | Upstream hook mutates the supplied arguments (works in the tested runtime) | Returns an explicit `modify` directive (also works) |
| Fail-open behaviour | — | Yes (see below) |

---

## Fail-open behaviour

The adapter **fails open**: on any of the following it returns `None`, so Hermes
runs the **original** command unchanged:

- `rtk` binary missing or unresolvable
- the terminal backend is not opted in, or cannot be determined
- `rtk rewrite` times out (bounded at 2s)
- `rtk rewrite` exits with an unexpected code
- any unexpected exception

This is distinct from Hermes' own fail-**closed** behaviour: a `pre_tool_call`
callback that hangs past `plugins.hook_callback_timeout` (default 30s) is
abandoned and Hermes blocks the tool. The adapter's 2s subprocess timeout is
deliberately well under that 30s ceiling so a hung RTK process can never wedge
the tool gate — the adapter returns `None` and the original command proceeds.

### RTK exit codes

- `{0, 3}` — a rewrite is available on stdout; the adapter returns a `modify`
  directive (only when the rewritten command differs from the original).
- `{1, 2}` — no rewrite (passthrough); the adapter returns `None`.
- anything else — unexpected failure; the adapter warns and returns `None`.

These are kept as documented unless a pinned RTK version proves otherwise (see
`docs/EVIDENCE.md` for what v0.50.0 actually returned in testing).

---

## Output-filtering tradeoffs

RTK rewrites commands to use its own `rtk read`/`rtk grep`/etc. subcommands,
which filter and summarise command output. That is the point of RTK (less
noise, lower token cost), but it is a **behavioural change**: the rewritten
command may produce different output than the original. For exact native output,
use `rtk proxy <command>` for that invocation, for example `rtk proxy git diff`.
The rewrite hook leaves this proxy command unchanged. This bypasses RTK filtering,
not Hermes output limits or tool approvals. The adapter does not detect missing
information or automatically retry commands.

### Companion skill: output awareness

[`skills/rtk-output-awareness/SKILL.md`](skills/rtk-output-awareness/SKILL.md)
teaches agents when to request raw output, how to recover safely, and why
state-changing commands must not be repeated just to recover their output.

Installing the plugin's two files does **not** install or load this skill.
Copy the `skills/rtk-output-awareness` directory into the intended profile's
`skills/` directory, using that profile's resolved Hermes home. Install it
separately for each profile that needs it; do not assume profiles share skills.
Start a new session so the skill catalog can discover it without changing an
existing conversation's cached prompt.

A skill is loaded on demand, whereas `AGENTS.md` provides standing operating
instructions. For reliable discovery, optionally add this short pointer to the
profile's `AGENTS.md` after installing the skill:

> Terminal output may be filtered by RTK. Before terminal work, load
> `rtk-output-awareness`; use `rtk proxy <command>` when full output is required.

Neither the skill nor that pointer is installed automatically by this repository.
The pointer makes the procedure discoverable; it is not a guarantee that an agent
will detect every omission. Prefer raw output proactively when completeness matters.

---

## Local / remote limits

- **Local:** the adapter rewrites commands on the Hermes host. Works for any
  `terminal` command RTK understands.
- **Remote / container:** skipped by default. Opt a profile in only after RTK is
  installed on its target (see
  [Docker and remote backends](#docker-and-remote-backends-the-problem-and-our-fix)).
  The adapter does not probe target binaries and does not retry native commands
  after target execution fails.
- **No global performance guarantee:** RTK adds a subprocess call (bounded at
  2s) to every `terminal` command. On slow machines or cold caches this adds
  latency. There is no guarantee of net speedup; measure for your workload.

---

## Rollback

To disable rewriting entirely:

1. `hermes plugins disable rtk-rewrite` — stops the hook firing.
2. Or remove the plugin directory (see Uninstall above).
3. Or unset `RTK_BIN` and remove `rtk` from `PATH` — the adapter then fails
   open and every command runs unrewritten.
4. To stop rewriting for one Docker profile only, remove `docker` from its
   `plugins.entries.rtk-rewrite.settings.backends` (or delete the block).

No live Hermes state is modified by installing or removing this plugin beyond
the plugin registry entry.

---

## Development

```bash
# Unit tests need only the Python standard library, not RTK or Hermes:
python3 tests/run_tests.py adapter

# Stage the verified binary at .staging/rtk (see docs/EVIDENCE.md), then:
python3 tests/run_tests.py integration

# Dispatch requires the pinned Hermes checkout and its dependencies:
HERMES_TREE=/path/to/hermes /path/to/hermes/.venv/bin/python tests/run_tests.py dispatch
```

The runner exits nonzero for skipped tests, including missing prerequisites.
CI checks out the pinned Hermes revision and installs its frozen dependencies;
it does not substitute skipped dispatch tests for coverage.

See `docs/EVIDENCE.md` for the verified binary, checksums, and the exact
commands used to validate this release.
