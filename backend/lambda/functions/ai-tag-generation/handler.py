# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Lambda handler for AI tag generation from rendered 3D model images

Supports multiple invocation sources:
- SDMA Connector (GenericPublisher via LambdaInvokeRunner)
- Direct invocation

Generates AI tags and stores vector embeddings in DynamoDB for semantic search.
"""
import os
import json
import tempfile
from datetime import datetime
from typing import Dict, Any, List

import boto3
import urllib3
from botocore.exceptions import ClientError

from aws_clients import s3_client, dynamodb_client
from metadata import generate_structured_metadata, classify_size_category
from log_utils import log_event
from asset_jobs import set_ai_tag_status
import sdma_client
from common_constants import JOB_STATUS, HTTP
import category_config

# The Extension's own configs and intermediates live in a bucket this stack
# owns. SDMA's asset bucket is still read for rendered images, using the CAS
# keys SDMA's API hands back.
EXTENSION_BUCKET = os.environ.get('EXTENSION_BUCKET', '')


def get_sdma_asset_info(asset_id: str, project_id: str) -> Dict[str, Any]:
    """Fetch the asset's name from SDMA, for use as a classification hint."""
    asset = sdma_client.get_asset(asset_id, project_id) or {}
    return {'assetName': asset.get('assetName', '')}






def cleanup_asset_data(asset_id: str, bucket_name: str) -> Dict[str, Any]:
    """
    Clean up all extension data when an asset is deleted.

    Deletes:
    - DynamoDB vector record
    - Rendered screenshots (SDMA CAS)
    - AI metadata (assets/{assetId}/ai_metadata.json)

    Args:
        asset_id: Asset ID to clean up
        bucket_name: S3 bucket name

    Returns:
        Dictionary with cleanup results
    """
    deleted_objects = []
    errors = []

    # Delete from DynamoDB vector table
    vector_table = os.environ.get('VECTOR_TABLE_NAME')
    if vector_table:
        try:
            import boto3
            ddb = boto3.client('dynamodb')
            ddb.delete_item(TableName=vector_table, Key={'assetId': {'S': asset_id}})
            deleted_objects.append(f"dynamodb:{vector_table}/{asset_id}")
        except Exception as e:
            errors.append(f"DynamoDB delete failed: {e}")

    # S3 paths to clean up
    cleanup_prefixes = [
        f"assets/{asset_id}/ai_metadata.json",  # AI metadata
    ]

    log_event('cleanup_started', assetId=asset_id, bucket=bucket_name, prefixes=cleanup_prefixes)

    for prefix in cleanup_prefixes:
        try:
            # List objects with this prefix
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)

            objects_to_delete = []
            for page in pages:
                for obj in page.get('Contents', []):
                    objects_to_delete.append({'Key': obj['Key']})

            # Delete objects if any found
            if objects_to_delete:
                response = s3_client.delete_objects(
                    Bucket=bucket_name,
                    Delete={'Objects': objects_to_delete}
                )
                deleted_objects.extend([obj['Key'] for obj in objects_to_delete])

                log_event('objects_deleted', assetId=asset_id, prefix=prefix, count=len(objects_to_delete))

        except ClientError as e:
            error_msg = f"Failed to delete {prefix}: {str(e)}"
            errors.append(error_msg)
            log_event('cleanup_error', assetId=asset_id, prefix=prefix, error=str(e))

    result = {
        'assetId': asset_id,
        'deletedCount': len(deleted_objects),
        'deletedObjects': deleted_objects,
        'errors': errors,
        'success': len(errors) == 0
    }

    log_event('cleanup_completed', **result)

    return result


