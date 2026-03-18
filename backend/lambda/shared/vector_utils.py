# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Shared utility for generating text embeddings with Amazon Bedrock.

Used by both ai-tag-generation (indexing) and vector-search-api (query).

The model and dimensions come from the CloudFormation template so that the
vector index definition and the code that writes to it cannot disagree. The
defaults below describe Titan Embed v2 and are only used for local runs where
the environment is not populated.
"""
import os
import json
import boto3
from typing import List

EMBEDDING_MODEL_ID = os.environ.get("EMBEDDING_MODEL_ID") or "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS") or 1024)

_bedrock_runtime = None


def get_bedrock_runtime():
    global _bedrock_runtime
    if _bedrock_runtime is None:
        _bedrock_runtime = boto3.client("bedrock-runtime")
    return _bedrock_runtime


def generate_embedding(text: str, dimensions: int = None) -> List[float]:
    """
    Generate a text embedding using the configured Bedrock embedding model.

    Args:
        text: Input text to embed (max ~8000 tokens)
        dimensions: Output dimensions. Defaults to EMBEDDING_DIMENSIONS, which
            must match the Dimensions of the DynamoDB vector index.

    Returns:
        List of floats representing the embedding vector
    """
    if dimensions is None:
        dimensions = EMBEDDING_DIMENSIONS

    client = get_bedrock_runtime()

    # Truncate very long texts (Titan v2 supports ~8000 tokens)
    if len(text) > 30000:
        text = text[:30000]

    response = client.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "inputText": text,
            "dimensions": dimensions,
            "normalize": True,
        }),
    )

    result = json.loads(response["body"].read())
    return result["embedding"]


def build_search_text(title: str, description: str, tags: List[str] = None) -> str:
    """
    Build a composite text for embedding from asset metadata.

    Combines title, description, and tags into a single string optimized
    for semantic similarity with user queries.

    Args:
        title: Asset title/name
        description: AI-generated description
        tags: List of tags

    Returns:
        Combined text for embedding
    """
    parts = []
    if title:
        parts.append(title)
    if description:
        parts.append(description)
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    return ". ".join(parts)
