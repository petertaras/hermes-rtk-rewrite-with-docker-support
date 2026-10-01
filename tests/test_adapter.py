"""Unit tests for the rtk-rewrite adapter (subprocess mocked).

These cover every branch of ``_pre_tool_call`` without needing a real RTK
binary: unsupported tools, empty args, existing rtk commands, rewrite return
codes, missing binary, timeout, and unexpected failure.
"""

import subprocess
import types
import unittest
from unittest import mock

from _helpers import load_plugin_module


class _FakeResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class AdapterUnitTest(unittest.TestCase):
    def setUp(self):
        self.m = load_plugin_module()
        self.m._reset_rtk_cache()
        # Binary discovery is not under test here; subprocess calls are mocked.
        binary = mock.patch.object(self.m, "_rtk_bin", return_value="/fake/rtk")
        binary.start()
        self.addCleanup(binary.stop)
        # Backend selection is covered by BackendGateTest; these tests assume local.
        backend = mock.patch.object(self.m, "_active_backend", return_value="local")
        backend.start()
        self.addCleanup(backend.stop)

    def _call(self, tool_name="terminal", args=None, **kwargs):
        return self.m._pre_tool_call(tool_name=tool_name, args=args, **kwargs)

    def _fake_run(self, code, stdout="", stderr=""):
        return mock.patch.object(
            self.m.subprocess, "run",
            lambda *a, **k: _FakeResult(code, stdout, stderr),
        )

    # ── unsupported tools / empty args ──────────────────────────────────────

    def test_unsupported_tool_returns_none(self):
        with self._fake_run(3, "rtk ls"):
            self.assertIsNone(self._call(tool_name="read_file", args={"path": "x"}))

    def test_non_dict_args_returns_none(self):
        with self._fake_run(3, "rtk ls"):
            self.assertIsNone(self._call(args="not-a-dict"))

    def test_empty_command_returns_none(self):
        with self._fake_run(3, "rtk ls"):
            self.assertIsNone(self._call(args={"command": ""}))
            self.assertIsNone(self._call(args={"command": "   "}))

    def test_missing_command_key_returns_none(self):
        with self._fake_run(3, "rtk ls"):
            self.assertIsNone(self._call(args={"cwd": "/tmp"}))

    # ── rewrite return codes ────────────────────────────────────────────────

    def test_accepted_rewrite_codes_return_modify(self):
        for code in (0, 3):
            with self.subTest(code=code), self._fake_run(code, "rtk read a.txt"):
                result = self._call(args={"command": "cat a.txt"})
                self.assertEqual(
                    result, {"action": "modify", "args": {"command": "rtk read a.txt"}}
                )

    def test_passthrough_codes_return_none(self):
        for code in (1, 2):
            with self.subTest(code=code), self._fake_run(code, "rtk read a.txt"):
                self.assertIsNone(self._call(args={"command": "cat a.txt"}))

    def test_unexpected_failure_code_returns_none(self):
        with self._fake_run(127, "", "rtk: boom"):
            self.assertIsNone(self._call(args={"command": "cat a.txt"}))

    # ── existing rtk commands / no-op rewrites ─────────────────────────────

    def test_rewrite_identical_to_command_returns_none(self):
        with self._fake_run(3, "rtk status"):
            self.assertIsNone(self._call(args={"command": "rtk status"}))

    def test_empty_rewrite_stdout_returns_none(self):
        with self._fake_run(3, "   "):
            self.assertIsNone(self._call(args={"command": "cat a.txt"}))

    # ── missing binary / timeout / failure ─────────────────────────────────

    def test_missing_binary_returns_none(self):
        with mock.patch.object(self.m, "_rtk_bin", lambda: None):
            self.assertIsNone(self._call(args={"command": "cat a.txt"}))

    def test_timeout_returns_none(self):
        def _hang(*a, **k):
            raise subprocess.TimeoutExpired(cmd="rtk", timeout=2)

        with mock.patch.object(self.m.subprocess, "run", _hang):
            self.assertIsNone(self._call(args={"command": "cat a.txt"}))

    def test_unexpected_exception_returns_none(self):
        def _boom(*a, **k):
            raise RuntimeError("unexpected")

        with mock.patch.object(self.m.subprocess, "run", _boom):
            self.assertIsNone(self._call(args={"command": "cat a.txt"}))

    # ── merge contract: only command is returned, other args preserved ─────

    def test_modify_directive_returns_only_command_key(self):
        """The returned directive must carry only ``command`` so Hermes' shallow
        merge preserves cwd/workdir, timeout and background."""
        with self._fake_run(3, "rtk read a.txt"):
            result = self._call(
                args={"command": "cat a.txt", "cwd": "/work", "timeout": 60, "background": True},
            )
        self.assertEqual(result, {"action": "modify", "args": {"command": "rtk read a.txt"}})

    # ── subprocess invocation shape ─────────────────────────────────────────

    def test_subprocess_called_with_rtk_rewrite_and_no_shell(self):
        captured = {}

        def _fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return _FakeResult(3, "rtk read a.txt")

        with mock.patch.object(self.m.subprocess, "run", _fake_run), \
             mock.patch.object(self.m, "_rtk_bin", lambda: "/opt/rtk"):
            self._call(args={"command": "cat a.txt"})
        self.assertEqual(captured["argv"], ["/opt/rtk", "rewrite", "cat a.txt"])
        self.assertIs(captured["kwargs"]["shell"], False)
        self.assertEqual(captured["kwargs"]["timeout"], self.m.RTK_REWRITE_TIMEOUT_SECONDS)

    # ── register() gating ───────────────────────────────────────────────────

    def test_register_skips_hook_when_rtk_missing(self):
        with mock.patch.object(self.m, "_rtk_bin", lambda: None):
            self.m._reset_rtk_cache()
            ctx = types.SimpleNamespace(register_hook=lambda *a, **k: None)
            self.m.register(ctx)  # must not raise and must not register
            self.assertIs(self.m._check_rtk(), False)

    def test_register_registers_hook_when_rtk_present(self):
        with mock.patch.object(self.m, "_rtk_bin", lambda: "/opt/rtk"):
            self.m._reset_rtk_cache()
            registered = []

            class _Ctx:
                def register_hook(self, name, cb):
                    registered.append((name, cb))

            self.m.register(_Ctx())
        self.assertEqual([name for name, _ in registered], ["pre_tool_call"])
        self.assertTrue(callable(registered[0][1]))


