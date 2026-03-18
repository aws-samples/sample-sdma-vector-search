# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Client for the SDMA REST API.

The Extension reaches SDMA through its documented API rather than by reading
SDMA's DynamoDB tables. Direct table access couples this code to SDMA's
internal schema -- table names, the ``AssetId-GSI`` index, attribute spellings,
and the undocumented ``#path-...`` suffix on ``FileObjectKey`` -- and bypasses
SDMA's per-project access control, so a schema change inside SDMA breaks the
Extension silently.

The signer and connection pool are built once per container. Each search page
makes two SDMA calls per result; rebuilding a botocore Session (which re-reads
config and re-resolves credentials) and a urllib3 PoolManager (which forces a
fresh TLS handshake) per call cost seconds per request.
"""
import json
import os
from typing import Any, Dict, List, Optional

import boto3
import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from log_utils import log_event

_http = urllib3.PoolManager(maxsize=16, retries=False)
_signer = SigV4Auth(
    boto3.Session().get_credentials(),
    'execute-api',
    os.environ.get('AWS_REGION', 'ap-northeast-1'),
)


class SdmaError(Exception):
    """An SDMA API call failed in a way the caller cannot work around."""


def _request(method: str, path: str) -> tuple:
    """Return (status, parsed body or None) for a signed SDMA API call."""
    endpoint = os.environ.get('SDMA_API_ENDPOINT')
    if not endpoint:
        raise SdmaError('SDMA_API_ENDPOINT is not configured')

    url = f"{endpoint}{path}"
    request = AWSRequest(method=method, url=url)
    _signer.add_auth(request)
    response = _http.request(method, url, headers=dict(request.headers))

    if response.status == 200:
        return response.status, json.loads(response.data.decode())
    return response.status, None


def _get(path: str, expected_missing: bool = False) -> Optional[dict]:
    """GET a path, returning None on 404.

    Anything other than 200 or 404 is logged: a silent None is what let a
    thumbnail regression go unnoticed for a whole release. 404 is logged only
    when the caller does not expect it, since probing for existence uses it.
    """
    status, body = _request('GET', path)
    if status == 200:
        return body
    if status != 404 or not expected_missing:
        log_event('sdma_api_error', path=path, status=status)
    return None


def _library_id() -> str:
    library_id = os.environ.get('SDMA_LIBRARY_ID')
    if not library_id:
        raise SdmaError('SDMA_LIBRARY_ID is not configured')
    return library_id


def asset_path(asset_id: str, project_id: str) -> str:
    """Return the API path for an asset. Both ids are required by SDMA."""
    return (f"/iam/libraries/{_library_id()}/projects/{project_id}"
            f"/assets/{asset_id}")


def list_projects() -> List[Dict[str, Any]]:
    """Return every project in the library."""
    body = _get(f"/iam/libraries/{_library_id()}/projects")
    return (body or {}).get('projects', [])


def resolve_project_id(asset_id: str) -> Optional[str]:
    """Find which project an asset belongs to.

    SDMA has no lookup from an asset id alone: GetAsset requires the project in
    its path, ListAssets ignores every filter parameter, there is no search
    endpoint, and the connector delivers only ``assetId`` even when
    ``project.projectId`` is mapped (verified against v1.6.0). So try each
    project until one returns the asset.

    Cost is O(projects) -- a 404 comes back immediately, and this runs once per
    upload in prepare-render, which then passes the id downstream. Callers that
    already know the project MUST pass it rather than calling this.
    """
    projects = list_projects()
    for project in projects:
        project_id = project.get('projectId')
        if not project_id:
            continue
        if _get(asset_path(asset_id, project_id), expected_missing=True):
            log_event('project_resolved', assetId=asset_id,
                      projectId=project_id, candidates=len(projects))
            return project_id

    log_event('project_not_resolved', assetId=asset_id,
              candidates=len(projects))
    return None


def get_asset(asset_id: str, project_id: str) -> Optional[dict]:
    """Return the asset record, or None if SDMA does not have it."""
    return _get(asset_path(asset_id, project_id))


def list_files(asset_id: str, project_id: str) -> List[Dict[str, Any]]:
    """Return the files registered to an asset.

    Each entry carries ``path`` and ``objectKey``. ``objectKey`` is the S3 key,
    already free of the ``#path-...`` suffix that SDMA's internal
    ``FileObjectKey`` attribute carries.
    """
    body = _get(f"{asset_path(asset_id, project_id)}/files")
    return (body or {}).get('files', [])


def find_file_by_extension(asset_id: str, project_id: str,
                           extensions: set) -> Optional[Dict[str, str]]:
    """Return the first registered file whose extension is in ``extensions``.

    Includes a ``downloadUrl`` so the caller can fetch the content through SDMA
    rather than reading the S3 object with its own role. The URL is presigned
    and valid for an hour, which is ample for a render fan-out.
    """
    for entry in list_files(asset_id, project_id):
        path = entry.get('path', '')
        extension = os.path.splitext(path)[1].lower()
        object_key = entry.get('objectKey')
        file_id = entry.get('fileId')
        if extension in extensions and object_key:
            found = {'s3Key': object_key, 'fileExtension': extension}
            if file_id:
                record = get_file(asset_id, project_id, file_id)
                if record and record.get('url'):
                    found['downloadUrl'] = record['url']
            return found
    return None


def get_file(asset_id: str, project_id: str, file_id: str) -> Optional[dict]:
    """Return one file's record, including the signed ``url`` SDMA mints.

    GetAsset does not carry a thumbnail URL -- only ``thumbnailFileId`` -- so
    resolving a thumbnail needs this second call. Going through SDMA rather than
    presigning the S3 object keeps SDMA's access control in the path and honours
    the solution's PreviewUrlType setting.
    """
    return _get(f"{asset_path(asset_id, project_id)}/files/{file_id}")


def get_thumbnail_url(asset_id: str, project_id: str,
                      asset: Optional[dict] = None) -> Optional[str]:
    """Return a signed URL for the asset's thumbnail, if it has one.

    Pass ``asset`` when the caller already fetched it, to save a call.
    """
    if asset is None:
        asset = get_asset(asset_id, project_id)
    if not asset:
        return None

    file_id = asset.get('thumbnailFileId')
    if not file_id:
        # Expected while rendering is still in flight. Persistent absence means
        # finalize-render did not add the thumbnail entry to the manifest.
        log_event('sdma_thumbnail_absent', assetId=asset_id)
        return None

    record = get_file(asset_id, project_id, file_id)
    if not record:
        return None

    url = record.get('url')
    if not url:
        log_event('sdma_thumbnail_url_absent', assetId=asset_id, fileId=file_id)
    return url
