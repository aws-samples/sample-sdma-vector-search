# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Processing state for each asset, in a table this stack owns.

These fields used to be written onto SDMA's own asset record, which put
Extension-specific attributes into a table this solution does not own. They
live here instead so SDMA's schema stays SDMA's.

Status is advisory: it records how far a pipeline got, and nothing gates on it.
A write failure is therefore logged and swallowed -- losing a status row must
not fail an asset that rendered and indexed correctly.
"""
import os
import time
from datetime import datetime, timezone
from typing import Optional

from botocore.exceptions import ClientError

from log_utils import log_event

# Long enough to diagnose a pipeline run well after it finished, short enough
# that the table does not grow one row per asset forever.
_RETENTION_SECONDS = 30 * 24 * 60 * 60


def _table():
    """Return the jobs table resource, or None if it is not configured."""
    name = os.environ.get('ASSET_JOBS_TABLE')
    if not name:
        log_event('asset_jobs_table_not_configured')
        return None
    import boto3
    return boto3.resource('dynamodb').Table(name)


def _put(asset_id: str, attributes: dict) -> None:
    table = _table()
    if not table or not asset_id:
        return

    names = {}
    values = {}
    assignments = []
    for index, (key, value) in enumerate(attributes.items()):
        # Alias every name: 'style' and others are DynamoDB reserved words, and
        # aliasing unconditionally means a new field cannot reintroduce that bug.
        placeholder = f'#n{index}'
        names[placeholder] = key
        values[f':v{index}'] = value
        assignments.append(f'{placeholder} = :v{index}')

    names['#ttl'] = 'expiresAt'
    values[':ttl'] = int(time.time()) + _RETENTION_SECONDS
    assignments.append('#ttl = :ttl')

    try:
        table.update_item(
            Key={'assetId': asset_id},
            UpdateExpression='SET ' + ', '.join(assignments),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as e:
        log_event('asset_jobs_write_error', assetId=asset_id, error=str(e))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_render_status(asset_id: str, status: str,
                      render_id: Optional[str] = None,
                      current: bool = False) -> None:
    """Record how far the render pipeline got for this asset.

    ``current`` distinguishes the render being started from the one that
    finished, matching the previous currentRenderId / lastRenderId split.
    """
    attributes = {'renderJobStatus': status, 'renderJobUpdatedAt': _now()}
    if render_id:
        attributes['currentRenderId' if current else 'lastRenderId'] = render_id
    _put(asset_id, attributes)


def set_ai_tag_status(asset_id: str, status: str,
                      error_message: Optional[str] = None) -> None:
    """Record how far AI tagging got for this asset."""
    attributes = {'aiTagJobStatus': status, 'aiTagJobUpdatedAt': _now()}
    if error_message:
        attributes['aiTagJobError'] = error_message
    _put(asset_id, attributes)


def get_status(asset_id: str) -> dict:
    """Return the recorded state, or an empty dict when there is none."""
    table = _table()
    if not table or not asset_id:
        return {}
    try:
        return table.get_item(Key={'assetId': asset_id}).get('Item', {}) or {}
    except ClientError as e:
        log_event('asset_jobs_read_error', assetId=asset_id, error=str(e))
        return {}
