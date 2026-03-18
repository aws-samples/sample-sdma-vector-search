# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for the render plan this function hands to the Map state.

Everything the render needs travels in this plan -- the container function reads
no environment variables at all. In particular it carries the presigned model
URL, so a plan that omits it sends every render back to reading S3 with its own
permissions without anything failing.

The handler runs twice: the connector's invocation starts the state machine, and
the state machine's invocation (marked ``_fromStepFunctions``) builds the plan.
These tests take the second path.
"""
import pytest

from conftest import load_function_module


@pytest.fixture
def prepare(monkeypatch):
    monkeypatch.setenv("SDMA_API_ENDPOINT", "https://example.invalid/stage")
    monkeypatch.setenv("SDMA_LIBRARY_ID", "library-" + "0" * 32)
    monkeypatch.setenv("ASSET_JOBS_TABLE", "test-asset-jobs")
    monkeypatch.setenv("EXTENSION_BUCKET", "test-extension-data")
    monkeypatch.setenv("S3_BUCKET_NAME", "sdma-bucket")
    return load_function_module("prepare-render", "handler")


@pytest.fixture
def plan(prepare, monkeypatch):
    """Build a plan with SDMA faked, and return the render jobs."""
    monkeypatch.setattr(prepare, 'set_render_status', lambda *a, **k: None)
    monkeypatch.setattr(prepare.sdma_client, 'resolve_project_id',
                        lambda asset_id: 'project-1')
    monkeypatch.setattr(prepare, '_load_render_config',
                        lambda: {'views': {'enabled': ['front', 'back']}})
    monkeypatch.setattr(prepare, '_get_sdma_write_credentials',
                        lambda *a, **k: {'AccessKeyId': 'x',
                                         's3Prefix': 'SpatialDataManagementAssets'})

    def run(model_info):
        monkeypatch.setattr(prepare.sdma_client, 'find_file_by_extension',
                            lambda *a, **k: model_info)
        result = prepare.lambda_handler(
            {'assetId': 'asset-1', '_fromStepFunctions': True}, None)
        return result['renderJobs']

    return run


class TestRenderPlan:
    def test_carries_the_presigned_url_to_every_view(self, plan):
        jobs = plan({'s3Key': 'Data/aaa.xxh128', 'fileExtension': '.glb',
                     'downloadUrl': 'https://presigned.invalid/m.glb'})

        assert len(jobs) == 2
        assert all(j['glbUrl'] == 'https://presigned.invalid/m.glb' for j in jobs)

    def test_keeps_the_key_as_a_fallback(self, plan):
        jobs = plan({'s3Key': 'Data/aaa.xxh128', 'fileExtension': '.glb',
                     'downloadUrl': 'https://presigned.invalid/m.glb'})

        assert all(j['glbKey'] == 'Data/aaa.xxh128' for j in jobs)

    def test_url_is_absent_when_sdma_could_not_presign(self, plan):
        # The render then falls back to S3, so the key must still be there.
        jobs = plan({'s3Key': 'Data/aaa.xxh128', 'fileExtension': '.glb'})

        assert all(j['glbUrl'] is None for j in jobs)
        assert all(j['glbKey'] == 'Data/aaa.xxh128' for j in jobs)

    def test_carries_the_credentials_the_render_uploads_with(self, plan):
        # The render writes to SDMA's bucket with these rather than its own role,
        # which is why it needs no PutObject permission.
        jobs = plan({'s3Key': 'Data/aaa.xxh128', 'fileExtension': '.glb'})

        assert all(j['sdmaCreds']['AccessKeyId'] == 'x' for j in jobs)
        assert all(j['s3Prefix'] == 'SpatialDataManagementAssets' for j in jobs)

    def test_one_job_per_enabled_view(self, plan):
        jobs = plan({'s3Key': 'Data/aaa.xxh128', 'fileExtension': '.glb'})

        assert [j['viewName'] for j in jobs] == ['front', 'back']


class TestFailsLoudly:
    """A neutral return here would leave the asset looking queued forever."""

    def _handler(self, prepare, monkeypatch, **patches):
        monkeypatch.setattr(prepare, 'set_render_status', lambda *a, **k: None)
        monkeypatch.setattr(prepare, '_load_render_config',
                            lambda: {'views': {'enabled': ['front']}})
        for name, value in patches.items():
            monkeypatch.setattr(prepare.sdma_client, name, value)
        return lambda: prepare.lambda_handler(
            {'assetId': 'asset-1', '_fromStepFunctions': True}, None)

    def test_when_no_model_file_exists(self, prepare, monkeypatch):
        # An asset whose upload registered no model cannot be rendered. This is
        # what a nonexistent source file produces.
        run = self._handler(prepare, monkeypatch,
                            resolve_project_id=lambda a: 'project-1',
                            find_file_by_extension=lambda *a, **k: None)

        with pytest.raises(ValueError, match='No model file'):
            run()

    def test_when_the_project_cannot_be_resolved(self, prepare, monkeypatch):
        # Every downstream SDMA path is project-scoped, so proceeding without it
        # would fail later with a less obvious error.
        run = self._handler(prepare, monkeypatch,
                            resolve_project_id=lambda a: None)

        with pytest.raises(ValueError, match='project'):
            run()

    def test_when_the_asset_id_is_missing(self, prepare, monkeypatch):
        monkeypatch.setattr(prepare, 'set_render_status', lambda *a, **k: None)

        with pytest.raises(ValueError, match='assetId'):
            prepare.lambda_handler({'_fromStepFunctions': True}, None)
