# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for how the render fetches its model.

Blender itself runs in a subprocess, so the rendering is not exercised here --
but the download is, because it decides whether the read goes through SDMA's
access control or this function's own S3 permissions. The S3 fallback succeeds
quietly, so a regression that stops passing the presigned URL would produce
correct renders with the permission boundary silently widened.
"""
import pytest

from conftest import load_function_module


@pytest.fixture
def render():
    return load_function_module("blender-render", "handler")


class FakeS3:
    def __init__(self):
        self.downloads = []

    def download_file(self, bucket, key, local_path):
        self.downloads.append((bucket, key))
        with open(local_path, 'wb') as handle:
            handle.write(b'from-s3')


class _Response:
    def __init__(self, status, body=b'glb-bytes'):
        self.status = status
        self._body = body
        self.released = False

    def stream(self, size):
        yield self._body

    def release_conn(self):
        self.released = True


class FakePool:
    def __init__(self, status=200):
        self.status = status
        self.fetched = []

    def request(self, method, url, **kwargs):
        self.fetched.append(url)
        return _Response(self.status)


@pytest.fixture
def wiring(render, monkeypatch):
    s3 = FakeS3()
    pool = FakePool()
    monkeypatch.setattr(render, 's3_client', s3)
    monkeypatch.setattr(render.urllib3, 'PoolManager', lambda *a, **k: pool)
    return render, s3, pool


class TestDownloadModel:
    def test_prefers_the_presigned_url(self, wiring, tmp_path):
        render, s3, pool = wiring
        target = str(tmp_path / "model.glb")

        render.download_model(target, url='https://presigned.invalid/m.glb',
                              bucket='sdma-bucket', key='Data/aaa.xxh128')

        assert pool.fetched == ['https://presigned.invalid/m.glb']
        assert s3.downloads == [], "should not have read S3 directly"
        with open(target, 'rb') as handle:
            assert handle.read() == b'glb-bytes'

    def test_reads_s3_when_no_url_is_supplied(self, wiring, tmp_path):
        # A direct invocation carries no presigned URL.
        render, s3, pool = wiring
        target = str(tmp_path / "model.glb")

        render.download_model(target, bucket='sdma-bucket', key='Data/aaa.xxh128')

        assert pool.fetched == []
        assert s3.downloads == [('sdma-bucket', 'Data/aaa.xxh128')]

    def test_raises_when_the_presigned_fetch_fails(self, wiring, tmp_path):
        # An expired URL must fail loudly: rendering an empty file would produce
        # eight blank views and an asset that looks processed.
        render, s3, pool = wiring
        pool.status = 403

        with pytest.raises(RuntimeError, match='403'):
            render.download_model(str(tmp_path / "model.glb"),
                                  url='https://presigned.invalid/m.glb',
                                  bucket='sdma-bucket', key='Data/aaa.xxh128')

        assert s3.downloads == [], "a rejected URL must not silently fall back"
