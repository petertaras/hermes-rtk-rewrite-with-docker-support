#!/usr/bin/env python3
"""Run the rtk-rewrite-docker test suite (stdlib unittest, no external deps).

Usage:
    python tests/run_tests.py            # all tests
    python tests/run_tests.py adapter    # only unit tests
    python tests/run_tests.py dispatch   # only Hermes dispatch tests
    python tests/run_tests.py integration# only real-RTK integration tests

The dispatch tests import the real Hermes plugin machinery, so this runner adds
the local Hermes tree to sys.path when it is present. Run with the Hermes venv
python (which has hermes_cli's dependencies) for the full suite.
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent

# Make the tests/ package importable (tests import _helpers).
sys.path.insert(0, str(TESTS_DIR))

# Make hermes_cli importable for the dispatch tests when a Hermes tree is local.
# Prefer an explicit HERMES_TREE override, else the conventional ~/.hermes/hermes-agent.
hermes_tree = os.environ.get("HERMES_TREE") or str(Path.home() / ".hermes" / "hermes-agent")
if Path(hermes_tree).is_dir() and hermes_tree not in sys.path:
    sys.path.insert(0, hermes_tree)

SUITES = {
    "adapter": ["test_adapter"],
    "dispatch": ["test_hermes_dispatch"],
    "integration": ["test_rtk_integration"],
}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which == "all":
        modules = [m for mods in SUITES.values() for m in mods]
    elif which in SUITES:
        modules = SUITES[which]
    else:
        print(f"unknown suite {which!r}; choose from {sorted(SUITES)} or 'all'")
        return 2

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for mod in modules:
        suite.addTests(loader.loadTestsFromName(mod))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print("Missing prerequisites: skipped tests are not passing coverage.", file=sys.stderr)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
