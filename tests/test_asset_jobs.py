# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for the processing-state table this stack owns.

These fields used to be written onto SDMA's own asset record -- six
Extension-specific attributes plus a lowercase ``updatedAt`` duplicating SDMA's
``UpdatedAt`` -- which put this solution's data in a table it does not own.
"""
import pytest
from botocore.exceptions import ClientError

from conftest import load_shared_module


class FakeTable:
    def __init__(self, error=None):
        self.updates = []
        self.item = {}
        self.error = error

    def update_item(self, **kwargs):
        if self.error:
            raise self.error
        self.updates.append(kwargs)

    def get_item(self, **kwargs):
        if self.error:
            raise self.error
        return {'Item': self.item} if self.item else {}


@pytest.fixture
def jobs(monkeypatch):
    monkeypatch.setenv("ASSET_JOBS_TABLE", "test-asset-jobs")
    asset_jobs = load_shared_module("asset_jobs")
    table = FakeTable()
    monkeypatch.setattr(asset_jobs, "_table", lambda: table)
    asset_jobs.table = table
    yield asset_jobs


def _assignments(update):
    return update["UpdateExpression"]


def _values(update):
    """Map attribute name -> value, resolving the placeholders."""
    names = update["ExpressionAttributeNames"]
    values = update["ExpressionAttributeValues"]
    resolved = {}
    for part in _assignments(update).removeprefix("SET ").split(", "):
        placeholder, value_key = [p.strip() for p in part.split("=")]
        resolved[names[placeholder]] = values[value_key]
    return resolved


class TestSetRenderStatus:
    def test_records_status_and_timestamp(self, jobs):
        jobs.set_render_status("asset-1", "RENDERING")

        written = _values(jobs.table.updates[0])
        assert written["renderJobStatus"] == "RENDERING"
        assert written["renderJobUpdatedAt"]

    def test_current_render_and_last_render_are_distinct(self, jobs):
        # prepare-render records the render it is starting, finalize-render the
        # one that finished; conflating them loses that distinction.
        jobs.set_render_status("asset-1", "RENDERING", "r1", current=True)
        jobs.set_render_status("asset-1", "COMPLETED", "r1")

        assert "currentRenderId" in _values(jobs.table.updates[0])
        assert "lastRenderId" in _values(jobs.table.updates[1])

    def test_omits_the_render_id_when_absent(self, jobs):
        jobs.set_render_status("asset-1", "FAILED")

        written = _values(jobs.table.updates[0])
        assert "currentRenderId" not in written
        assert "lastRenderId" not in written


class TestSetAiTagStatus:
    def test_records_status(self, jobs):
        jobs.set_ai_tag_status("asset-1", "COMPLETED")

        assert _values(jobs.table.updates[0])["aiTagJobStatus"] == "COMPLETED"

    def test_records_an_error_message_when_given(self, jobs):
        jobs.set_ai_tag_status("asset-1", "FAILED", "bedrock threw")

        assert _values(jobs.table.updates[0])["aiTagJobError"] == "bedrock threw"


class TestExpiry:
    def test_every_write_sets_a_ttl(self, jobs):
        # Without one the table grows a row per asset forever, and processing
        # state is only useful while a pipeline runs or shortly after.
        jobs.set_render_status("asset-1", "RENDERING")

        assert "expiresAt" in _values(jobs.table.updates[0])


class TestReservedWordSafety:
    def test_aliases_every_attribute_name(self, jobs):
        # 'style' and others are DynamoDB reserved words. Aliasing
        # unconditionally means a newly added field cannot reintroduce that bug.
        jobs.set_render_status("asset-1", "RENDERING", "r1", current=True)

        update = jobs.table.updates[0]
        assert "#" in _assignments(update)
        assert all(name.startswith("#") for name in update["ExpressionAttributeNames"])


class TestFailureHandling:
    """Status is advisory and nothing gates on it, so a write failure must not
    fail an asset that rendered and indexed correctly.
    """

    def test_swallows_a_write_error(self, jobs, monkeypatch):
        table = FakeTable(error=ClientError({'Error': {'Code': 'AccessDenied'}}, 'UpdateItem'))
        monkeypatch.setattr(jobs, "_table", lambda: table)

        jobs.set_render_status("asset-1", "RENDERING")  # must not raise

    def test_returns_empty_when_a_read_fails(self, jobs, monkeypatch):
        table = FakeTable(error=ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetItem'))
        monkeypatch.setattr(jobs, "_table", lambda: table)

        assert jobs.get_status("asset-1") == {}

    def test_returns_empty_for_an_asset_with_no_row(self, jobs):
        assert jobs.get_status("asset-1") == {}

    def test_does_nothing_without_an_asset_id(self, jobs):
        jobs.set_render_status("", "RENDERING")

        assert jobs.table.updates == []


class TestGetStatus:
    def test_returns_the_recorded_row(self, jobs):
        jobs.table.item = {"assetId": "asset-1", "renderJobStatus": "COMPLETED"}

        assert jobs.get_status("asset-1")["renderJobStatus"] == "COMPLETED"
