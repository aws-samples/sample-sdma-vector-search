# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""DynamoDB vector indexing.

Called from ai-tag-generation handler after AI tagging completes.
Generates an embedding with Titan Embed v2 and writes the asset
record (metadata + vector) to the DynamoDB vector table.
"""
import os
import boto3
from typing import Dict, Any, List
from datetime import datetime, timezone

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
from vector_utils import generate_embedding, build_search_text, EMBEDDING_DIMENSIONS

dynamodb_client = boto3.client('dynamodb')

VECTOR_TABLE = os.environ.get('VECTOR_TABLE_NAME', '')


def index_asset_vector(
    asset_id: str,
    ai_result: Dict[str, Any],
    project_id: str = None,
) -> Dict[str, Any]:
    """
    Generate embedding and write to DynamoDB vector table.

    The record is immediately searchable via SearchVectors after write.

    Args:
        asset_id: Unique asset identifier
        ai_result: Complete AI-generated metadata from Claude
        project_id: SDMA project ID for filtering

    Returns:
        Dictionary with indexing result
    """
    if not VECTOR_TABLE:
        raise ValueError("VECTOR_TABLE_NAME not configured")

    structured = ai_result.get('structuredMetadata', {})
    tags = ai_result.get('tags', [])
    description = ai_result.get('description', '')
    title = tags[0] if tags else asset_id

    colors = structured.get('primaryColors', [])
    materials = structured.get('materials', [])
    category = structured.get('category', 'Other')
    subcategory = structured.get('subcategory', 'Miscellaneous')
    style = structured.get('style', 'Other')
    size_category = structured.get('sizeCategory', 'medium')

    # Build text for embedding
    search_text = build_search_text(title, description, tags)

    # Generate embedding
    embedding = generate_embedding(search_text)

    # Build DynamoDB item
    item = {
        'assetId': {'S': asset_id},
        'category': {'S': category},
        'title': {'S': title},
        'description': {'S': description[:2000]},  # Limit description length
        'tags': {'L': [{'S': t} for t in tags[:10]]},
        'subcategory': {'S': subcategory},
        'style': {'S': style},
        'primaryMaterial': {'S': materials[0] if materials else ''},
        'primaryColor': {'S': colors[0] if colors else ''},
        'secondaryColor': {'S': colors[1] if len(colors) > 1 else ''},
        'sizeCategory': {'S': size_category},
        'indexedAt': {'S': datetime.now(timezone.utc).isoformat()},
        'embedding': {'L': [{'N': str(v)} for v in embedding]},
    }

    # Optional attributes
    if project_id:
        item['projectId'] = {'S': project_id}

    # Write to DynamoDB (immediate — no sync delay)
    # Embedding stored as List of Numbers (L type) — DynamoDB auto-indexes it
    dynamodb_client.put_item(
        TableName=VECTOR_TABLE,
        Item=item,
    )

    return {
        'assetId': asset_id,
        'vectorDimensions': EMBEDDING_DIMENSIONS,
        'embeddingTextLength': len(search_text),
        'indexed': True,
    }
