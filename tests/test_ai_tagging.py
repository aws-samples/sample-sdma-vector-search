# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for how the rendered views are fetched.

The renders are read through the presigned URLs SDMA issues, so the download
goes through SDMA's access control rather than this function's own S3
permissions. A direct S3 read remains as a fallback, which the first of the two
invocations the pipeline makes actually needs -- the renders are not registered
yet at that point. Because the fallback succeeds quietly, a regression that
routes *every* download back to S3 would leave no failing behaviour to notice,
so which path was taken is asserted here.
"""
import pytest

from conftest import load_function_module


@pytest.fixture
def tagging(monkeypatch):
    monkeypatch.setenv("SDMA_API_ENDPOINT", "https://example.invalid/stage")
    monkeypatch.setenv("SDMA_LIBRARY_ID", "library-" + "0" * 32)
    monkeypatch.setenv("ASSET_JOBS_TABLE", "test-asset-jobs")
    monkeypatch.setenv("EXTENSION_BUCKET", "test-extension-data")
    return load_function_module("ai-tag-generation", "handler")


class FakeS3:
    def __init__(self):
        self.downloads = []

    def download_file(self, bucket, key, local_path):
        self.downloads.append((bucket, key))
        with open(local_path, 'wb') as handle:
            handle.write(b'from-s3')


class _Response:
    def __init__(self, status, body=b'from-sdma'):
        self.status = status
        self._body = body
        self.released = False

    def stream(self, size):
        yield self._body

    def release_conn(self):
        self.released = True


class FakePool:
    """Serves presigned URLs, and records which ones were fetched."""

    def __init__(self, status=200):
        self.status = status
        self.fetched = []

    def request(self, method, url, **kwargs):
        self.fetched.append(url)
        return _Response(self.status)


@pytest.fixture
def wiring(tagging, monkeypatch, tmp_path):
    s3 = FakeS3()
    pool = FakePool()
    monkeypatch.setattr(tagging, 's3_client', s3)
    monkeypatch.setattr(tagging.urllib3, 'PoolManager', lambda *a, **k: pool)
    return tagging, s3, pool, str(tmp_path)


def _register(tagging, monkeypatch, *views, url='https://presigned.invalid/v.png'):
    """Make ListFiles report the given views as registered, each presignable."""
    files = [{'path': f'screenshots/{v}.png', 'fileId': f'file-{v}'} for v in views]
    monkeypatch.setattr(tagging.sdma_client, 'list_files', lambda *a, **k: files)
    monkeypatch.setattr(tagging.sdma_client, 'get_file',
                        lambda *a, **k: {'url': url})


class TestDownloadRenderedImages:
    def test_reads_through_sdma_when_the_view_is_registered(
            self, wiring, monkeypatch):
        tagging, s3, pool, tmp = wiring
        _register(tagging, monkeypatch, 'front', 'back')

        images = tagging.download_rendered_images(
            'sdma-bucket', 'asset-1', tmp,
            file_hashes={'front': 'h1', 'back': 'h2'},
            s3_prefix='Prefix', project_id='project-1')

        assert set(images) == {'front', 'back'}
        assert len(pool.fetched) == 2
        assert s3.downloads == [], "should not have fallen back to S3"

    def test_falls_back_to_s3_for_a_view_not_yet_registered(
            self, wiring, monkeypatch):
        # This is the first of the two invocations: finalize-render has not
        # registered the renders yet, so only the CAS objects exist.
        tagging, s3, pool, tmp = wiring
        _register(tagging, monkeypatch, 'front')

        images = tagging.download_rendered_images(
            'sdma-bucket', 'asset-1', tmp,
            file_hashes={'front': 'h1', 'back': 'h2'},
            s3_prefix='Prefix', project_id='project-1')

        assert set(images) == {'front', 'back'}
        assert len(pool.fetched) == 1
        assert s3.downloads == [('sdma-bucket', 'Prefix/Data/h2.xxh128')]

    def test_falls_back_to_s3_without_a_project(self, wiring, monkeypatch):
        # Every SDMA path is project-scoped, so with no project there is nothing
        # to presign -- a direct invocation takes this path.
        tagging, s3, pool, tmp = wiring

        images = tagging.download_rendered_images(
            'sdma-bucket', 'asset-1', tmp,
            file_hashes={'front': 'h1'}, s3_prefix='Prefix')

        assert set(images) == {'front'}
        assert pool.fetched == []
        assert s3.downloads == [('sdma-bucket', 'Prefix/Data/h1.xxh128')]

    def test_falls_back_to_s3_when_the_presigned_fetch_fails(
            self, tagging, monkeypatch, tmp_path):
        # An expired or rejected URL must not lose the view.
        s3 = FakeS3()
        pool = FakePool(status=403)
        monkeypatch.setattr(tagging, 's3_client', s3)
        monkeypatch.setattr(tagging.urllib3, 'PoolManager', lambda *a, **k: pool)
        _register(tagging, monkeypatch, 'front')

        images = tagging.download_rendered_images(
            'sdma-bucket', 'asset-1', str(tmp_path),
            file_hashes={'front': 'h1'}, s3_prefix='Prefix',
            project_id='project-1')

        assert set(images) == {'front'}
        assert s3.downloads == [('sdma-bucket', 'Prefix/Data/h1.xxh128')]

    def test_content_comes_from_sdma_not_s3(self, wiring, monkeypatch):
        # Asserting the path was taken is not enough: the bytes have to be the
        # ones the URL served.
        tagging, s3, pool, tmp = wiring
        _register(tagging, monkeypatch, 'front')

        images = tagging.download_rendered_images(
            'sdma-bucket', 'asset-1', tmp,
            file_hashes={'front': 'h1'}, s3_prefix='Prefix',
            project_id='project-1')

        with open(images['front'], 'rb') as handle:
            assert handle.read() == b'from-sdma'

    def test_requires_the_hashes_and_prefix(self, wiring):
        # Without these there is no CAS path and no view names, so continuing
        # would produce an empty result that looks like "no renders found".
        tagging, _, _, tmp = wiring

        with pytest.raises(ValueError):
            tagging.download_rendered_images('b', 'asset-1', tmp,
                                             file_hashes=None, s3_prefix='P')
        with pytest.raises(ValueError):
            tagging.download_rendered_images('b', 'asset-1', tmp,
                                             file_hashes={'front': 'h'},
                                             s3_prefix=None)

    def test_lists_files_once_for_the_whole_asset(self, wiring, monkeypatch):
        # One ListFiles per view would cost eight round trips per asset.
        tagging, _, _, tmp = wiring
        calls = []
        monkeypatch.setattr(tagging.sdma_client, 'list_files',
                            lambda *a, **k: calls.append(1) or [])
        monkeypatch.setattr(tagging.sdma_client, 'get_file', lambda *a, **k: None)

        tagging.download_rendered_images(
            'sdma-bucket', 'asset-1', tmp,
            file_hashes={f'v{i}': f'h{i}' for i in range(8)},
            s3_prefix='Prefix', project_id='project-1')

        assert len(calls) == 1
