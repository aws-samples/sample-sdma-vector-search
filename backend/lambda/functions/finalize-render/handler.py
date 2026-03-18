# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Finalize Render Lambda

Writes rendered views directly into SDMA content-addressed storage (CAS),
updates the SDMA manifest, and notifies the backend. Rendered images never
pass through an intermediate staging prefix.
"""
import os
import json
import time
from typing import Dict, Any
from datetime import datetime

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError
import urllib3

from log_utils import log_event
from asset_jobs import set_render_status
import sdma_client
from common_constants import JOB_STATUS, HTTP

s3_client = boto3.client('s3')
http = urllib3.PoolManager()

SDMA_API_ENDPOINT = os.environ.get('SDMA_API_ENDPOINT', '')
REGION = os.environ.get('AWS_REGION', os.environ.get('AWS_DEFAULT_REGION', ''))

SCREENSHOT_FOLDER = 'screenshots'


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Finalize render, recording a failure in the job table before re-raising.

    Without this the asset is left reading ``RENDERING`` forever when anything
    here fails: the state machine's Catch routes to a Fail state, which cannot
    update DynamoDB, and this is the one pipeline stage that had no failure path
    of its own. The status is advisory, so a failure to record it is logged and
    swallowed rather than replacing the original error.
    """
    try:
        return _finalize(event, context)
    except Exception as e:
        asset_id = (event.get('prepareResult') or {}).get('assetId')
        if asset_id:
            set_render_status(asset_id, JOB_STATUS['failed'])
        log_event('finalize_render_failed', assetId=asset_id, error=str(e))
        raise


