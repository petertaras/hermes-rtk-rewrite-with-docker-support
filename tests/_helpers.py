"""Shared helpers for the rtk-rewrite test suite (stdlib only, no pytest)."""

import importlib.util
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGED_RTK = REPO_ROOT / ".staging" / "rtk"


def load_plugin_module() -> types.ModuleType:
    """Load the flat ``__init__.py`` plugin as a standalone module for testing."""
    spec = importlib.util.spec_from_file_location(
        "rtk_rewrite_adapter", REPO_ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
