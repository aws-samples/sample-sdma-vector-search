# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for the SDMA API client.

This module exists so the Extension stops reading SDMA's DynamoDB tables. Those
reads coupled it to SDMA's internal schema -- table names, the AssetId-GSI index,
attribute spellings, and an undocumented ``#path-...`` suffix on
``FileObjectKey`` -- and bypassed SDMA's per-project access control.
"""
import json
import pytest

from conftest import load_shared_module

@pytest.fixture
def sdma(monkeypatch):
    """Load the client with a fake signer and HTTP pool.

    Reloaded per test because the signer and pool are module-scoped: they are
    built once per Lambda container so a page of results does not pay a TLS
    handshake per call.
    """
    monkeypatch.setenv("SDMA_API_ENDPOINT", "https://example.invalid/stage")
    monkeypatch.setenv("SDMA_LIBRARY_ID", "library-" + "0" * 32)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    sdma_client = load_shared_module("sdma_client")

    class FakeResponse:
        def __init__(self, status, body):
            self.status = status
            self.data = json.dumps(body).encode() if body is not None else b""

    class FakePool:
        def __init__(self):
            self.responses = {}
            self.calls = []

        def request(self, method, url, headers=None, **kwargs):
            path = url.replace("https://example.invalid/stage", "")
            self.calls.append((method, path))
            status, body = self.responses.get(path, (404, None))
            return FakeResponse(status, body)

    pool = FakePool()
    monkeypatch.setattr(sdma_client, "_http", pool)
    monkeypatch.setattr(sdma_client._signer, "add_auth", lambda request: None)
    sdma_client.pool = pool
    yield sdma_client


def _base(sdma, asset="asset-1", project="project-1"):
    return sdma.asset_path(asset, project)


class TestAssetPath:
    def test_scopes_by_library_and_project(self, sdma):
        path = sdma.asset_path("asset-1", "project-1")
        assert path == (f"/iam/libraries/library-{'0' * 32}"
                        "/projects/project-1/assets/asset-1")

    def test_requires_the_library_to_be_configured(self, sdma, monkeypatch):
        # Every SDMA path is library-scoped, so a missing id must fail loudly
        # rather than produce a URL with an empty segment.
        monkeypatch.delenv("SDMA_LIBRARY_ID")
        with pytest.raises(sdma.SdmaError):
            sdma.asset_path("asset-1", "project-1")


class TestResolveProjectId:
    """SDMA has no lookup from an asset id alone: GetAsset needs the project in
    its path, ListAssets ignores every filter parameter, no search endpoint
    exists, and the connector delivers only assetId even when project.projectId
    is mapped. So each project is probed until one has the asset.
    """

    def _projects(self, sdma, *ids):
        sdma.pool.responses[f"/iam/libraries/library-{'0' * 32}/projects"] = (
            200, {"projects": [{"projectId": i} for i in ids]})

    def test_returns_the_project_that_has_the_asset(self, sdma):
        self._projects(sdma, "project-a", "project-b")
        sdma.pool.responses[_base(sdma, project="project-b")] = (200, {"assetId": "asset-1"})

        assert sdma.resolve_project_id("asset-1") == "project-b"

    def test_stops_probing_once_found(self, sdma):
        self._projects(sdma, "project-a", "project-b")
        sdma.pool.responses[_base(sdma, project="project-a")] = (200, {"assetId": "asset-1"})

        sdma.resolve_project_id("asset-1")
        probed = [p for _, p in sdma.pool.calls if p.endswith("/assets/asset-1")]
        assert len(probed) == 1

    def test_returns_none_when_no_project_has_it(self, sdma):
        self._projects(sdma, "project-a", "project-b")

        assert sdma.resolve_project_id("asset-1") is None

    def test_returns_none_when_there_are_no_projects(self, sdma):
        self._projects(sdma)

        assert sdma.resolve_project_id("asset-1") is None


class TestFindFileByExtension:
    """Replaces a GSI query against SDMA's FilesTable. The API's ``objectKey``
    is already free of the ``#path-...`` suffix the internal attribute carries,
    so no string surgery is needed.
    """

    def _files(self, sdma, *entries):
        sdma.pool.responses[f"{_base(sdma)}/files"] = (200, {"files": list(entries)})

    def test_returns_the_matching_model_file(self, sdma):
        self._files(sdma,
                    {"path": "screenshots/front.png", "objectKey": "Data/aaa.xxh128"},
                    {"path": "chair.glb", "objectKey": "Data/bbb.xxh128"})

        assert sdma.find_file_by_extension("asset-1", "project-1", {".glb"}) == {
            "s3Key": "Data/bbb.xxh128", "fileExtension": ".glb"}

    def test_includes_a_presigned_url_when_the_file_has_an_id(self, sdma):
        # The render reads the model through this URL rather than with its own
        # S3 permissions, so losing it silently would send every download back
        # to the direct-S3 fallback without anything failing.
        self._files(sdma, {"path": "chair.glb", "objectKey": "Data/bbb.xxh128",
                           "fileId": "file-1"})
        sdma.pool.responses[f"{_base(sdma)}/files/file-1"] = (
            200, {"url": "https://presigned.invalid/chair.glb?X-Amz-Expires=3600"})

        found = sdma.find_file_by_extension("asset-1", "project-1", {".glb"})

        assert found["downloadUrl"] == (
            "https://presigned.invalid/chair.glb?X-Amz-Expires=3600")
        assert found["s3Key"] == "Data/bbb.xxh128"

    def test_omits_the_url_when_the_file_has_no_id(self, sdma):
        # Without a fileId there is nothing to presign. The caller falls back to
        # s3Key, so the entry must still be returned.
        self._files(sdma, {"path": "chair.glb", "objectKey": "Data/bbb.xxh128"})

        found = sdma.find_file_by_extension("asset-1", "project-1", {".glb"})

        assert "downloadUrl" not in found

    def test_omits_the_url_when_get_file_fails(self, sdma):
        # GetFile 404s (or any non-200) must not lose the file itself.
        self._files(sdma, {"path": "chair.glb", "objectKey": "Data/bbb.xxh128",
                           "fileId": "file-1"})

        found = sdma.find_file_by_extension("asset-1", "project-1", {".glb"})

        assert found["s3Key"] == "Data/bbb.xxh128"
        assert "downloadUrl" not in found

    def test_matches_extensions_case_insensitively(self, sdma):
        self._files(sdma, {"path": "Chair.GLB", "objectKey": "Data/bbb.xxh128"})

        assert sdma.find_file_by_extension("asset-1", "project-1", {".glb"})

    def test_returns_none_when_nothing_matches(self, sdma):
        self._files(sdma, {"path": "notes.txt", "objectKey": "Data/ccc.xxh128"})

        assert sdma.find_file_by_extension("asset-1", "project-1", {".glb"}) is None

    def test_skips_entries_without_an_object_key(self, sdma):
        self._files(sdma, {"path": "chair.glb"})

        assert sdma.find_file_by_extension("asset-1", "project-1", {".glb"}) is None


class TestGetThumbnailUrl:
    """GetAsset carries no thumbnail URL, only ``thumbnailFileId``; GetFile is
    what mints the signed url. Going through SDMA rather than presigning the S3
    object keeps SDMA's access control in the path.
    """

    def test_chains_get_asset_then_get_file(self, sdma):
        sdma.pool.responses[_base(sdma)] = (200, {"thumbnailFileId": "file-1"})
        sdma.pool.responses[f"{_base(sdma)}/files/file-1"] = (
            200, {"url": "https://cdn.invalid/thumb.png"})

        assert sdma.get_thumbnail_url("asset-1", "project-1") == "https://cdn.invalid/thumb.png"

    def test_reuses_a_supplied_asset_record(self, sdma):
        sdma.pool.responses[f"{_base(sdma)}/files/file-1"] = (200, {"url": "https://cdn.invalid/t"})

        sdma.get_thumbnail_url("asset-1", "project-1", asset={"thumbnailFileId": "file-1"})
        assert not any(p == _base(sdma) for _, p in sdma.pool.calls)

    def test_returns_none_when_the_asset_has_no_thumbnail_yet(self, sdma):
        sdma.pool.responses[_base(sdma)] = (200, {"assetName": "chair"})

        assert sdma.get_thumbnail_url("asset-1", "project-1") is None