def _finalize(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Finalize render: update SDMA manifest with rendered views.

    Input:  { "prepareResult": {...}, "renderResults": [...] }
    Output: { "assetId", "bucket", "outputPath", "projectId", "status", "fileHashes" }
    """
    log_event('finalize_render_invoked', payload=event)

    prepare = event.get('prepareResult', {})
    asset_id = prepare.get('assetId')
    render_id = prepare.get('renderId')
    bucket = prepare.get('bucket')
    enabled_views = prepare.get('enabledViews', [])
    project_id = prepare.get('projectId')
    sdma_creds = prepare.get('sdmaCreds')

    if not asset_id or not render_id or not bucket:
        raise ValueError('Missing required fields from prepareResult')

    # Collect successful render results
    render_results = event.get('renderResults', [])
    failed = [r for r in render_results if r.get('status') == 'FAILED']
    succeeded = [r for r in render_results if r.get('status') == 'SUCCESS']

    if failed:
        log_event('render_failures_detected', assetId=asset_id, failedCount=len(failed))

    # Collect the hash and byte size each render reported. blender-render knows
    # both when it uploads, so taking them from here avoids HEADing the objects
    # back out of SDMA's bucket to learn what the pipeline already computed.
    file_hashes = {}
    file_sizes = {}
    for r in succeeded:
        if r.get('fileHash'):
            file_hashes[r['viewName']] = r['fileHash']
            file_sizes[r['viewName']] = r.get('fileSize', 0)

    # Update SDMA manifest if we have CAS hashes
    manifest_updated = False
    if file_hashes and sdma_creds:
        manifest_updated = _update_sdma_manifest(
            asset_id=asset_id,
            project_id=project_id,
            bucket=bucket,
            sdma_creds=sdma_creds,
            file_hashes=file_hashes,
            file_sizes=file_sizes,
        )

    set_render_status(asset_id, JOB_STATUS['completed'], render_id)

    # Build output path for AI Tag Lambda (now uses CAS hashes)
    s3_prefix = sdma_creds.get('s3Prefix', 'SpatialDataManagementAssets') if sdma_creds else None
    output_info = {
        'assetId': asset_id,
        'bucketName': bucket,
        'projectId': project_id,
        'status': JOB_STATUS['completed'],
        'fileHashes': file_hashes,
        's3Prefix': s3_prefix,
        'manifestUpdated': manifest_updated,
    }

    log_event('render_finalized', **output_info)
    return output_info


def _update_sdma_manifest(
    asset_id: str,
    project_id: str,
    bucket: str,
    sdma_creds: Dict[str, Any],
    file_hashes: Dict[str, str],
    file_sizes: Dict[str, int],
) -> bool:
    """Update SDMA asset manifest with rendered file hashes.

    Reads existing manifest, appends new screenshot entries, writes new manifest
    with _input suffix to trigger SDMA backend processing.
    """
    try:
        import xxhash
    except ImportError:
        # Not a soft failure: the thumbnail entry is added as part of this
        # manifest update, so skipping it means every asset renders without a
        # thumbnail. If this fires, backend/lambda/Makefile is resolving wheels
        # for the build host instead of the Lambda runtime.
        log_event('manifest_update_skipped', reason='xxhash not available')
        return False

    asset = sdma_client.get_asset(asset_id, project_id)
    if not asset:
        log_event('manifest_update_skipped', reason='asset not found')
        return False

    manifest_key = asset.get('manifestObjectKey')
    s3_prefix = asset.get('s3Prefix', 'SpatialDataManagementAssets')
    library_id = asset.get('libraryId')

    if not manifest_key:
        log_event('manifest_update_skipped', reason='No manifest key found')
        return False

    # Read current manifest
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=manifest_key)
        manifest = json.loads(resp['Body'].read().decode())
    except ClientError as e:
        log_event('manifest_read_error', error=str(e))
        return False

    # Build new manifest entries for screenshots
    existing_paths = {p['path'] for p in manifest.get('paths', [])}
    mtime = int(time.time())
    new_entries = []

    for view_name, file_hash in file_hashes.items():
        path = f"{SCREENSHOT_FOLDER}/{view_name}.png"
        if path not in existing_paths:
            new_entries.append({
                'hash': file_hash,
                'mtime': mtime,
                'path': path,
                # Reported by blender-render, which computed it when uploading.
                'size': file_sizes.get(view_name, 0),
            })

    # Add thumbnail entry (SDMA uses this to set thumbnailUrl on the asset)
    thumbnail_path = '.spatial_data_mgmt_asset_thumbnail.jpg'
    if thumbnail_path not in existing_paths and 'perspective_front' in file_hashes:
        # Reuse perspective_front hash — SDMA will serve it as thumbnail
        new_entries.append({
            'hash': file_hashes['perspective_front'],
            'mtime': mtime,
            'path': thumbnail_path,
            'size': file_sizes.get('perspective_front', 0),
        })

    if not new_entries:
        log_event('manifest_no_new_entries', assetId=asset_id)
        return False

    # Append to manifest
    manifest['paths'].extend(new_entries)
    manifest['totalSize'] = sum(p.get('size', 0) for p in manifest['paths'])

    # Write updated manifest with new _input key
    manifest_bytes = json.dumps(manifest).encode('utf-8')
    manifest_hash = xxhash.xxh128(manifest_bytes).hexdigest()
    manifest_base = manifest_key.rsplit('/', 1)[0]
    new_manifest_key = f"{manifest_base}/{manifest_hash}_input"

    # Use SDMA credentials to write manifest
    temp_session = boto3.Session(
        aws_access_key_id=sdma_creds['AccessKeyId'],
        aws_secret_access_key=sdma_creds['SecretAccessKey'],
        aws_session_token=sdma_creds['SessionToken'],
    )
    temp_s3 = temp_session.client('s3')
    temp_s3.put_object(
        Bucket=bucket,
        Key=new_manifest_key,
        Body=manifest_bytes,
        ContentType=HTTP['content_type_json'],
    )

    log_event('manifest_updated', assetId=asset_id, newKey=new_manifest_key,
              newEntries=len(new_entries), totalPaths=len(manifest['paths']))

    # Notify SDMA backend
    if SDMA_API_ENDPOINT and library_id and project_id:
        _notify_sdma_backend(library_id, project_id, asset_id, new_manifest_key, manifest_hash)

    return True


def _notify_sdma_backend(library_id: str, project_id: str, asset_id: str,
                         manifest_key: str, manifest_hash: str):
    """Notify SDMA backend about manifest update via UpdateAsset API."""
    url = (f"{SDMA_API_ENDPOINT}/iam/libraries/{library_id}/projects/{project_id}"
           f"/assets/{asset_id}")
    body = json.dumps({
        'manifestObjectKey': manifest_key,
        'manifestHash': manifest_hash,
    })

    session = boto3.Session()
    credentials = session.get_credentials()
    request = AWSRequest(method='PUT', url=url, data=body,
                         headers={'Content-Type': HTTP['content_type_json']})
    SigV4Auth(credentials, 'execute-api', REGION).add_auth(request)

    resp = http.request('PUT', url, headers=dict(request.headers), body=body)
    if resp.status in (200, 204):
        log_event('sdma_backend_notified', assetId=asset_id)
    else:
        log_event('sdma_backend_notify_error', status=resp.status, body=resp.data.decode()[:200])