def download_rendered_images(bucket_name: str, asset_id: str, temp_dir: str,
                             file_hashes: Dict[str, str] = None,
                             s3_prefix: str = None,
                             project_id: str = None) -> Dict[str, str]:
    """
    Download the rendered views for an asset.

    Prefers SDMA's presigned URLs, so the read goes through SDMA's access
    control rather than this function's own S3 permissions. finalize-render
    registers the renders as ``screenshots/<view>.png``, so they are addressable
    through the API by then. Falls back to reading the CAS objects directly when
    the project is unknown or a view is not registered -- a direct invocation,
    or a render whose registration did not land.

    Args:
        bucket_name: SDMA asset bucket, for the fallback path
        asset_id: Asset ID
        temp_dir: Temporary directory for downloaded files
        file_hashes: Dict mapping viewName to xxh128 hash
        s3_prefix: SDMA S3 prefix for CAS paths
        project_id: SDMA project, required to reach the API

    Returns:
        Dictionary mapping view names to local file paths
    """
    if not file_hashes or not s3_prefix:
        raise ValueError(f"file_hashes and s3_prefix are required for asset {asset_id}")

    rendered_images = {}
    log_event('downloading_rendered_views', assetId=asset_id,
              viewCount=len(file_hashes), viaSdma=bool(project_id))

    # One ListFiles for the whole asset rather than one per view.
    urls_by_path = {}
    if project_id:
        for entry in sdma_client.list_files(asset_id, project_id):
            path, file_id = entry.get('path'), entry.get('fileId')
            if path and file_id:
                urls_by_path[path] = file_id

    http = urllib3.PoolManager()

    for view_name, file_hash in file_hashes.items():
        local_path = os.path.join(temp_dir, f"{view_name}.png")
        file_id = urls_by_path.get(f"screenshots/{view_name}.png")

        if project_id and file_id:
            record = sdma_client.get_file(asset_id, project_id, file_id)
            url = (record or {}).get('url')
            if url:
                response = http.request('GET', url, preload_content=False,
                                        retries=urllib3.Retry(3))
                if response.status == 200:
                    with open(local_path, 'wb') as handle:
                        for chunk in response.stream(1024 * 1024):
                            handle.write(chunk)
                    response.release_conn()
                    rendered_images[view_name] = local_path
                    log_event('image_downloaded', assetId=asset_id,
                              viewName=view_name, via='sdma')
                    continue
                response.release_conn()
                log_event('sdma_image_download_failed', assetId=asset_id,
                          viewName=view_name, status=response.status)

        cas_key = f"{s3_prefix}/Data/{file_hash}.xxh128"
        try:
            s3_client.download_file(bucket_name, cas_key, local_path)
            rendered_images[view_name] = local_path
            log_event('image_downloaded', assetId=asset_id, viewName=view_name,
                      s3Key=cas_key, via='s3')
        except ClientError as e:
            log_event('image_download_error', assetId=asset_id, viewName=view_name,
                      s3Key=cas_key, error=str(e))

    return rendered_images


def save_ai_metadata_to_s3(bucket_name: str, asset_id: str, ai_metadata: Dict[str, Any]) -> str:
    """
    Save AI-generated metadata to S3 (legacy format)

    Args:
        bucket_name: S3 bucket name
        asset_id: Asset ID
        ai_metadata: AI-generated metadata dictionary

    Returns:
        S3 key of the saved metadata file
    """
    s3_key = f"assets/{asset_id}/ai_metadata.json"

    s3_client.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=json.dumps(ai_metadata, indent=2),
        ContentType=HTTP['content_type_json']
    )

    log_event('ai_metadata_saved', assetId=asset_id, s3Key=s3_key)

    return s3_key




