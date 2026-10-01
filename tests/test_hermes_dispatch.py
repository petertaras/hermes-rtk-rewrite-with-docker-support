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

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from _helpers import REPO_ROOT, STAGED_RTK


def _make_home(base: Path, name: str) -> Path:
    """Create an isolated HERMES_HOME with the rtk-rewrite plugin installed+enabled."""
    home = base / name
    plugin_dir = home / "plugins" / "rtk-rewrite"
    plugin_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "plugin.yaml", plugin_dir / "plugin.yaml")
    shutil.copy2(REPO_ROOT / "__init__.py", plugin_dir / "__init__.py")
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - rtk-rewrite\n")
    return home


def _make_docker_home(base: Path, name: str, opt_in: bool) -> Path:
    """A profile whose terminal backend is docker, optionally opted in to rewrites."""
    home = _make_home(base, name)
    plugins = "plugins:\n  enabled:\n    - rtk-rewrite\n"
    if opt_in:
        plugins += (
            "  entries:\n    rtk-rewrite:\n      settings:\n"
            "        backends: [local, docker]\n"
        )
    (home / "config.yaml").write_text(plugins + "terminal:\n  backend: docker\n")
    return home


class HermesDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not STAGED_RTK.is_file():
            raise unittest.SkipTest("staged RTK binary not present")
        cls._old_home = os.environ.get("HERMES_HOME")
        cls._old_rtk_bin = os.environ.get("RTK_BIN")
        cls.addClassCleanup(cls._restore_env)
        # Point the plugin at the verified binary deterministically.
        os.environ["RTK_BIN"] = str(STAGED_RTK)
        # Ensure hermes_cli is importable. The runner adds the Hermes tree to
        # sys.path; also try the conventional ~/.hermes/hermes-agent directly.
        hermes_tree = os.environ.get("HERMES_TREE") or str(Path.home() / ".hermes" / "hermes-agent")
        if Path(hermes_tree).is_dir() and hermes_tree not in sys.path:
            sys.path.insert(0, hermes_tree)
        try:
            import hermes_cli  # noqa: F401
        except Exception as exc:  # pragma: no cover - CI without a Hermes tree
            raise unittest.SkipTest(f"hermes_cli not importable: {exc}")

    @classmethod
    def _restore_env(cls):
        if cls._old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = cls._old_home
        if cls._old_rtk_bin is None:
            os.environ.pop("RTK_BIN", None)
        else:
            os.environ["RTK_BIN"] = cls._old_rtk_bin

    def setUp(self):
        from hermes_cli import plugins as P
        self.P = P
        self._tmp = tempfile.TemporaryDirectory()
        self._base = Path(self._tmp.name)

    def tearDown(self):
        try:
            self.P.unload_plugins()
        except Exception:
            pass
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