class BackendGateTest(unittest.TestCase):
    """The rewritten command runs on the terminal backend, so ``rtk`` must exist there."""

    def setUp(self):
        self.m = load_plugin_module()
        self.m._reset_rtk_cache()
        binary = mock.patch.object(self.m, "_rtk_bin", return_value="/fake/rtk")
        binary.start()
        self.addCleanup(binary.stop)

    def _run(self, backend, allowed=None, command="cat a.txt"):
        calls = []

        def _fake_run(argv, **kwargs):
            calls.append(argv)
            return _FakeResult(3, "rtk read a.txt")

        with mock.patch.object(self.m, "_active_backend", return_value=backend), \
             mock.patch.object(self.m.subprocess, "run", _fake_run):
            result = self.m._pre_tool_call(
                tool_name="terminal", args={"command": command}, _allowed_backends=allowed
            )
        return result, calls

    def test_local_backend_rewrites_by_default(self):
        result, calls = self._run("local")
        self.assertEqual(result, {"action": "modify", "args": {"command": "rtk read a.txt"}})
        self.assertEqual(len(calls), 1)

    def test_container_and_remote_backends_are_not_rewritten_by_default(self):
        for backend in ("docker", "podman", "ssh", "modal", "singularity", "daytona"):
            with self.subTest(backend=backend):
                result, calls = self._run(backend)
                self.assertIsNone(result)
                # RTK is not even consulted: no wasted subprocess for a skipped command.
                self.assertEqual(calls, [])

    def test_unknown_backend_is_not_rewritten(self):
        for backend in (None, ""):
            with self.subTest(backend=backend):
                result, calls = self._run(backend, allowed={"local", "docker"})
                self.assertIsNone(result)
                self.assertEqual(calls, [])

    def test_docker_rewrites_when_profile_opts_in(self):
        result, calls = self._run("docker", allowed={"local", "docker"})
        self.assertEqual(result, {"action": "modify", "args": {"command": "rtk read a.txt"}})
        self.assertEqual(len(calls), 1)

    def test_opt_in_is_per_backend(self):
        result, _ = self._run("ssh", allowed={"local", "docker"})
        self.assertIsNone(result)

    def test_all_opts_in_every_known_backend(self):
        result, _ = self._run("ssh", allowed={"all"})
        self.assertIsNotNone(result)

    def test_all_does_not_rewrite_when_backend_unknown(self):
        result, calls = self._run(None, allowed={"all"})
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_backend_names_are_case_insensitive_in_settings(self):
        self.assertEqual(self.m._parse_allowed_backends(["Local", " DOCKER "]), {"local", "docker"})

    def test_setting_accepts_comma_separated_string(self):
        self.assertEqual(self.m._parse_allowed_backends("local, docker"), {"local", "docker"})

    def test_unusable_setting_falls_back_to_local_only(self):
        for value in (None, 5, {}, [], "", "  ", [None, ""]):
            with self.subTest(value=value):
                self.assertEqual(
                    self.m._parse_allowed_backends(value), self.m.DEFAULT_ALLOWED_BACKENDS
                )

    def test_default_is_local_only(self):
        self.assertEqual(self.m.DEFAULT_ALLOWED_BACKENDS, frozenset({"local"}))

    def test_settings_are_read_per_call_from_the_context(self):
        values = [["local"], ["local", "docker"]]

        class _Ctx:
            def get_config(self, key, default=None):
                assert key == "backends"
                return values.pop(0)

        hook = self.m._bind_to_context(_Ctx())
        with mock.patch.object(self.m, "_active_backend", return_value="docker"), \
             mock.patch.object(self.m.subprocess, "run", lambda *a, **k: _FakeResult(3, "rtk read a.txt")):
            first = hook(tool_name="terminal", args={"command": "cat a.txt"}, task_id="t")
            second = hook(tool_name="terminal", args={"command": "cat a.txt"}, task_id="t")
        self.assertIsNone(first)
        self.assertEqual(second, {"action": "modify", "args": {"command": "rtk read a.txt"}})

    def test_settings_read_failure_falls_back_to_local_only(self):
        class _Ctx:
            def get_config(self, key, default=None):
                raise RuntimeError("config unreadable")

        self.assertEqual(self.m._read_allowed_backends(_Ctx()), self.m.DEFAULT_ALLOWED_BACKENDS)

    def test_hook_accepts_extra_hermes_kwargs(self):
        hook = self.m._bind_to_context(types.SimpleNamespace(get_config=lambda k, d=None: None))
        with mock.patch.object(self.m, "_active_backend", return_value="local"), \
             mock.patch.object(self.m.subprocess, "run", lambda *a, **k: _FakeResult(3, "rtk ls")):
            result = hook(
                tool_name="terminal", args={"command": "ls"},
                session_id="s", task_id="t", tool_call_id="c", turn_id="u", api_request_id="a",
            )
        self.assertEqual(result, {"action": "modify", "args": {"command": "rtk ls"}})


