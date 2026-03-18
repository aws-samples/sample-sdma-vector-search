# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Common constants shared across all Lambda functions

This module contains constants for DynamoDB schema, job statuses,
and HTTP-related values that are used consistently across the system.

These parameters are NOT user-configurable. They define the data model
and should only be modified with careful consideration of backward
compatibility.
"""

# SDMA's own DynamoDB attribute and index names used to live here, because the
# Extension read and wrote SDMA's tables directly. It now goes through the SDMA
# REST API instead, so nothing depends on SDMA's internal schema. Job status
# field names live with the code that owns them, in shared/asset_jobs.py.

# Job status values
JOB_STATUS = {
    'pending': 'PENDING',
    'processing': 'PROCESSING',
    'rendering': 'RENDERING',
    'started': 'STARTED',
    'in_progress': 'IN_PROGRESS',
    'completed': 'COMPLETED',
    'success': 'SUCCESS',
    'failed': 'FAILED',
    'unknown': 'UNKNOWN',
    'skipped': 'SKIPPED',
    'invoked': 'INVOKED',
}

# HTTP constants
HTTP = {
    'content_type_json': 'application/json',
}

# CORS headers
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
}
