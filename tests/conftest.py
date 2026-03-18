# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Shared pytest fixtures.

Each Lambda function directory carries its own ``system_defaults.py`` and
``aws_clients.py`` with *different* contents, so the function directories
cannot all sit on ``sys.path`` at once -- ``import aws_clients`` would resolve
to whichever directory came first. ``load_function_module`` below therefore
imports a module with exactly one function directory on the path and then
unloads it, so each test gets the module it asked for.

``backend/lambda/shared`` is also placed on the path, because the shared modules
live only there. At build time ``backend/lambda/Makefile`` copies the ones each
function imports into the build artifact, so a function directory on its own is
not importable.
"""
import importlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED = REPO_ROOT / "backend" / "lambda" / "shared"
FUNCTIONS = REPO_ROOT / "backend" / "lambda" / "functions"

# Modules import boto3 clients at module scope, which needs a region and
# credentials to construct (no network calls are made). Set them before any
# module under test is imported, and use obviously fake values so that a test
# which accidentally reaches AWS fails instead of touching a real account.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")

# Configuration the handlers read at import time.
os.environ.setdefault("VECTOR_TABLE_NAME", "test-asset-vectors")
os.environ.setdefault("VECTOR_INDEX_NAME", "embedding-index")
os.environ.setdefault("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
os.environ.setdefault("EMBEDDING_DIMENSIONS", "1024")
os.environ.setdefault("DYNAMODB_TABLE", "test-sdma-assets")
os.environ.setdefault("S3_BUCKET_NAME", "test-sdma-assets-bucket")
os.environ.setdefault("SDMA_API_ENDPOINT", "https://example.invalid/dev")


@contextmanager
def _isolated_path(function_dir: Path):
    """Put one function directory (plus shared) on sys.path, then restore."""
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    sys.path.insert(0, str(SHARED))
    sys.path.insert(0, str(function_dir))
    try:
        yield
    finally:
        # Unload anything that came from the function directory or shared, so a
        # later load of a same-named module in another function directory is not
        # served from cache. Third-party modules are left alone.
        roots = (str(function_dir), str(SHARED))
        for name, module in list(sys.modules.items()):
            if name in saved_modules:
                continue
            origin = getattr(module, "__file__", None) or ""
            if origin.startswith(roots):
                del sys.modules[name]
        sys.path[:] = saved_path


def load_function_module(function_name: str, module_name: str):
    """Import ``module_name`` from the given Lambda function directory."""
    function_dir = FUNCTIONS / function_name
    if not function_dir.is_dir():
        raise AssertionError(f"no such function directory: {function_dir}")
    with _isolated_path(function_dir):
        return importlib.import_module(module_name)


def load_shared_module(module_name: str):
    """Import ``module_name`` from ``backend/lambda/shared``.

    Same unload-after-import discipline as ``load_function_module``: a shared
    module can be imported by several tests, and leaving it in ``sys.modules``
    would let one test's monkeypatching leak into the next.
    """
    with _isolated_path(SHARED):
        return importlib.import_module(module_name)


@pytest.fixture(scope="module")
def vector_utils():
    """The shared embedding helper, imported from backend/lambda/shared."""
    saved_path = list(sys.path)
    saved = sys.modules.pop("vector_utils", None)
    sys.path.insert(0, str(SHARED))
    try:
        yield importlib.import_module("vector_utils")
    finally:
        sys.modules.pop("vector_utils", None)
        if saved is not None:
            sys.modules["vector_utils"] = saved
        sys.path[:] = saved_path


@pytest.fixture(scope="module")
def vector_indexer():
    """ai-tag-generation's DynamoDB vector writer."""
    yield load_function_module("ai-tag-generation", "vector_indexer")


@pytest.fixture(scope="module")
def search_handler():
    """vector-search-api's Lambda handler module."""
    yield load_function_module("vector-search-api", "handler")
