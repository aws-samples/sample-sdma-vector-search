# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for the shared structured logger.

The redaction here is a security control: prepare-render fetches short-lived
SDMA write credentials and passes them through the Step Functions payload,
and several handlers log that payload verbatim to make executions traceable.
Before redaction existed those handlers wrote usable AWS credentials into
CloudWatch, once from prepare-render and once per render view.
"""
import json
import pytest
from conftest import load_shared_module

@pytest.fixture(scope="module")
def log_utils():
    return load_shared_module("log_utils")


def _logged(capsys):
    """Parse the single JSON object the logger printed."""
    return json.loads(capsys.readouterr().out.strip())


def test_logs_event_type_and_timestamp(log_utils, capsys):
    log_utils.log_event("render_finalized", assetId="asset-1")

    record = _logged(capsys)
    assert record["event"] == "render_finalized"
    assert record["assetId"] == "asset-1"
    assert "timestamp" in record


def test_redacts_credentials_nested_in_a_step_functions_payload(log_utils, capsys):
    # Shaped like the real finalize_render_invoked payload, where the
    # credentials sit four levels down inside a per-view render job.
    log_utils.log_event(
        "finalize_render_invoked",
        payload={
            "prepareResult": {
                "assetId": "asset-1",
                "renderJobs": [
                    {
                        "viewName": "front",
                        "sdmaCreds": {
                            # Deliberately not shaped like a real key id, so
                            # the secret-scanning pre-commit hook does not flag
                            # this fixture.
                            "AccessKeyId": "access-key-sentinel-4b2c",
                            "SecretAccessKey": "aSecretThatMustNotBeLogged",
                            "SessionToken": "aSessionTokenThatMustNotBeLogged",
                            "ExpirationTimestamp": "1788087504000.0",
                        },
                    }
                ],
            }
        },
    )

    out = capsys.readouterr().out
    assert "aSecretThatMustNotBeLogged" not in out
    assert "aSessionTokenThatMustNotBeLogged" not in out
    assert "access-key-sentinel-4b2c" not in out

    creds = json.loads(out)["payload"]["prepareResult"]["renderJobs"][0]["sdmaCreds"]
    assert creds["SecretAccessKey"] == "[REDACTED]"
    assert creds["SessionToken"] == "[REDACTED]"
    assert creds["AccessKeyId"] == "[REDACTED]"
    # Non-secret fields must survive, or the log stops being useful for
    # diagnosing credential expiry.
    assert creds["ExpirationTimestamp"] == "1788087504000.0"


def test_keeps_surrounding_payload_intact(log_utils, capsys):
    log_utils.log_event(
        "lambda_invoked",
        input={
            "assetId": "asset-1",
            "viewName": "front",
            "sdmaCreds": {"SecretAccessKey": "secret"},
        },
    )

    payload = _logged(capsys)["input"]
    assert payload["assetId"] == "asset-1"
    assert payload["viewName"] == "front"


@pytest.mark.parametrize(
    "key",
    [
        "SecretAccessKey",
        "secret_access_key",
        "secretaccesskey",
        "SessionToken",
        "session-token",
        "AccessKeyId",
        "password",
        "Authorization",
    ],
)
def test_matches_key_spellings_case_and_separator_insensitively(log_utils, capsys, key):
    # The sentinel must not appear in any key name, or the assertion would
    # pass on the key rather than on the redacted value.
    log_utils.log_event("probe", data={key: "sentinel-value-9f3a"})

    out = capsys.readouterr().out
    assert "sentinel-value-9f3a" not in out
    assert json.loads(out)["data"][key] == "[REDACTED]"


def test_leaves_non_sensitive_keys_alone(log_utils, capsys):
    log_utils.log_event(
        "probe",
        data={"assetId": "asset-1", "renderId": "abc123", "tokenCount": 42},
    )

    record = _logged(capsys)["data"]
    assert record == {"assetId": "asset-1", "renderId": "abc123", "tokenCount": 42}


def test_redacts_inside_lists_of_render_jobs(log_utils, capsys):
    log_utils.log_event(
        "probe",
        jobs=[
            {"sdmaCreds": {"SessionToken": "first"}},
            {"sdmaCreds": {"SessionToken": "second"}},
        ],
    )

    out = capsys.readouterr().out
    assert "first" not in out
    assert "second" not in out


def test_serializes_values_json_cannot_encode(log_utils, capsys):
    # Handlers pass Decimal values straight from DynamoDB; without a default
    # encoder the log call would raise and lose the event entirely.
    from decimal import Decimal

    log_utils.log_event("probe", score=Decimal("0.384"))

    assert _logged(capsys)["score"] == "0.384"


def test_survives_a_self_referential_payload(log_utils, capsys):
    payload = {"assetId": "asset-1"}
    payload["self"] = payload

    log_utils.log_event("probe", payload=payload)

    assert "asset-1" in capsys.readouterr().out
