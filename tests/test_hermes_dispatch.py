"""Real Hermes plugin discovery + pre_tool_call dispatch tests in isolated homes.

These exercise the actual Hermes ``hermes_cli.plugins`` machinery: the plugin is
installed into a throwaway HERMES_HOME, discovered, loaded, and dispatched
through ``_dispatch_pre_tool_call_hooks``. They verify the real shallow-merge
contract (cwd/workdir, timeout, background preserved) and that profile switching
(A -> B -> A) does not break dispatch.

Requires a real RTK binary (the staged, verified one) so the plugin registers
and produces a rewrite, and a local Hermes tree so ``hermes_cli`` is importable.
Missing prerequisites are reported as skips, which the suite runner rejects.
"""

import builtins
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from _helpers import REPO_ROOT, STAGED_RTK


def _make_home(base: Path, name: str) -> Path:
    """Create an isolated HERMES_HOME with the rtk-rewrite-docker plugin installed+enabled."""
    home = base / name
    plugin_dir = home / "plugins" / "rtk-rewrite-docker"
    plugin_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "plugin.yaml", plugin_dir / "plugin.yaml")
    shutil.copy2(REPO_ROOT / "__init__.py", plugin_dir / "__init__.py")
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - rtk-rewrite-docker\n")
    return home


def _make_docker_home(base: Path, name: str, opt_in: bool) -> Path:
    """A profile whose terminal backend is docker, optionally opted in to rewrites."""
    home = _make_home(base, name)
    plugins = "plugins:\n  enabled:\n    - rtk-rewrite-docker\n"
    if opt_in:
        plugins += (
            "  entries:\n    rtk-rewrite-docker:\n      settings:\n"
            "        backends: [local, docker]\n"
        )
    (home / "config.yaml").write_text(plugins + "terminal:\n  backend: docker\n")
    return home


class HermesDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not STAGED_RTK.is_file():
            raise unittest.SkipTest("staged RTK binary not present")
        # Ordinary dispatch tests use the host PATH fallback.
        cls.enterClassContext(patch.dict(
            os.environ, RTK_BIN="", PATH=f"{STAGED_RTK.parent}{os.pathsep}{os.environ['PATH']}"
        ))
        # Ensure hermes_cli is importable. The runner adds the Hermes tree to
        # sys.path; also try the conventional ~/.hermes/hermes-agent directly.
        hermes_tree = os.environ.get("HERMES_TREE") or str(Path.home() / ".hermes" / "hermes-agent")
        if Path(hermes_tree).is_dir() and hermes_tree not in sys.path:
            sys.path.insert(0, hermes_tree)
        try:
            import hermes_cli  # noqa: F401
        except Exception as exc:  # pragma: no cover - CI without a Hermes tree
            raise unittest.SkipTest(f"hermes_cli not importable: {exc}")

    def setUp(self):
        from hermes_cli import plugins as P
        self.P = P
        self._tmp = tempfile.TemporaryDirectory()
        self._base = Path(self._tmp.name)

    def tearDown(self):
        self.P._reset_plugin_managers_for_tests()
        self._tmp.cleanup()

    def _dispatch(self, home, tool_name, args):
        os.environ["HERMES_HOME"] = str(home)
        self.P._ensure_plugins_discovered(force=True)
        # The gateway binds the routed profile's terminal policy around each turn
        # (tools/terminal_scope.py). Without it the terminal tool falls back to a
        # process-wide bridge that latches on the first home it sees, which is not
        # how a served profile behaves. Mirror the gateway.
        from tools.terminal_scope import (
            build_profile_terminal_scope, reset_terminal_scope, set_terminal_scope,
        )
        token = set_terminal_scope(build_profile_terminal_scope(home))
        try:
            return self.P._dispatch_pre_tool_call_hooks(tool_name, args)
        finally:
            reset_terminal_scope(token)

    def test_dispatch_rewrites_and_preserves_other_args(self):
        home = _make_home(self._base, "homeA")
        block, modified = self._dispatch(
            home, "terminal",
            {"command": "cat a.txt", "cwd": "/work", "timeout": 60, "background": True},
        )
        self.assertIsNone(block)
        self.assertIsNotNone(modified)
        self.assertEqual(modified["command"], "rtk read a.txt")
        # The real Hermes shallow merge must preserve the other keys.
        self.assertEqual(modified["cwd"], "/work")
        self.assertEqual(modified["timeout"], 60)
        self.assertIs(modified["background"], True)

    def _dispatch_routed(self, override, *, path, launch_override):
        from agent.secret_scope import (
            build_profile_secret_scope, reset_multiplex_context, reset_secret_scope,
            set_multiplex_context, set_secret_scope,
        )
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        from tools.terminal_scope import (
            build_profile_terminal_scope, reset_terminal_scope, set_terminal_scope,
        )

        launch_home = _make_home(self._base, "launch")
        profile_home = _make_home(self._base, "routed")
        if override is not None:
            (profile_home / ".env").write_text(f"RTK_BIN={override}\n")
        with patch.dict(os.environ, HERMES_HOME=str(launch_home), PATH=path, RTK_BIN=launch_override):
            with ExitStack() as stack:
                token = set_multiplex_context(True)
                stack.callback(reset_multiplex_context, token)
                token = set_hermes_home_override(profile_home)
                stack.callback(reset_hermes_home_override, token)
                secrets = build_profile_secret_scope(profile_home)
                self.assertEqual(secrets.get("RTK_BIN"), override)
                token = set_secret_scope(secrets, profile_home=str(profile_home))
                stack.callback(reset_secret_scope, token)
                token = set_terminal_scope(build_profile_terminal_scope(profile_home))
                stack.callback(reset_terminal_scope, token)
                self.P._ensure_plugins_discovered(force=True)
                return self.P._dispatch_pre_tool_call_hooks(
                    "terminal", {"command": "cat a.txt"}
                )

    def _host_approvals(self, *paths):
        """Supply approval data without modifying the host's /etc."""
        read_text = Path.read_text

        def read(path, *args, **kwargs):
            if path == Path("/etc/rtk-rewrite-docker.allowed-binaries"):
                return "# Host-approved RTK executables\n\n" + "\n".join(map(str, paths))
            return read_text(path, *args, **kwargs)

        return patch.object(Path, "read_text", read)

    def test_multiplexed_profile_uses_approved_absolute_override(self):
        with self._host_approvals(STAGED_RTK):
            result = self._dispatch_routed(str(STAGED_RTK), path=str(self._base), launch_override="")
        self.assertEqual(result, (None, {"command": "rtk read a.txt"}))

    def test_multiplexed_profile_rejects_relative_override_without_running_it(self):
        with self._host_approvals(STAGED_RTK), patch.object(subprocess, "run") as run:
            result = self._dispatch_routed("./rtk", path=str(STAGED_RTK.parent), launch_override="")
        run.assert_not_called()
        self.assertEqual(result, (None, None))

    def test_multiplexed_profile_rejects_unapproved_absolute_override(self):
        with self._host_approvals(), patch.object(subprocess, "run") as run:
            result = self._dispatch_routed(str(STAGED_RTK), path=str(STAGED_RTK.parent), launch_override="")
        run.assert_not_called()
        self.assertEqual(result, (None, None))

    def test_multiplexed_profile_without_override_uses_path_not_launch_override(self):
        launch_rtk = self._base / "launch-rtk"
        shutil.copy2(STAGED_RTK, launch_rtk)
        with self._host_approvals(launch_rtk), patch.object(subprocess, "run", wraps=subprocess.run) as run:
            result = self._dispatch_routed(None, path=str(STAGED_RTK.parent), launch_override=str(launch_rtk))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][0], str(STAGED_RTK))
        self.assertEqual(result, (None, {"command": "rtk read a.txt"}))

    def test_multiplexed_profile_without_env_resolver_uses_path(self):
        launch_rtk = self._base / "launch-rtk"
        import_module = builtins.__import__

        def import_without_resolver(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "hermes_cli.config" and fromlist == ("get_env_value",):
                raise ImportError("profile environment resolver unavailable")
            return import_module(name, globals, locals, fromlist, level)

        rewrite = subprocess.CompletedProcess([], 0, stdout="rtk read a.txt")
        with (
            self._host_approvals(launch_rtk),
            patch.object(builtins, "__import__", import_without_resolver),
            patch.object(subprocess, "run", return_value=rewrite) as run,
        ):
            result = self._dispatch_routed(None, path=str(STAGED_RTK.parent), launch_override=str(launch_rtk))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][0], str(STAGED_RTK))
        self.assertEqual(result, (None, {"command": "rtk read a.txt"}))

    def test_dispatch_passthrough_returns_no_modify(self):
        home = _make_home(self._base, "homeA")
        block, modified = self._dispatch(home, "terminal", {"command": "pwd"})
        self.assertIsNone(block)
        self.assertIsNone(modified)

    def test_dispatch_unsupported_tool_untouched(self):
        home = _make_home(self._base, "homeA")
        block, modified = self._dispatch(home, "read_file", {"path": "x"})
        self.assertIsNone(block)
        self.assertIsNone(modified)

    def test_profile_switch_a_b_a(self):
        """Dispatch must work in home A, then home B, then back to home A."""
        home_a = _make_home(self._base, "homeA")
        home_b = _make_home(self._base, "homeB")

        # A
        block, modified = self._dispatch(
            home_a, "terminal", {"command": "cat a.txt", "cwd": "/a", "timeout": 10}
        )
        self.assertIsNone(block)
        self.assertEqual(modified["command"], "rtk read a.txt")
        self.assertEqual(modified["cwd"], "/a")

        # B
        block, modified = self._dispatch(
            home_b, "terminal", {"command": "cat b.txt", "cwd": "/b", "timeout": 20}
        )
        self.assertIsNone(block)
        self.assertEqual(modified["command"], "rtk read b.txt")
        self.assertEqual(modified["cwd"], "/b")

        # back to A
        block, modified = self._dispatch(
            home_a, "terminal", {"command": "cat a.txt", "cwd": "/a", "timeout": 10}
        )
        self.assertIsNone(block)
        self.assertEqual(modified["command"], "rtk read a.txt")
        self.assertEqual(modified["cwd"], "/a")

    # -- terminal backend gate (real Hermes config + terminal policy) ---------------

    def _with_terminal_env(self, value):
        """Set the launch process's ambient TERMINAL_ENV for one test, then restore."""
        old = os.environ.get("TERMINAL_ENV")

        def _restore():
            if old is None:
                os.environ.pop("TERMINAL_ENV", None)
            else:
                os.environ["TERMINAL_ENV"] = old

        self.addCleanup(_restore)
        os.environ["TERMINAL_ENV"] = value

    def test_docker_profile_is_not_rewritten_by_default(self):
        home = _make_docker_home(self._base, "dockerA", opt_in=False)
        block, modified = self._dispatch(home, "terminal", {"command": "cat a.txt", "cwd": "/w"})
        self.assertIsNone(block)
        self.assertIsNone(modified)

    def test_docker_profile_is_rewritten_when_opted_in(self):
        home = _make_docker_home(self._base, "dockerB", opt_in=True)
        block, modified = self._dispatch(
            home, "terminal", {"command": "cat a.txt", "cwd": "/w", "timeout": 30}
        )
        self.assertIsNone(block)
        self.assertEqual(modified["command"], "rtk read a.txt")
        self.assertEqual(modified["cwd"], "/w")
        self.assertEqual(modified["timeout"], 30)

    def test_ambient_env_does_not_decide_the_backend(self):
        """The multiplexer bug: process env says local, the profile says docker."""
        self._with_terminal_env("local")
        home = _make_docker_home(self._base, "dockerC", opt_in=False)
        _, modified = self._dispatch(home, "terminal", {"command": "cat a.txt"})
        self.assertIsNone(modified)

    def test_ambient_docker_env_does_not_suppress_a_local_profile(self):
        self._with_terminal_env("docker")
        home = _make_home(self._base, "localD")
        _, modified = self._dispatch(home, "terminal", {"command": "cat a.txt"})
        self.assertEqual(modified["command"], "rtk read a.txt")

    def test_local_and_docker_profiles_alternate_without_leaking(self):
        local = _make_home(self._base, "localE")
        docker = _make_docker_home(self._base, "dockerE", opt_in=False)
        for home, expect in (
            (local, "rtk read a.txt"), (docker, None), (local, "rtk read a.txt"), (docker, None),
        ):
            _, modified = self._dispatch(home, "terminal", {"command": "cat a.txt"})
            self.assertEqual(None if modified is None else modified["command"], expect, home.name)


if __name__ == "__main__":
    unittest.main()
