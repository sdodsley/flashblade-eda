# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Shared test setup: import the source plugins without an array or the SDK."""

import importlib.util
import pathlib
import sys
import types
from unittest.mock import MagicMock

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGINS = ROOT / "extensions" / "eda" / "plugins" / "event_source"


def _load(name):
    """Load a plugin module from its file; pypureclient is stubbed so the
    import works on a machine without the SDK. The test for the SDK client
    construction reads the stub's call arguments."""
    if "pypureclient" not in sys.modules:
        stub = types.ModuleType("pypureclient")
        stub.flashblade = MagicMock()
        sys.modules["pypureclient"] = stub
        sys.modules["pypureclient.flashblade"] = stub.flashblade
    path = PLUGINS / f"{name}.py"
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} is not in this tree")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def events():
    return _load("purefb_events")


@pytest.fixture(scope="session")
def syslog():
    return _load("purefb_syslog")


@pytest.fixture(scope="session")
def repo_root():
    return ROOT
