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


def _stub_boto3():
    """boto3 and botocore are stubbed when they are not installed so the
    object-audit source imports on a machine without them; the tests slot
    their own fake S3 client in, and the one for the client construction
    patches boto3 and Config itself. The stub offers what the plugin
    imports: botocore.config.Config and botocore.exceptions.ClientError."""
    if importlib.util.find_spec("boto3") and importlib.util.find_spec("botocore"):
        return
    if "boto3" in sys.modules:
        return

    class Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ClientError(Exception):
        def __init__(self, error_response=None, operation_name=None):
            super().__init__(f"{operation_name}: {error_response}")
            self.response = error_response or {}
            self.operation_name = operation_name

    boto3 = types.ModuleType("boto3")
    boto3.client = MagicMock()
    botocore = types.ModuleType("botocore")
    config = types.ModuleType("botocore.config")
    config.Config = Config
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = ClientError
    botocore.config = config
    botocore.exceptions = exceptions
    sys.modules["boto3"] = boto3
    sys.modules["botocore"] = botocore
    sys.modules["botocore.config"] = config
    sys.modules["botocore.exceptions"] = exceptions


@pytest.fixture(scope="session")
def events():
    return _load("purefb_events")


@pytest.fixture(scope="session")
def object_audit():
    _stub_boto3()
    return _load("purefb_object_audit")


@pytest.fixture(scope="session")
def syslog():
    return _load("purefb_syslog")


@pytest.fixture(scope="session")
def repo_root():
    return ROOT
