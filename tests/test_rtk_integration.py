"""Integration tests: real pinned RTK rewrite on a harmless temp git fixture.

Uses the staged, checksum-verified RTK v0.50.0 binary (see docs/EVIDENCE.md).
Creates a throwaway git repo as the fixture — never a user repo. Verifies the
adapter produces a real rewrite, that passthrough stays untouched, and that the
rewritten command is a meaningful, executable transform with sane exit semantics.
"""

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import STAGED_RTK, load_plugin_module


def _make_git_fixture(base: Path) -> Path:
    repo = base / "fixture"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "a.txt").write_text("hello world\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


class RtkIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not STAGED_RTK.is_file():
            raise unittest.SkipTest("staged RTK binary not present")
        cls.m = load_plugin_module()
        cls.m._reset_rtk_cache()
        # These tests exercise RTK's real rewrite, not backend detection (covered by
        # the unit and dispatch suites). Without a Hermes tree on the path the
        # backend is "unknown" and the adapter rightly skips, so pin it to local.
        cls._backend_patch = mock.patch.object(cls.m, "_active_backend", return_value="local")
        cls._backend_patch.start()
        cls._old_rtk_bin = os.environ.get("RTK_BIN")
        os.environ["RTK_BIN"] = str(STAGED_RTK)

    @classmethod
    def tearDownClass(cls):
        cls._backend_patch.stop()
        if cls._old_rtk_bin is None:
            os.environ.pop("RTK_BIN", None)
        else:
            os.environ["RTK_BIN"] = cls._old_rtk_bin

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_git_fixture(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _rewrite(self, command):
        return self.m._pre_tool_call(tool_name="terminal", args={"command": command})

    def test_cat_rewrites_to_rtk_read(self):
        result = self._rewrite("cat a.txt")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "modify")
        self.assertEqual(result["args"]["command"], "rtk read a.txt")

    def test_git_status_rewrites_to_rtk_git(self):
        result = self._rewrite("git status")
        self.assertIsNotNone(result)
        self.assertEqual(result["args"]["command"], "rtk git status")

    def test_passthrough_command_untouched(self):
        self.assertIsNone(self._rewrite("pwd"))

    def test_rewritten_command_is_executable_and_meaningful(self):
        """The rewritten command must run and produce the same content as the
        original (RTK's read is a faithful cat replacement for this fixture)."""
        original = subprocess.run(
            ["cat", "a.txt"], cwd=self.repo, capture_output=True, text=True
        )
        directive = self._rewrite("cat a.txt")
        argv = shlex.split(directive["args"]["command"])
        self.assertEqual(argv[0], "rtk")
        # Execute the actual rewrite with the same verified binary used above.
        argv[0] = str(STAGED_RTK)
        rewritten = subprocess.run(argv, cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(original.returncode, 0)
        self.assertEqual(rewritten.returncode, 0)
        self.assertEqual(rewritten.stdout, original.stdout)

    def test_rtk_rewrite_exit_semantics(self):
        """Confirm the pinned RTK's rewrite exit codes match the adapter's
        documented contract: 3 = rewrite, 1 = passthrough."""
        rw = subprocess.run(
            [str(STAGED_RTK), "rewrite", "cat a.txt"], capture_output=True, text=True
        )
        self.assertEqual(rw.returncode, 3)
        self.assertEqual(rw.stdout.strip(), "rtk read a.txt")

        ps = subprocess.run(
            [str(STAGED_RTK), "rewrite", "pwd"], capture_output=True, text=True
        )
        self.assertEqual(ps.returncode, 1)


if __name__ == "__main__":
    unittest.main()
