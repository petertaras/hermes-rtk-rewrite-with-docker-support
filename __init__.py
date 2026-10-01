"""Hermes plugin adapter for RTK command rewriting.

All rewrite logic lives in RTK's Rust ``rtk rewrite`` command; this module
only bridges Hermes ``pre_tool_call`` payloads to that command and fails open.

Upstream provenance: this file is derived from the RTK project's Hermes hook
(``hooks/hermes/rtk-rewrite/__init__.py``, Apache-2.0, RTK Contributors). The
rewritten command is returned to Hermes as an explicit ``modify`` directive
instead of being assigned to the hook's ``args`` dict, and the adapter adds a
terminal-backend gate for Docker and remote targets. See NOTICE and README.
"""

import os
import shutil
import subprocess
import sys

# Adapter version is independent of the RTK executable version (see plugin.yaml).
ADAPTER_VERSION = "0.1.0"

# Execution backends whose commands may be rewritten. The rewrite runs on the
# Hermes host, but the rewritten command (``rtk read a.txt``) executes on the
# terminal backend, so ``rtk`` must exist THERE. Only the host is assumed to have
# it. A container or remote profile opts in per profile with
# ``plugins.entries.rtk-rewrite-docker.settings.backends`` once its image carries RTK.
DEFAULT_ALLOWED_BACKENDS = frozenset({"local"})
BACKENDS_SETTING = "backends"

# RTK rewrite exit codes. ``{0, 3}`` mean "a rewrite is available on stdout";
# ``{1, 2}`` mean "no rewrite" (passthrough). Kept as documented unless a pinned
# RTK version proves otherwise (see docs/EVIDENCE.md).
ACCEPTED_REWRITE_RETURN_CODES = {0, 3}
EXPECTED_PASSTHROUGH_RETURN_CODES = {1, 2}

# Subprocess timeout must stay well under plugins.hook_callback_timeout
# (default 30s): a hung pre_tool_call fails closed in Hermes, so we bound RTK
# hard and fail open to the original command on timeout.
RTK_REWRITE_TIMEOUT_SECONDS = 2.0

_rtk_available = None
_rtk_missing_warned = False


def register(ctx):
    """Register the Hermes pre-tool callback."""
    if not _check_rtk():
        return

    ctx.register_hook("pre_tool_call", _bind_to_context(ctx))


def _bind_to_context(ctx):
    """Return the hook callback, reading this profile's settings at call time.

    Settings are read per call, not at registration: one Hermes process can
    serve several profiles, each with its own ``plugins.entries`` block.
    """

    def _hook(tool_name=None, args=None, **kwargs):
        return _pre_tool_call(
            tool_name=tool_name,
            args=args,
            _allowed_backends=lambda: _read_allowed_backends(ctx),
            **kwargs,
        )

    return _hook


def _parse_allowed_backends(value):
    """Normalise a ``backends`` setting; anything unusable means the default."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple, set, frozenset)):
        return DEFAULT_ALLOWED_BACKENDS
    names = {str(v).strip().lower() for v in value if v is not None and str(v).strip()}
    return frozenset(names) or DEFAULT_ALLOWED_BACKENDS


def _read_allowed_backends(ctx):
    try:
        return _parse_allowed_backends(ctx.get_config(BACKENDS_SETTING, None))
    except Exception:
        return DEFAULT_ALLOWED_BACKENDS


def _active_backend():
    """Return the backend that will run the next terminal command, or ``None``.

    Hermes keeps terminal policy per profile and does not mirror it into the
    process environment when one process serves several profiles, so reading
    ``os.environ["TERMINAL_ENV"]`` here would report the launch profile's
    backend for everyone. Ask Hermes the same question the terminal tool asks.
    ``None`` means "could not tell", and the caller skips the rewrite.
    """
    value = None
    try:
        from tools.terminal_tool import _get_env_config

        value = _get_env_config().get("env_type")
    except Exception:
        try:
            from tools.terminal_scope import terminal_env

            value = terminal_env("TERMINAL_ENV", "local")
        except Exception:
            return None
    value = str(value or "").strip().lower()
    return value or None


def _backend_allowed(backend, allowed):
    """True when *backend* is known and opted in. Unknown never rewrites."""
    if not backend:
        return False
    return "all" in allowed or backend in allowed


def _rtk_bin():
    """Return the RTK binary path: ``RTK_BIN`` env override, else PATH lookup.

    ``RTK_BIN`` is honoured so tests and constrained deployments can point the
    adapter at a specific verified binary without mutating PATH. It is not a
    rewrite-logic feature; when unset the adapter behaves exactly as upstream.
    """
    override = (os.environ.get("RTK_BIN") or "").strip()
    if override:
        return override
    return shutil.which("rtk")


def _check_rtk():
    """Return whether the rtk binary is resolvable, warning once when missing."""
    global _rtk_available, _rtk_missing_warned

    if _rtk_available is None:
        _rtk_available = _rtk_bin() is not None

    if not _rtk_available and not _rtk_missing_warned:
        _warn("rtk binary not found in PATH; Hermes hook not registered")
        _rtk_missing_warned = True

    return _rtk_available


def _reset_rtk_cache():
    """Forget the cached availability result (test helper).

    Lets tests re-resolve the binary after changing ``RTK_BIN`` or PATH without
    importing a fresh module. Not part of the runtime hot path.
    """
    global _rtk_available, _rtk_missing_warned
    _rtk_available = None
    _rtk_missing_warned = False


def _pre_tool_call(tool_name=None, args=None, _allowed_backends=None, **_kwargs):
    """Return a Hermes modify directive when RTK provides a rewrite.

    Hermes shallow-merges the returned ``args`` into the original tool args, so
    returning only ``{"command": rewritten}`` preserves ``cwd``/``workdir``,
    ``timeout`` and ``background``. Fails open (returns ``None``) on missing
    binary, timeout, and unexpected RTK failure so the original command runs.

    Commands are left untouched when the terminal backend is not opted in
    (default: local only) or cannot be determined, because the rewritten
    command needs ``rtk`` on the machine that runs it.
    """
    try:
        if tool_name != "terminal" or not isinstance(args, dict):
            return

        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return

        allowed = _allowed_backends() if callable(_allowed_backends) else _allowed_backends
        if allowed is None:
            allowed = DEFAULT_ALLOWED_BACKENDS
        if not _backend_allowed(_active_backend(), allowed):
            return

        rtk_bin = _rtk_bin()
        if not rtk_bin:
            # Binary vanished after registration; fail open to the original command.
            return

        try:
            result = subprocess.run(
                [rtk_bin, "rewrite", command],
                shell=False,
                timeout=RTK_REWRITE_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            _warn("rtk rewrite timed out")
            return

        if result.returncode not in ACCEPTED_REWRITE_RETURN_CODES:
            if result.returncode not in EXPECTED_PASSTHROUGH_RETURN_CODES:
                details = f"rtk rewrite failed with exit {result.returncode}"
                stderr = result.stderr.strip()
                if stderr:
                    details = f"{details}: {stderr}"
                _warn(details)
            return

        rewritten = result.stdout.strip()
        if rewritten and rewritten != command:
            return {"action": "modify", "args": {"command": rewritten}}
    except Exception as e:
        _warn(str(e))
        return


def _warn(message):
    print(f"rtk: hermes plugin warning: {message}", file=sys.stderr)
