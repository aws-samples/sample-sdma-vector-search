# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Prepare Render Lambda

Resolves asset info and builds the render plan for Step Functions Map state.
"""
import os
import json
import uuid
from typing import Dict, Any, Optional

import boto3
import yaml
from botocore.exceptions import ClientError

from log_utils import log_event
from asset_jobs import set_render_status
import sdma_client
from common_constants import JOB_STATUS
from rendering_constants import DEFAULT_VIEWS, SUPPORTED_EXTENSIONS, CONFIG_PATHS

s3_client = boto3.client('s3')

S3_BUCKET = os.environ.get('S3_BUCKET_NAME', '')
# The Extension's own configs live in a bucket this stack owns.
EXTENSION_BUCKET = os.environ.get('EXTENSION_BUCKET', '')

_render_config = None


SFN_ARN = os.environ.get('STATE_MACHINE_ARN', '')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Build render plan for Step Functions Map state.
    When invoked directly by SDMA connector (not from Step Functions),
    starts the Step Functions execution instead.

    Input:  { "assetId": "xxx", "bucket": "optional", "projectId": "optional" }
    Output: { "assetId", "renderId", "bucket", "renderJobs": [...] }
    """
    log_event('prepare_render_invoked', payload=event)

    # If invoked by SDMA connector (not from Step Functions), start the pipeline
    if SFN_ARN and not event.get('_fromStepFunctions'):
        sfn = boto3.client('stepfunctions')
        sfn.start_execution(
            stateMachineArn=SFN_ARN,
            input=json.dumps({**event, '_fromStepFunctions': True})
        )
        log_event('step_functions_started', stateMachineArn=SFN_ARN, assetId=event.get('assetId'))
        return {'status': 'PIPELINE_STARTED', 'assetId': event.get('assetId')}

    asset_id = event.get('assetId')
    bucket = event.get('bucket') or S3_BUCKET
    project_id = event.get('projectId')

    if not asset_id:
        raise ValueError('Missing assetId')
    if not bucket:
        raise ValueError('S3_BUCKET_NAME not configured')

    # The connector delivers only assetId, and SDMA has no API that resolves an
    # asset without its project, so find it here -- once per upload -- and pass
    # it downstream in the payload rather than making each function repeat it.
    if not project_id:
        project_id = sdma_client.resolve_project_id(asset_id)
    if not project_id:
        set_render_status(asset_id, JOB_STATUS['failed'])
        raise ValueError(f'Could not resolve the project for asset {asset_id}')

    render_id = str(uuid.uuid4())[:8]
    render_config = _load_render_config()
    enabled_views = render_config.get('views', {}).get('enabled', DEFAULT_VIEWS)
    supported_extensions = set(render_config.get('supported_extensions', SUPPORTED_EXTENSIONS))

    model_info = sdma_client.find_file_by_extension(
        asset_id, project_id, supported_extensions)
    if not model_info:
        set_render_status(asset_id, JOB_STATUS['failed'])
        raise ValueError(f'No model file found for asset {asset_id}')

    set_render_status(asset_id, JOB_STATUS['rendering'], render_id, current=True)

    # Get SDMA write credentials so Blender Lambdas write directly to CAS
    sdma_creds = _get_sdma_write_credentials(asset_id, project_id)

    render_jobs = [
        {
            'assetId': asset_id,
            'bucket': bucket,
            'glbKey': model_info['s3Key'],
            # Presigned by SDMA, so the render reads the model through SDMA's
            # access control instead of with its own S3 permissions. glbKey
            # stays as a fallback for a direct invocation without a URL.
            'glbUrl': model_info.get('downloadUrl'),
            'fileExtension': model_info['fileExtension'],
            'viewName': view,
            'renderId': render_id,
            'renderConfig': render_config,
            'sdmaCreds': sdma_creds,
            's3Prefix': sdma_creds.get('s3Prefix', 'SpatialDataManagementAssets') if sdma_creds else None,
        }
        for view in enabled_views
    ]

    log_event('render_plan_created', assetId=asset_id, renderId=render_id, viewCount=len(render_jobs),
              sdmaCredsObtained=sdma_creds is not None)

    return {
        'assetId': asset_id,
        'renderId': render_id,
        'bucket': bucket,
        'projectId': project_id,
        'enabledViews': enabled_views,
        'renderJobs': render_jobs,
        'sdmaCreds': sdma_creds,
    }




def _load_render_config() -> Dict[str, Any]:
    """Load rendering config from S3, with defaults fallback."""
    global _render_config
    if _render_config is not None:
        return _render_config

    for key in [CONFIG_PATHS['rendering'], CONFIG_PATHS['rendering_json_fallback']]:
        try:
            body = s3_client.get_object(Bucket=EXTENSION_BUCKET, Key=key)['Body'].read().decode('utf-8')
            raw = yaml.safe_load(body) if key.endswith(('.yaml', '.yml')) else json.loads(body)
            if 'rendering' in raw:
                raw = raw['rendering']
            if 'views' not in raw:
                raw['views'] = {'enabled': DEFAULT_VIEWS}
            elif isinstance(raw['views'], list):
                raw['views'] = {'enabled': raw['views']}
            _render_config = raw
            return _render_config
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchKey':
                log_event('render_config_load_error', error=str(e))
            continue

    _render_config = {'views': {'enabled': DEFAULT_VIEWS}, 'supported_extensions': list(SUPPORTED_EXTENSIONS)}
    return _render_config


SDMA_API_ENDPOINT = os.environ.get('SDMA_API_ENDPOINT', '')
REGION = os.environ.get('AWS_REGION', os.environ.get('AWS_DEFAULT_REGION', ''))


def _get_sdma_write_credentials(asset_id: str,
                                project_id: str) -> Optional[Dict[str, Any]]:
    """Get SDMA write credentials for CAS storage via the SDMA API.

    Blender writes renders into SDMA's content-addressed store with these
    short-lived credentials rather than with its own role, so the write goes
    through SDMA's access control and lands in its audit trail.

    Returns a dict with AccessKeyId, SecretAccessKey, SessionToken, bucket and
    s3Prefix, or None on failure.
    """
    if not SDMA_API_ENDPOINT:
        log_event('sdma_creds_skipped', reason='SDMA_API_ENDPOINT not configured')
        return None

    asset = sdma_client.get_asset(asset_id, project_id)
    if not asset:
        log_event('sdma_creds_skipped', reason='asset not found')
        return None
    s3_prefix = asset.get('s3Prefix', 'SpatialDataManagementAssets')

    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    import urllib3

    url = (f"{SDMA_API_ENDPOINT}{sdma_client.asset_path(asset_id, project_id)}"
           f"/credentials?operation=write&location=manifest_and_data")

    session = boto3.Session()
    credentials = session.get_credentials()
    request = AWSRequest(method='GET', url=url)
    SigV4Auth(credentials, 'execute-api', REGION).add_auth(request)

    http = urllib3.PoolManager()
    resp = http.request('GET', url, headers=dict(request.headers))

    if resp.status != 200:
        log_event('sdma_creds_api_error', status=resp.status, body=resp.data.decode()[:200])
        return None

    creds = json.loads(resp.data.decode())
    creds['s3Prefix'] = s3_prefix
    log_event('sdma_write_credentials_obtained', assetId=asset_id)
    return creds


