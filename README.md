# hermes-rtk-rewrite with Docker support

A plugin for Hermes Agent. It rewrites `terminal` tool commands with [RTK](https://github.com/rtk-ai/rtk) before they run. RTK output is shorter, so the agent uses fewer tokens.

The plugin is a thin bridge. The RTK program does all the rewrite work (`rtk rewrite`). The plugin calls `rtk rewrite` and gives the result to Hermes. It does not copy any rewrite logic.

This plugin supports Docker and remote terminal backends. Without this support, a Docker agent can fail with `rtk: command not found`. The next section explains the problem.

- **Plugin version:** 0.1.1 (RTK has its own version number)
- **Supported:** Hermes >=0.21, RTK 1.1.0
- **RTK version:** v1.1.0 (tag `v1.1.0`, commit `753a3b0e939f2e4490fc24e3a1984010f40a103c`)
- **License:** Apache-2.0 (see `LICENSE` and `NOTICE`)

### Tested

| Hermes | RTK | Environment | Result | Evidence |
| --- | --- | --- | --- | --- |
| 0.22.7 (`b200f5bc60e3`) | 1.1.0 | `hermes-lab`, Docker | Pass | [Lab results](docs/EVIDENCE.md#hermes-lab-profile-tests), task `t_574c3813` |
| 0.23.0 (`2bd378ed1b8d`) | 1.1.0 | `hermes-lab`, fresh Docker container | Pass | [Lab results](docs/EVIDENCE.md#hermes-lab-profile-tests), task `t_cb72b2d4` |
| 0.23.0 (`2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`) | 1.1.0 | CI, Python 3.14, full test suite | 59 passed, zero skipped | [Verified CI run](https://github.com/petertaras/hermes-rtk-rewrite-with-docker-support/actions/runs/42148926407) |

The supported range is not a test matrix. The table lists the versions tested for this release.

---

## The Docker problem and the fix

### The problem

The plugin rewrites a command on the Hermes host. Then the command runs on the terminal backend. With a Docker backend, this is a different machine. This machine usually has no `rtk` program.

```
Hermes host:     cat a.txt   ->   rtk read a.txt
Docker backend:  bash: rtk: command not found   (exit 127)
```

The plugin cannot stop this error. The rewrite is already done when the error occurs.

We saw this problem in a real Docker agent. Of 839 terminal results, 52 contained `rtk: command not found`.

- 31 results had exit code 127. The agent had to retry.
- 16 results had exit code 0. In a chain of commands, the later parts ran after the first part failed. The agent saw incomplete output and no error.

A plugin cannot read the backend from `os.environ["TERMINAL_ENV"]`. One Hermes process can serve many profiles. Hermes keeps the terminal settings for each profile and does not copy them into the process environment. So the variable shows the profile that started the process. It does not show the profile that runs the command.

### The fix

The fix has three parts.

1. **Backend gate.** Before the plugin calls `rtk`, it asks Hermes which backend runs the command. It uses the same per-profile terminal settings as the terminal tool. The plugin rewrites the command only if its settings allow this backend. By default, only `local` is allowed. If the plugin cannot find the backend, it does not rewrite. You can lose some token savings. You never lose a working command.

2. **Opt-in for each profile.** A profile opts in when its container has `rtk`. Add this block to the profile settings:

   ```yaml
   # <profile>/config.yaml
   plugins:
     entries:
       rtk-rewrite-docker:
         settings:
           backends: [local, docker]   # or "all"
   ```

   Hermes reads these settings on each call. A profile without this block stays `local` only.

3. **A container layer with RTK.** The file [`container/Containerfile`](container/Containerfile) adds the RTK program to any base image. The program has a fixed version and a checksum (RTK v1.1.0, x86_64 Linux, static). To build an image:

   ```bash
   docker build -f container/Containerfile \
     --build-arg BASE_IMAGE=localhost/my-image:1.0 \
     -t localhost/my-image:1.0-rtk1.1.0 container/
   ```

   RTK telemetry is off in this image (`RTK_TELEMETRY_DISABLED=1`).

---

## Install RTK first

Complete this prerequisite before any plugin install method. The plugin does not download or include the RTK binary.
Use RTK 1.1.0. The verified archive and checksum are in [docs/EVIDENCE.md](docs/EVIDENCE.md#pins).

- **Local backend:** Install RTK on the Hermes host. The host needs it for both `rtk rewrite` and rewritten commands.
- **Docker backend:** Install RTK on the Hermes host and inside the backend image. Use [`container/Containerfile`](container/Containerfile) as described in "The fix".
- **Remote backend:** Install RTK on the Hermes host and in the remote environment before opt-in.

Check the host installation:

```bash
rtk --version
# Expected: rtk 1.1.0
```

For Docker, check the image too:

```bash
docker run --rm --entrypoint rtk <image> --version
# Expected: rtk 1.1.0
```

For remote backends, run `rtk --version` in the remote environment too.
A host install alone does not add RTK to Docker or remote backends.
Do not opt in a backend until its RTK check passes.

To select a different host executable, set `RTK_BIN` to its absolute path.
This setting does not change how rewritten commands find `rtk` on the backend.

## Install

### Install from the Hermes plugin catalog

First, complete [Install RTK first](#install-rtk-first) for your backend.
Then install the catalog entry in the profile that needs the plugin:

```bash
hermes -p <profile> plugins install rtk-rewrite-docker --enable
```

The [Hermes plugin catalog](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/rtk-rewrite-docker.yaml) pins a reviewed commit, not the latest `master` or release tag.
The current entry installs version 0.1.1 at `61701dd6e14c2392f7cebcac68f46680f3a428ee`.
For the next release, update the catalog entry's `version` and `sha` on upstream Hermes after review.
A new release in this repository alone does not change what the catalog installs.

The GitHub and file-copy methods below are alternatives.
All methods need RTK on the Hermes host. Docker and remote backends also need RTK and explicit opt-in, as described above.

### Install from GitHub

First, complete [Install RTK first](#install-rtk-first) for your backend.
Install the plugin in the profile that uses the Docker backend. Pin the install to one commit:

```bash
hermes -p <profile> plugins install petertaras/hermes-rtk-rewrite-with-docker-support \
  --ref <40-char-commit-sha> --force --enable
```

Two things can stop an install. Neither one is a Docker problem.

1. **The plugin scanner blocks the install.** The scanner gives the verdict `caution` and the reason "community source". The findings are pattern matches on files in this repository:
   - the checksum check in the CI file (`echo "<sha256>  file" | sha256sum -c`)
   - the call `subprocess.run([rtk, "rewrite", ...])` in `__init__.py`
   - the test files that run `git` and `rtk` in a temporary repository
   - README text about `os.environ`

   Read the findings. Then add `--force`. Always pin `--ref` to a full commit hash. Then you install exactly the code that you reviewed.

2. **The plugin has no `pyproject.toml`. This is on purpose.** Hermes treats a `pyproject.toml` file as a list of Python dependencies. Then it asks for consent in a prompt. In a non-interactive shell, the install fails with `Reinstall declined: dependency install skipped (non-interactive)`. The package name also becomes a member of the shared Hermes environment. A second profile that installs the plugin then fails with `Two workspace members are both named ...`. This plugin uses only the Python standard library. It needs neither, so you can install it in many profiles without a prompt.

### Install by copying files

First, complete [Install RTK first](#install-rtk-first) for your backend.
This method is for the `local` backend. The plugin is two files: `plugin.yaml` and `__init__.py`.

```bash
mkdir -p ~/.hermes/plugins/rtk-rewrite-docker
cp plugin.yaml __init__.py ~/.hermes/plugins/rtk-rewrite-docker/
hermes plugins enable rtk-rewrite-docker
hermes plugins list
```

To update, copy the two files again. If needed, enable the plugin again.

---

## Set up a Docker profile

1. Build an image that contains `rtk`. Use the build command from "The fix" above.
2. Set the image of the profile:

   ```bash
   hermes -p <profile> config set terminal.docker_image <image>
   ```

3. Opt in the Docker backend:

   ```bash
   hermes -p <profile> config set plugins.entries.rtk-rewrite-docker.settings.backends '["local","docker"]'
   ```

4. Remove the existing container of the profile. A running container keeps its old image.
5. Start a new session.
6. Ask the agent to run `git status` in a Git repository. RTK output is shorter than the normal Git output.
7. Ask the agent to run `command -v rtk`. Make sure that it shows the path of the program in the container.

If the image has no `rtk`, do not opt in. Docker commands then stay unchanged. The local backend keeps its existing policy.

### Upgrade to 0.1.1

1. Install RTK 1.1.0 on the host, or point `RTK_BIN` to the verified 1.1.0 executable.
2. For Docker profiles, rebuild the existing base image with `container/Containerfile`.
3. Install the plugin with the pinned commit command in "Install from GitHub".
4. Keep the existing backend settings. Opt in to Docker only after the new image contains RTK 1.1.0.
5. Replace the old profile container and start a new session.
6. Check `rtk --version` on the host and through the profile terminal. Both must report `rtk 1.1.0`.

The plugin update does not update host binaries, rebuild images, or replace running containers.

### Settings

| Setting | Values | Default | Meaning |
| --- | --- | --- | --- |
| `plugins.entries.rtk-rewrite-docker.settings.backends` | A list of backend names, or `all` | `[local]` | The backends where the plugin rewrites commands |

---

## What RTK rewrites

RTK rewrites each command in a chain. A chain uses `&&`, `||`, or `;`.

| Command | Result |
| --- | --- |
| `git status` | `rtk git status` |
| `cd /tmp && git status` | `cd /tmp && rtk git status` |
| `ls -la && git log -3` | `rtk ls -la && rtk git log -3` |
| `FOO=1 git status` | `FOO=1 rtk git status` |
| `cat a.txt \| grep x` | `cat a.txt \| rtk grep x` |

RTK handles shell safety as follows:

- RTK leaves stdout producers unchanged before semantic consumers, for example `git status | grep branch`. It can rewrite the last stage, for example `git diff | rtk grep foo`. With a display-only consumer, RTK can rewrite the producer too: `git status | cat` becomes `rtk git status | cat`.
- A command whose output goes into a file, for example `git status > out.txt`.
- A command that has a heredoc.
- A command that has command substitution, for example `echo $(git status)`.
- A command that RTK has no filter for, for example `echo hi`.
- A command that starts with `rtk`. It stays as it is.

The results come from `rtk rewrite` v1.1.0. A later RTK version can give different results.

---

## When the plugin does not rewrite

The plugin fails open. Hermes then runs the original command, unchanged. This happens in these cases:

- The `rtk` program is missing, or the plugin cannot find it.
- The backend is not opted in, or the plugin cannot find the backend.
- `rtk rewrite` takes more than 2 seconds.
- `rtk rewrite` returns an unexpected exit code.
- Any other error occurs in the plugin.

Hermes has its own fail-closed rule. A `pre_tool_call` callback that takes longer than `plugins.hook_callback_timeout` (30 seconds by default) is stopped. Then Hermes blocks the tool. The 2-second limit of the plugin is much lower than this limit. A stuck RTK process cannot block the tool gate.

### RTK exit codes

- `0` or `3`: RTK has a rewrite on stdout. The plugin gives Hermes a `modify` directive. It does this only if the rewritten command is different.
- `1` or `2`: RTK has no rewrite. The plugin returns `None`.
- Any other code: the plugin logs a warning and returns `None`.

These codes follow the RTK documentation. The file `docs/EVIDENCE.md` shows what RTK v1.1.0 returned in tests.

---

## Output filtering

RTK replaces commands with its own `rtk read`, `rtk grep`, and similar commands. These commands filter and shorten the output. This is the goal of RTK, because short output uses fewer tokens. But the output can differ from the normal output of the command.

For the full native output, use `rtk bypass <command>`, for example `rtk bypass git diff`. The plugin does not change a command that starts with `rtk bypass`. This command skips the RTK filter. It does not skip Hermes output limits or tool approvals. The plugin does not detect missing information. It does not retry commands.

### Failure output

RTK 1.1.0 has a recall store for output recovery. The default mode is `sqlite`.
For filters with failure recovery, RTK saves the full captured output and prints a hint with a content hash.
Use the hash from that hint in the same backend and as the same user:

```bash
rtk recall <hash>
rtk recall <hash> --full
```

The first command returns the missing output. The `--full` option returns the complete stored output.
Recall reads stored output. It does not run the failed command again.
Do not re-run a failed command to recover its output, especially a command that changes state.

**Keep recall enabled.** `RTK_RECALL=0` or `rtk config recall disabled` prevents RTK from saving new recovery output.
Filtered output from those runs cannot be recovered through recall.
Check the mode in the backend where commands run:

```bash
rtk config recall
# Default: recall mode: sqlite
```

Recall is not a guarantee for every failure. Some filters do not save failure output, and short failures can have no recall hint.
Storage limits and retention also apply. The default SQLite limits are 10 MiB per entry, 200 entries and 30 days.
For commands that require complete output from the start, use `rtk bypass <command>`.

**Docker persistence:** The recall store belongs to the container, not the Hermes host that runs `rtk rewrite`.
On Linux, its default location is `~/.local/share/rtk/recall.db` for the user that runs the command.
Mount that user's `~/.local/share/rtk` directory on persistent storage before commands run.
Without this mount, a container reset or replacement loses the store.
If you override `XDG_DATA_HOME` or `RTK_RECALL_DB`, persist the actual store location instead.

See the [RTK 1.1.0 recall documentation](https://github.com/rtk-ai/rtk/blob/753a3b0e939f2e4490fc24e3a1984010f40a103c/docs/guide/getting-started/configuration.md#recall-system).

### Companion skill

The repository has a companion skill: [`skills/rtk-output-awareness/SKILL.md`](skills/rtk-output-awareness/SKILL.md). It teaches agents when to ask for raw output and how to recover safely. It also tells agents not to repeat a command that changes state only to get its output again.

The plugin install does not install this skill. Hermes does not share skills between profiles. Install the skill in each profile that needs it:

1. Copy the directory `skills/rtk-output-awareness` into the `skills/` directory of the profile. Use the Hermes home of that profile.
2. Start a new session. The skill catalog then finds the skill. An existing conversation keeps its cached prompt.

A skill loads on request. `AGENTS.md` gives standing instructions. For reliable discovery, you can add this pointer to the `AGENTS.md` of the profile:

> RTK can filter terminal output. Before terminal work, load `rtk-output-awareness`.
> If you need full output, use `rtk bypass <command>`.

The pointer helps the agent find the skill. It does not guarantee that the agent detects every missing output. If you need complete output, ask for raw output from the start.

---

## Limits

- The container layer supports x86_64 Linux only. It uses a static musl release file.
- The plugin uses Hermes internals (`tools.terminal_tool` and `tools.terminal_scope`). These are not a documented plugin interface. If they change, the plugin cannot find the backend and stops rewriting. This is safe. Run the dispatch tests after each Hermes update.
- If a profile is opted in but its image has no `rtk`, the old error returns. The plugin does not look inside the backend.
- The plugin adds one `rtk rewrite` call (2 seconds at most) to each terminal command. On a slow machine, this adds delay. We do not promise a speed gain. Measure your own workload.

---

## Stop or remove the plugin

To stop rewriting, use one of these methods:

1. Run `hermes plugins disable rtk-rewrite-docker`. The hook stops.
2. To stop one Docker profile only, remove `docker` from its `plugins.entries.rtk-rewrite-docker.settings.backends` list. You can also delete the block.
3. Remove `RTK_BIN` and remove `rtk` from `PATH` on the host. The plugin then fails open, and every command runs unchanged.

To remove the plugin, disable it. Then delete the directory `plugins/rtk-rewrite-docker` in the Hermes home of the profile.

---

## How this plugin differs from the upstream RTK hook

RTK v1.1.0 has its own Hermes hook: `hooks/hermes/rtk-rewrite/__init__.py`. Its `pre_tool_call` callback does this:

```python
rewritten = result.stdout.strip()
if rewritten and rewritten != command:
    args["command"] = rewritten   # changes the dictionary of the hook
# the callback returns None
```

In the tested Hermes version, this change does reach the tool call. Hermes gives the same argument dictionary to the hook. It uses this dictionary when the callback returns no `modify` directive. A test confirmed that the upstream hook rewrites `cat a.txt` to `rtk read a.txt`.

This plugin returns a `modify` directive instead: `{"action": "modify", "args": {"command": rewritten}}`. Hermes merges the directive into the original arguments. The other fields stay the same (`cwd`, `workdir`, `timeout`, `background`). Both methods rewrote the test command. The explicit directive is a design choice. It does not repair a broken upstream hook.

The upstream authors keep their credit (see `NOTICE`). This plugin adds the backend gate, the `RTK_BIN` setting, and a 2-second timeout.

---

## How we tested the plugin

- **Unit tests** cover the gate, the setting parser, and the backend detection. They include "an unknown backend never rewrites" and "the settings are read on each call".
- **Dispatch tests** use the real Hermes plugin code. Each profile gets its terminal settings the same way the gateway sets them. These tests include the multiplexer case: the process environment says `local`, and the profile says `docker`. They also alternate between `local` and `docker` profiles and check that nothing leaks.
- **Container tests.** The RTK v1.1.0 layer builds from a digest-pinned Node base. Real Hermes dispatch produces `rtk read`, and that command runs inside a network-disabled container. The tests also cover the default Docker gate, raw bypass, exit codes and the telemetry opt-out.
- **Release tests.** All 59 tests pass with Hermes v0.23.0 and RTK v1.1.0. No tests are skipped.
- **Profile tests.** `hermes-lab` passed on Hermes 0.22.7 and 0.23.0 with RTK 1.1.0 on the host and in Docker. Both runs proved the default Docker gate and opted-in rewrites. Neither run returned `rtk: command not found`. The 0.23.0 run used a fresh container. See the [lab evidence](docs/EVIDENCE.md#hermes-lab-profile-tests).

---

## Development

Unit tests need only Python 3. They do not need RTK or Hermes:

```bash
python3 tests/run_tests.py adapter
```

For the integration tests, put the verified RTK binary in `.staging/rtk` (see `docs/EVIDENCE.md`). Then run:

```bash
python3 tests/run_tests.py integration
```

The dispatch tests need the pinned Hermes v0.23.0 checkout and Python 3.14. Build its frozen environment through PM:

```bash
PYTHONPATH="$PWD/.staging/hermes" python3 -m pm.build_env \
  --source .staging/hermes --out .staging/hermes-venv14 \
  --python "$(command -v python3)" --cache .staging/pm-cache
HERMES_TREE="$PWD/.staging/hermes" .staging/hermes-venv14/bin/python tests/run_tests.py dispatch
```

The full suite also needs a built container image:

```bash
docker build -f container/Containerfile \
  --build-arg BASE_IMAGE=docker.io/library/node@sha256:18a6d90fd60542269850c80ca03b29b1d9b14d6b221ac7ee8e35b0d33ef904bd \
  -t localhost/hermes-lab-rtk-test:1.1.0 container/
HERMES_TREE="$PWD/.staging/hermes" RTK_TEST_IMAGE=localhost/hermes-lab-rtk-test:1.1.0 \
  .staging/hermes-venv14/bin/python tests/run_tests.py all
```

Use an isolated `HERMES_HOME` and `HERMES_RUNTIME_DIR` for development. Never build against a live profile.

The test runner exits with an error when a test is skipped. A missing prerequisite counts as missing coverage. CI checks out the pinned Hermes commit, builds frozen dependencies through PM, builds the container layer and runs every suite.

The file `docs/EVIDENCE.md` has the verified binary, the checksums, and the exact commands used to test this release.
