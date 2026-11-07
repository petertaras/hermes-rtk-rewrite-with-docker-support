"""Real Hermes dispatch into the image built from container/Containerfile.

Set RTK_TEST_IMAGE to that image. Missing prerequisites are rejected by the runner.
All containers are disposable and use fixture files created inside the container.
"""

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import STAGED_RTK
from test_hermes_dispatch import _make_docker_home


class ContainerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = os.environ.get("RTK_TEST_IMAGE")
        if not cls.image or not shutil.which("docker") or not STAGED_RTK.is_file():
            raise unittest.SkipTest("RTK_TEST_IMAGE, container engine and staged RTK are required")

    def _run(self, command):
        assert self.image is not None
        return subprocess.run(
            ["docker", "run", "--rm", "--network=none", self.image, "sh", "-c", command],
            capture_output=True, text=True, timeout=60,
        )

    def _dispatch(self, command, *, opt_in=True):
        from hermes_cli import plugins
        from tools.terminal_scope import (
            build_profile_terminal_scope, reset_terminal_scope, set_terminal_scope,
        )

        with tempfile.TemporaryDirectory() as directory:
            home = _make_docker_home(Path(directory), "docker", opt_in=opt_in)
            with mock.patch.dict(os.environ, HERMES_HOME=str(home), RTK_BIN=str(STAGED_RTK)):
                plugins._ensure_plugins_discovered(force=True)
                token = set_terminal_scope(build_profile_terminal_scope(home))
                try:
                    block, modified = plugins._dispatch_pre_tool_call_hooks(
                        "terminal", {"command": command, "timeout": 30},
                    )
                    self.assertIsNone(block)
                    return modified
                finally:
                    reset_terminal_scope(token)
                    plugins._reset_plugin_managers_for_tests()

    def test_image_version_and_telemetry_opt_out(self):
        result = self._run('rtk --version; printf "telemetry=%s\\n" "$RTK_TELEMETRY_DISABLED"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["rtk 1.1.0", "telemetry=1"])

    def test_container_rewrite_exit_codes(self):
        result = self._run(
            'rtk rewrite "cat a.txt"; code=$?; test "$code" = 3 || exit 10; '
            'rtk rewrite "pwd"; code=$?; test "$code" = 1 || exit 11'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "rtk read a.txt")

    def test_dispatched_rewrite_runs_inside_container(self):
        modified = self._dispatch("cat /workspace/rtk-fixture.txt")
        assert modified is not None
        self.assertEqual(modified["command"], "rtk read /workspace/rtk-fixture.txt")
        self.assertEqual(modified["timeout"], 30)
        fixture = "mkdir -p /workspace; printf 'container evidence\\n' > /workspace/rtk-fixture.txt; "
        result = self._run(fixture + modified["command"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "container evidence\n")

    def test_default_docker_gate_and_raw_bypass(self):
        self.assertIsNone(self._dispatch("cat /workspace/rtk-fixture.txt", opt_in=False))
        command = "rtk bypass printf %s " + shlex.quote("raw container evidence")
        self.assertIsNone(self._dispatch(command))
        result = self._run(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "raw container evidence")


if __name__ == "__main__":
    unittest.main()
