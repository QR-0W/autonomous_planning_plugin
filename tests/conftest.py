"""
conftest.py — Shared fixtures for xuqian13 plugin tests.

Provides the standard "load plugin" fixture used by all test modules,
mirroring the approach in run_smoke.py.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PKG_NAME = "_maibot_plugin_xuqian13_autonomous_planning_plugin_v4"


def _load_plugin() -> Any:
    """Load the plugin package exactly once, returning the top-level module."""
    if PKG_NAME in sys.modules:
        return sys.modules[PKG_NAME]
    spec = importlib.util.spec_from_file_location(
        PKG_NAME, PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def plugin_pkg() -> Any:
    """Return the loaded plugin package (session-scoped, single load)."""
    return _load_plugin()


@pytest.fixture
def imp(plugin_pkg: Any):
    """Return a function that imports a submodule by relative name."""
    def _imp(rel: str) -> Any:
        return importlib.import_module(f"{PKG_NAME}.{rel}")
    return _imp


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Per-test isolated data directory (never the plugin's live ``data/``)."""
    directory = tmp_path / "data"
    directory.mkdir(parents=True, exist_ok=True)
    return directory
