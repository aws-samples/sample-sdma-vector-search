# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Checks that each Lambda function declares exactly what it imports.

Both failure directions have already occurred in this repository:

- ``opensearch-py`` and ``Pillow`` stayed declared long after the last import of
  them was deleted, inflating the deployment package and keeping the functions
  exposed to advisories against dependencies they never loaded.
- ``urllib3`` was imported by three functions but declared by none of them. It
  resolved anyway because boto3 depends on it, so the omission was invisible
  until boto3 stopped guaranteeing a safe version.
"""
import re

import pytest

from conftest import FUNCTIONS

# Third-party modules the Lambda code may import, and the distribution that
# provides each one. Standard library modules are deliberately absent.
MODULE_TO_DISTRIBUTION = {
    "boto3": "boto3",
    "botocore": "botocore",
    "urllib3": "urllib3",
    "yaml": "PyYAML",
    "xxhash": "xxhash",
    "PIL": "Pillow",
    "requests": "requests",
    "opensearchpy": "opensearch-py",
}

FUNCTION_NAMES = sorted(
    p.name for p in FUNCTIONS.iterdir() if p.is_dir() and (p / "requirements.txt").exists()
)


def _declared(function_name):
    text = (FUNCTIONS / function_name / "requirements.txt").read_text()
    names = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if line:
            names.add(re.split(r"[><=!\[]", line)[0].strip().lower())
    return names


def _shared_modules(function_name):
    """Return the shared modules backend/lambda/Makefile copies into this
    function's artifact.

    A function's deployment package is its own directory *plus* the shared
    modules its build target lists, so a dependency imported only by a shared
    module still has to be declared in that function's requirements.txt. The
    Makefile is the single source of which modules each function gets.
    """
    makefile = (FUNCTIONS.parent / "Makefile").read_text()
    match = re.search(
        rf"build_function,functions/{re.escape(function_name)},([^)]*)\)", makefile)
    if not match:
        return []
    shared_dir = FUNCTIONS.parent / "shared"
    return [shared_dir / f"{name}.py" for name in match.group(1).split()]


def _imported(function_name):
    distributions = set()
    paths = list((FUNCTIONS / function_name).rglob("*.py"))
    paths += _shared_modules(function_name)
    for path in paths:
        # blender_scripts runs inside Blender's own interpreter and is not
        # installed from requirements.txt.
        if "blender_scripts" in path.parts:
            continue
        if not path.exists():
            continue
        source = path.read_text(errors="ignore")
        for module, distribution in MODULE_TO_DISTRIBUTION.items():
            if re.search(rf"^\s*(import {module}\b|from {module}[\s.])", source, re.M):
                distributions.add(distribution.lower())
    return distributions


@pytest.mark.parametrize("function_name", FUNCTION_NAMES)
def test_every_imported_distribution_is_declared(function_name):
    missing = _imported(function_name) - _declared(function_name)
    assert not missing, (
        f"{function_name} imports {sorted(missing)} without declaring it in "
        "requirements.txt. It currently resolves only as a transitive "
        "dependency, which can disappear without warning."
    )


@pytest.mark.parametrize("function_name", FUNCTION_NAMES)
def test_every_declared_distribution_is_imported(function_name):
    unused = _declared(function_name) - _imported(function_name)
    assert not unused, (
        f"{function_name} declares {sorted(unused)} but never imports it. "
        "Unused dependencies enlarge the deployment package and attract "
        "advisories for code that never runs."
    )


def test_the_test_environment_declares_what_the_tests_import():
    """tests/requirements.txt must cover the runtime deps the tests load.

    The tests import the function handlers, so the test environment needs every
    distribution those handlers import -- not just pytest and boto3. PyYAML and
    xxhash were missing here while correctly declared in the functions' own
    requirements, so a developer whose machine happened to have them saw a green
    suite while CI errored at collection on one and failed four assertions on the
    other.
    """
    here = FUNCTIONS.parent.parent.parent / "tests" / "requirements.txt"

    def normalise(name):
        # PEP 503: comparison is case-insensitive and treats -, _ and . alike,
        # so `PyYAML` and `pyyaml` are the same distribution.
        return re.sub(r"[-_.]+", "-", name).lower()

    declared = set()
    for line in here.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            declared.add(normalise(re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()))

    needed = set()
    for name in FUNCTION_NAMES:
        needed |= {normalise(d) for d in _imported(name)}

    missing = needed - declared
    assert not missing, (
        f"tests/requirements.txt does not declare {sorted(missing)}, which the "
        "function modules under test import. The suite passes only on a machine "
        "that already has them for some other reason."
    )


def test_shared_version_floors_agree_across_functions():
    # A distribution pinned differently per function makes the effective version
    # depend on which package happens to be built, which is hard to reason about
    # when diagnosing a runtime failure.
    floors = {}
    for function_name in FUNCTION_NAMES:
        text = (FUNCTIONS / function_name / "requirements.txt").read_text()
        for line in text.splitlines():
            line = line.split("#")[0].strip()
            if not line or ">=" not in line:
                continue
            name, _, spec = line.partition(">=")
            floors.setdefault(name.strip().lower(), {}).setdefault(
                spec.strip(), []
            ).append(function_name)

    # boto3 and botocore intentionally differ: only vector-search-api needs the
    # release that introduced SearchVectors.
    conflicting = {
        name: specs
        for name, specs in floors.items()
        if len(specs) > 1 and name not in {"boto3", "botocore"}
    }
    assert not conflicting, f"inconsistent version floors: {conflicting}"