def parse_sdma_connector_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse SDMA Connector invocation event

    SDMA GenericPublisher (LambdaInvokeRunner) sends payload with mapped fields:
    {
        "assetId": "asset-xxx",
        "projectId": "project-xxx",
        "libraryId": "library-xxx"
    }

    Or SQS Record format (when using queue):
    {
        "Records": [{
            "body": "{\"resourceType\": 1, \"resourceId\": \"asset-xxx\", ...}"
        }]
    }

    Args:
        event: Lambda event payload

    Returns:
        Dictionary with parsed asset information
    """
    result = {
        'assetId': None,
        'projectId': None,
        'libraryId': None,
        'connectorId': None,
        'eventType': None,
        'source': 'unknown'
    }

    # Check for SQS Records format (SDMA connector via SQS)
    if "Records" in event:
        for record in event.get("Records", []):
            try:
                message = json.loads(record.get("body", "{}"))
                result['assetId'] = message.get("resourceId")
                result['libraryId'] = message.get("libraryId")
                result['connectorId'] = message.get("connectorId")
                result['eventType'] = message.get("event")
                result['source'] = 'sdma_sqs'

                log_event('sdma_sqs_event_parsed',
                          resourceType=message.get("resourceType"),
                          assetId=result['assetId'],
                          libraryId=result['libraryId'],
                          connectorId=result['connectorId'],
                          eventType=result['eventType'])
                break
            except json.JSONDecodeError as e:
                log_event('sqs_parse_error', error=str(e))
                continue

    # Check for direct SDMA connector invocation (LambdaInvokeRunner)
    elif event.get('assetId') or event.get('asset'):
        # Handle both direct assetId and nested asset.assetId formats
        asset_data = event.get('asset', {})
        result['assetId'] = event.get('assetId') or asset_data.get('assetId')
        result['projectId'] = event.get('projectId')
        result['libraryId'] = event.get('libraryId')
        result['eventType'] = event.get('event')  # 'create', 'update', 'delete', etc.
        result['source'] = 'sdma_direct'

        log_event('sdma_direct_event_parsed',
                  assetId=result['assetId'],
                  projectId=result['projectId'],
                  libraryId=result['libraryId'],
                  eventType=result['eventType'])

    return result



def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for AI tag generation

    Triggered by:
    - SDMA Connector Invocation (direct or via SQS)
    - Direct invocation with asset information

    Event structure (SDMA Connector - direct):
    {
        "assetId": "asset-123",
        "projectId": "project-xxx",
        "libraryId": "library-xxx"
    }

    Event structure (SDMA Connector - SQS):
    {
        "Records": [{
            "body": "{\"resourceType\": 1, \"resourceId\": \"asset-xxx\", ...}"
        }]
    }

    Event structure (Step Functions, which is how the pipeline invokes this):
    {
        "assetId": "asset-xxx",
        "projectId": "project-xxx",
        "file_hashes": {"front": "abc123...", "back": "def456..."},
        "s3_prefix": "assets/asset-xxx/renders/"
    }

    Returns:
        Dictionary with processing results including tags, metadata, and embedding
    """
    log_event('lambda_invoked', eventPayload=event)

    asset_id = None
    render_job_id = None
    library_id = None
    project_id = None
    event_type = None
    file_id = None

    # === Source 1: SDMA Connector (SQS Records or direct invocation) ===
    if "Records" in event or (event.get('assetId') and not event.get('source')) or event.get('asset'):
        sdma_data = parse_sdma_connector_event(event)
        asset_id = sdma_data.get('assetId')
        library_id = sdma_data.get('libraryId')
        project_id = sdma_data.get('projectId')
        event_type = sdma_data.get('eventType')
        # Also extract outputPath for direct invocation with SDMA context
    
        if sdma_data.get('source') in ['sdma_sqs', 'sdma_direct']:
            log_event('sdma_connector_event',
                      assetId=asset_id,
                      libraryId=library_id,
                      eventType=event_type,
                      source=sdma_data.get('source'))

            # === Handle DELETE event: cleanup and return ===
            if event_type == 'delete':
                if not EXTENSION_BUCKET:
                    return {
                        'statusCode': 500,
                        'body': json.dumps({'error': 'EXTENSION_BUCKET not configured'})
                    }

                cleanup_result = cleanup_asset_data(asset_id, EXTENSION_BUCKET)
                return {
                    'statusCode': 200 if cleanup_result['success'] else 500,
                    'body': json.dumps({
                        'action': 'cleanup',
                        'assetId': asset_id,
                        **cleanup_result
                    })
                }

    # === Source 2: Step Functions, or a direct invocation ===
    else:
        asset_id = event.get('assetId')
        render_job_id = event.get('renderJobId')
        file_id = event.get('fileId')
        project_id = project_id or event.get('projectId')  # Allow direct invocation with projectId

    bucket_name = event.get('bucketName', os.environ.get('S3_BUCKET_NAME'))

    # Validate required parameters
    if not asset_id:
        error_msg = "Missing required parameter: assetId"
        log_event('validation_error', error=error_msg)
        return {
            'statusCode': 400,
            'body': json.dumps({'error': error_msg})
        }

    if not bucket_name:
        error_msg = "S3 bucket not configured"
        log_event('configuration_error', error=error_msg)
        return {
            'statusCode': 500,
            'body': json.dumps({'error': error_msg})
        }

    # Update job status to PROCESSING
    set_ai_tag_status(asset_id, JOB_STATUS['processing'])

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Step 1: Download rendered images from SDMA CAS
            file_hashes = event.get('fileHashes')
            s3_prefix = event.get('s3Prefix')
            log_event('downloading_rendered_images', assetId=asset_id, bucketName=bucket_name,
                      casMode=bool(file_hashes))

            rendered_images = download_rendered_images(
                bucket_name, asset_id, temp_dir,
                file_hashes=file_hashes, s3_prefix=s3_prefix,
                project_id=project_id
            )

            if len(rendered_images) < 4:
                raise ValueError(f"Insufficient rendered images: {len(rendered_images)} found, minimum 4 required")

            # Step 2: Load model metadata (bounding box info)

            # Step 3: Load category configuration and generate structured metadata using AI
            # Category config can be customized per project/library
            cat_config = None
            try:
                cat_config = category_config.load_category_config(
                    bucket=EXTENSION_BUCKET,
                    project_id=project_id,
                    library_id=library_id
                )
                log_event('category_config_loaded',
                          assetId=asset_id,
                          projectId=project_id,
                          libraryId=library_id,
                          categoriesCount=len(cat_config.get('categories', {})))
            except Exception as e:
                log_event('category_config_load_failed', assetId=asset_id, error=str(e), usingDefault=True)

            # Get asset name for classification hint
            asset_info = get_sdma_asset_info(asset_id, project_id)
            asset_name = asset_info.get('assetName', '')

            log_event('generating_structured_metadata',
                      assetId=asset_id,
                      assetName=asset_name,
                      imageCount=len(rendered_images),
                      hasCustomCategories=cat_config is not None)

            ai_metadata = generate_structured_metadata(
                rendered_images,
                category_config=cat_config,
                asset_name=asset_name if asset_name else None
            )

            # Validate AI output against category config
            if cat_config:
                ai_metadata = category_config.validate_metadata_against_config(
                    ai_metadata, cat_config
                )

            # A manual category override used to be read here from a `tags`
            # attribute on SDMA's asset record. That attribute does not exist --
            # SDMA never sets it and its API exposes no equivalent -- so the
            # override never applied. Removed rather than ported; reinstate it
            # only against a field SDMA actually returns.

            # Step 4: Calculate size category from bounding box
            model_metadata = {}  # Model metadata no longer stored separately
            if model_metadata.get('size'):
                size = model_metadata['size']
                dimensions = {
                    'width': size[0] if len(size) > 0 else 0,
                    'height': size[2] if len(size) > 2 else 0,
                    'depth': size[1] if len(size) > 1 else 0
                }
                size_category = classify_size_category(dimensions)
                ai_metadata['structuredMetadata']['sizeCategory'] = size_category
                ai_metadata['structuredMetadata']['dimensions'] = dimensions

                log_event('size_category_classified', assetId=asset_id, dimensions=dimensions, sizeCategory=size_category)

            # Step 5: Prepare complete AI metadata
            # Embeddings are generated inline by Titan Embed v2 and stored in DynamoDB
            file_format = model_metadata.get('modelFormat', 'unknown')
            complete_metadata = {
                'assetId': asset_id,
                'tags': ai_metadata['tags'],
                'structuredMetadata': ai_metadata['structuredMetadata'],
                'description': ai_metadata['description'],
                'renderJobId': render_job_id,
                'libraryId': library_id,
                'generatedAt': datetime.now().isoformat()
            }

            # Step 6: Save AI metadata to S3 (legacy format)
            metadata_s3_key = save_ai_metadata_to_s3(EXTENSION_BUCKET, asset_id, complete_metadata)

            # Step 7: Index for vector search (DynamoDB native vector)
            from vector_indexer import index_asset_vector
            vector_index_result = index_asset_vector(
                asset_id=asset_id,
                ai_result=complete_metadata,
                project_id=project_id,
            )
            vector_index_result = {'backend': 'dynamodb-vector', **vector_index_result}

            # Step 8: Files were already registered to SDMA via CAS in
            # finalize-render, which also updated the manifest.
            log_event('sdma_files_registered_via_cas',
                      assetId=asset_id,
                      manifestUpdated=event.get('manifestUpdated', False))

            # Update job status to COMPLETED
            set_ai_tag_status(asset_id, JOB_STATUS['completed'])

            log_event('ai_tag_generation_completed',
                      assetId=asset_id,
                      tagCount=len(ai_metadata['tags']),
                      category=ai_metadata['structuredMetadata'].get('category'),
                      sizeCategory=ai_metadata['structuredMetadata'].get('sizeCategory'),
                      metadataS3Key=metadata_s3_key,
                      vectorDimensions=vector_index_result.get('vectorDimensions'))

            response_body = {
                'assetId': asset_id,
                'success': True,
                'tags': ai_metadata['tags'],
                'category': ai_metadata['structuredMetadata'].get('category'),
                'sizeCategory': ai_metadata['structuredMetadata'].get('sizeCategory'),
                'metadataS3Key': metadata_s3_key,
                'vectorIndexData': vector_index_result
            }

            return {
                'statusCode': 200,
                'body': json.dumps(response_body)
            }

    except Exception as e:
        error_msg = f"AI tag generation failed: {str(e)}"
        log_event('ai_tag_generation_failed', assetId=asset_id, error=error_msg, errorType=type(e).__name__)

        set_ai_tag_status(asset_id, JOB_STATUS['failed'], error_msg)

        return {
            'statusCode': 500,
            'body': json.dumps({
                'assetId': asset_id,
                'success': False,
                'error': error_msg
            })
        }
