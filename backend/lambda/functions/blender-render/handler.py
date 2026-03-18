# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Lambda handler for single-view Blender rendering

Renders a single view of a 3D asset using Blender Cycles.
Designed to be invoked in parallel (8 instances for 8 views).
"""
import os
import json
import subprocess
import tempfile
import time
from typing import Dict, Any

import boto3
import urllib3
from botocore.exceptions import ClientError

# Import system defaults and utilities
from system_defaults import DEFAULT_VIEWS, DEFAULT_RENDER_TIMEOUT
from common_constants import JOB_STATUS
from log_utils import log_event

# Initialize S3 client
s3_client = boto3.client('s3')


def download_model(local_path: str, url: str = None,
                   bucket: str = None, key: str = None) -> None:
    """Fetch the model to a local path.

    Prefer the presigned URL SDMA issued: the download then goes through SDMA's
    access control rather than this function's own S3 permissions. Falls back to
    a direct S3 read, which a direct invocation without a URL still needs.
    """
    if url:
        log_event('downloading_model_via_sdma_url', localPath=local_path)
        response = urllib3.PoolManager().request(
            'GET', url, preload_content=False, retries=urllib3.Retry(3))
        if response.status != 200:
            raise RuntimeError(
                f"SDMA download returned HTTP {response.status}")
        with open(local_path, 'wb') as handle:
            for chunk in response.stream(1024 * 1024):
                handle.write(chunk)
        response.release_conn()
        return

    log_event('downloading_model_from_s3', bucket=bucket, key=key,
              localPath=local_path)
    s3_client.download_file(bucket, key, local_path)


def upload_to_cas(local_path: str, bucket: str, s3_prefix: str,
                  sdma_creds: dict) -> tuple:
    """Upload file to SDMA CAS using SDMA-issued credentials.

    Returns (xxh128 hash, byte size). The size is reported because
    finalize-render needs it for the manifest entry, and it would otherwise
    have to HEAD the object back out of SDMA's bucket to learn something this
    function already knows.
    """
    import xxhash

    with open(local_path, 'rb') as f:
        data = f.read()

    file_hash = xxhash.xxh128(data).hexdigest()
    cas_key = f"{s3_prefix}/Data/{file_hash}.xxh128"

    temp_session = boto3.Session(
        aws_access_key_id=sdma_creds['AccessKeyId'],
        aws_secret_access_key=sdma_creds['SecretAccessKey'],
        aws_session_token=sdma_creds['SessionToken'],
    )
    temp_s3 = temp_session.client('s3')
    temp_s3.put_object(Bucket=bucket, Key=cas_key, Body=data, ContentType='image/png')

    log_event('uploaded_to_cas', bucket=bucket, casKey=cas_key, hash=file_hash, size=len(data))
    return file_hash, len(data)


def render_single_view(
    model_path: str,
    view_name: str,
    output_path: str,
    render_config: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Render a single view using Blender

    Args:
        model_path: Path to the 3D model file
        view_name: Name of the view to render
        output_path: Path for the output PNG file
        render_config: Optional rendering configuration

    Returns:
        Dictionary with render results
    """
    script_path = os.path.join(
        os.path.dirname(__file__),
        'blender_scripts',
        'single_view.py'
    )

    # Build Blender command
    cmd = [
        'blender',
        '--background',
        '--python', script_path,
        '--',
        '--model_path', model_path,
        '--view_name', view_name,
        '--output_path', output_path
    ]

    # Add config as JSON argument if provided
    if render_config:
        cmd.extend(['--config', json.dumps(render_config)])

    log_event('executing_blender',
              command=' '.join(cmd[:8]) + ' ...',
              viewName=view_name,
              hasConfig=render_config is not None)

    start_time = time.time()

    # `timeout.render_seconds` is the key in config/rendering/default.yaml, which
    # prepare-render passes through as loaded. Reading any other spelling here
    # silently ignores the config and always uses the default.
    timeout = DEFAULT_RENDER_TIMEOUT
    if render_config:
        timeout = render_config.get('timeout', {}).get('render_seconds',
                                                       DEFAULT_RENDER_TIMEOUT)

    # Run Blender
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout
    )

    duration = time.time() - start_time

    if result.returncode != 0:
        log_event('blender_error',
                  returnCode=result.returncode,
                  stdout=result.stdout[-2000:] if result.stdout else '',
                  stderr=result.stderr[-2000:] if result.stderr else '')
        raise RuntimeError(f"Blender render failed: {result.stderr}")

    log_event('render_completed',
              viewName=view_name,
              durationSeconds=round(duration, 2))

    return {
        'success': True,
        'viewName': view_name,
        'durationSeconds': duration
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for single-view rendering

    Event:
    {
        "assetId": "asset-xxx",
        "bucket": "sdma-bucket",
        "glbKey": "assets/asset-xxx/files/file-xxx/model.glb",
        "viewName": "front",
        # Renders to SDMA CAS (Data/{hash}.xxh128)
        "renderId": "abc12345"
    }

    Returns:
    {
        "assetId": "asset-xxx",
        "viewName": "front",
        "outputKey": "...",
        "status": "SUCCESS",
        "durationSeconds": 5.2
    }
    """
    log_event('lambda_invoked', input=event)

    # Extract parameters
    asset_id = event.get('assetId')
    bucket = event.get('bucket')
    glb_key = event.get('glbKey')
    # Presigned by SDMA in prepare-render. Preferred over glbKey so the read
    # goes through SDMA's access control instead of this function's own S3
    # permissions; absent on a direct invocation.
    glb_url = event.get('glbUrl')
    file_extension = event.get('fileExtension', '.glb')  # Default to .glb
    view_name = event.get('viewName')
    render_id = event.get('renderId', 'unknown')
    render_config = event.get('renderConfig')  # Optional render config
    sdma_creds = event.get('sdmaCreds')  # SDMA write credentials
    s3_prefix = event.get('s3Prefix')  # SDMA CAS prefix

    # Validate inputs
    if not all([asset_id, bucket, glb_key, view_name, sdma_creds, s3_prefix]):
        return {
            'assetId': asset_id,
            'viewName': view_name,
            'status': JOB_STATUS['failed'],
            'error': 'Missing required parameters (assetId, bucket, glbKey, viewName, sdmaCreds, s3Prefix)'
        }

    # Get valid views from config or use defaults
    valid_views = DEFAULT_VIEWS
    if render_config:
        valid_views = render_config.get('views', {}).get('enabled', DEFAULT_VIEWS)

    if view_name not in valid_views:
        return {
            'assetId': asset_id,
            'viewName': view_name,
            'status': JOB_STATUS['failed'],
            'error': f'Invalid view name: {view_name}'
        }

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Use file extension from event (original filename extension)
            model_filename = f"model{file_extension}"
            model_path = os.path.join(tmpdir, model_filename)
            output_path = os.path.join(tmpdir, f"{view_name}.png")

            # Fetch the model, through SDMA when a presigned URL was supplied
            download_model(model_path, url=glb_url, bucket=bucket, key=glb_key)

            # Render the view
            render_result = render_single_view(model_path, view_name, output_path, render_config)

            # List files in tmpdir for debugging
            files_in_tmpdir = os.listdir(tmpdir)
            log_event('debug_tmpdir_contents', tmpdir=tmpdir, files=files_in_tmpdir)

            # Verify output exists
            if not os.path.exists(output_path):
                raise RuntimeError(f"Render output not found: {output_path}")

            # Upload result to SDMA CAS
            file_hash, file_size = upload_to_cas(output_path, bucket, s3_prefix, sdma_creds)

            return {
                'assetId': asset_id,
                'viewName': view_name,
                'fileHash': file_hash,
                # Reported so finalize-render can build the manifest entry
                # without reading the object back out of SDMA's bucket.
                'fileSize': file_size,
                'renderId': render_id,
                'status': JOB_STATUS['success'],
                'durationSeconds': render_result['durationSeconds']
            }

    except ClientError as e:
        error_code = e.response['Error']['Code']
        log_event('aws_error', errorCode=error_code, error=str(e))
        return {
            'assetId': asset_id,
            'viewName': view_name,
            'status': JOB_STATUS['failed'],
            'error': f'AWS error: {error_code}'
        }

    except subprocess.TimeoutExpired:
        log_event('render_timeout', viewName=view_name)
        return {
            'assetId': asset_id,
            'viewName': view_name,
            'status': JOB_STATUS['failed'],
            'error': 'Render timeout'
        }

    except Exception as e:
        log_event('unexpected_error', error=str(e), errorType=type(e).__name__)
        return {
            'assetId': asset_id,
            'viewName': view_name,
            'status': JOB_STATUS['failed'],
            'error': str(e)
        }
