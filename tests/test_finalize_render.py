# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for the manifest update, which is where a thumbnail is won or lost.

SDMA derives an asset's thumbnail from an entry in its manifest, so a manifest
update that silently does nothing produces an asset that renders correctly and
still shows no thumbnail -- the exact failure this repository hit twice. Two
causes are covered here: the ``xxhash`` import that skips the whole update, and
the byte sizes, which used to be recovered with a ``head_object`` per view and
are now reported by the render itself.
"""
import json

import pytest

from conftest import load_function_module


@pytest.fixture
def finalize(monkeypatch):
    monkeypatch.setenv("SDMA_API_ENDPOINT", "https://example.invalid/stage")
    monkeypatch.setenv("SDMA_LIBRARY_ID", "library-" + "0" * 32)
    monkeypatch.setenv("ASSET_JOBS_TABLE", "test-asset-jobs")
    return load_function_module("finalize-render", "handler")


class FakeS3:
    """Records the manifest that was written, and serves the one to read."""

    def __init__(self, existing):
        self.existing = existing
        self.puts = []

    def get_object(self, Bucket, Key):
        return {'Body': _Body(json.dumps(self.existing).encode())}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class _FakeSession:
    """Stands in for the session built from SDMA's short-lived credentials."""

    def __init__(self, s3):
        self._s3 = s3

    def client(self, name):
        assert name == 's3'
        return self._s3


def _written_manifest(s3):
    assert s3.puts, "no manifest was written"
    return json.loads(s3.puts[-1]['Body'].decode())


def _paths(manifest):
    return {entry['path']: entry for entry in manifest['paths']}


class TestManifestSizes:
    """The sizes come from the render, which computed them while uploading."""

    def _update(self, finalize, monkeypatch, file_sizes):
        s3 = FakeS3({'paths': [{'path': 'chair.glb', 'hash': 'aaa',
                                'mtime': 1, 'size': 100}]})
        monkeypatch.setattr(finalize, 's3_client', s3)
        # The write goes through a session built from SDMA's short-lived
        # credentials, so the fake stands in for that session.
        monkeypatch.setattr(finalize.boto3, 'Session',
                            lambda **kwargs: _FakeSession(s3))
        monkeypatch.setattr(finalize.sdma_client, 'get_asset',
                            lambda *a, **k: {'manifestObjectKey': 'Manifests/x/aaa_input'})
        ok = finalize._update_sdma_manifest(
            'asset-1', 'project-1', 'bucket',
            {'AccessKeyId': 'x', 'SecretAccessKey': 'y', 'SessionToken': 'z'},
            file_hashes={'front': 'hhh', 'perspective_front': 'ppp'},
            file_sizes=file_sizes,
        )
        return ok, s3

    def test_records_the_size_the_render_reported(self, finalize, monkeypatch):
        ok, s3 = self._update(finalize, monkeypatch,
                              {'front': 4096, 'perspective_front': 8192})

        assert ok
        entries = _paths(_written_manifest(s3))
        assert entries['screenshots/front.png']['size'] == 4096

    def test_reuses_the_perspective_size_for_the_thumbnail(self, finalize, monkeypatch):
        # The thumbnail entry points at the same object as perspective_front, so
        # it must carry that view's size rather than zero.
        ok, s3 = self._update(finalize, monkeypatch,
                              {'front': 4096, 'perspective_front': 8192})

        entries = _paths(_written_manifest(s3))
        thumb = entries['.spatial_data_mgmt_asset_thumbnail.jpg']
        assert thumb['size'] == 8192
        assert thumb['hash'] == 'ppp'

    def test_falls_back_to_zero_for_a_view_with_no_reported_size(
            self, finalize, monkeypatch):
        # An older render that does not report sizes must still produce entries;
        # SDMA tolerates a zero size, and losing the entry loses the thumbnail.
        ok, s3 = self._update(finalize, monkeypatch, {})

        entries = _paths(_written_manifest(s3))
        assert entries['screenshots/front.png']['size'] == 0
        assert '.spatial_data_mgmt_asset_thumbnail.jpg' in entries

    def test_keeps_the_entries_already_in_the_manifest(self, finalize, monkeypatch):
        ok, s3 = self._update(finalize, monkeypatch, {'front': 1})

        assert 'chair.glb' in _paths(_written_manifest(s3))


class TestManifestSkip:
    def test_reports_failure_when_xxhash_is_missing(self, finalize, monkeypatch):
        # The Makefile must resolve wheels for the Lambda runtime. When it does
        # not, this path fires -- and it has to be visible, because it takes the
        # thumbnail entry down with it.
        import builtins
        real_import = builtins.__import__

        def no_xxhash(name, *args, **kwargs):
            if name == 'xxhash':
                raise ImportError('no xxhash')
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', no_xxhash)

        assert finalize._update_sdma_manifest(
            'asset-1', 'project-1', 'bucket', {}, {'front': 'h'}, {}) is False