class ActiveBackendTest(unittest.TestCase):
    """Backend detection must follow Hermes' per-profile policy, not os.environ."""

    def setUp(self):
        self.m = load_plugin_module()

    def _stub_modules(self, **modules):
        patcher = mock.patch.dict("sys.modules", modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _terminal_tool(self, env_type=None, raises=None):
        def _get_env_config():
            if raises:
                raise raises
            return {"env_type": env_type}

        return types.SimpleNamespace(_get_env_config=_get_env_config)

    def test_uses_the_terminal_tools_resolved_backend(self):
        self._stub_modules(**{"tools": types.ModuleType("tools"),
                              "tools.terminal_tool": self._terminal_tool("docker")})
        with mock.patch.dict("os.environ", {"TERMINAL_ENV": "local"}):
            self.assertEqual(self.m._active_backend(), "docker")

    def test_normalises_case_and_whitespace(self):
        self._stub_modules(**{"tools": types.ModuleType("tools"),
                              "tools.terminal_tool": self._terminal_tool("  Docker ")})
        self.assertEqual(self.m._active_backend(), "docker")

    def test_falls_back_to_terminal_scope_when_config_lookup_fails(self):
        self._stub_modules(**{
            "tools": types.ModuleType("tools"),
            "tools.terminal_tool": self._terminal_tool(raises=RuntimeError("boom")),
            "tools.terminal_scope": types.SimpleNamespace(terminal_env=lambda n, d="": "ssh"),
        })
        self.assertEqual(self.m._active_backend(), "ssh")

    def test_returns_none_when_hermes_internals_are_unavailable(self):
        # Never guess: an unknown backend means "do not rewrite".
        self._stub_modules(**{"tools": None, "tools.terminal_tool": None, "tools.terminal_scope": None})
        self.assertIsNone(self.m._active_backend())

    def test_returns_none_when_both_lookups_fail(self):
        def _boom(name, default=""):
            raise RuntimeError("refusal scope")

        self._stub_modules(**{
            "tools": types.ModuleType("tools"),
            "tools.terminal_tool": self._terminal_tool(raises=RuntimeError("boom")),
            "tools.terminal_scope": types.SimpleNamespace(terminal_env=_boom),
        })
        self.assertIsNone(self.m._active_backend())

    def test_empty_backend_is_unknown(self):
        self._stub_modules(**{"tools": types.ModuleType("tools"),
                              "tools.terminal_tool": self._terminal_tool("")})
        self.assertIsNone(self.m._active_backend())


if __name__ == "__main__":
    unittest.main()
