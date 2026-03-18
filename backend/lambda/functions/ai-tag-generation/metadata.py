# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""AI-powered tag and structured metadata generation using Bedrock Vision API"""
import os
import json
import base64
import time
from typing import Dict, List, Any, Optional
from botocore.exceptions import ClientError
from aws_clients import bedrock_client
from log_utils import log_event
from system_defaults import AI_MODEL_DEFAULTS


# Base prompt template - category section is injected dynamically
STRUCTURED_METADATA_PROMPT_TEMPLATE = """Analyze the 3D model renderings from multiple viewpoints and extract structured metadata for search indexing.

{asset_info_section}
VIEWPOINT INFORMATION:
- front: Front view (looking from -Y towards +Y)
- back: Back view (looking from +Y towards -Y)
- left: Left side view (looking from +X towards -X)
- right: Right side view (looking from -X towards +X)
- top: Top view (looking down from +Z)
- bottom: Bottom view (looking up from -Z)
- perspective_front: Diagonal view from front-right-top
- perspective_back: Diagonal view from back-left-top

OUTPUT FORMAT (JSON only):
{{
  "tags": ["tag1", "tag2", ...],
  "structuredMetadata": {{
    "primaryColors": ["color1", "color2"],
    "secondaryColors": ["color3"],
    "materials": ["material1", "material2"],
    "category": "category_name",
    "subcategory": "subcategory_name",
    "style": "style_name",
    "complexity": "low|medium|high"
  }},
  "description": "Detailed description of the 3D model including orientation, visual characteristics, structural details, and functional context."
}}

TAG REQUIREMENTS:
- Generate 8-12 descriptive tags
- Each tag: 1-3 words, lowercase, hyphenated if multi-word
- Include both broad categories and specific details
- Avoid redundant or overlapping tags

{category_section}

DESCRIPTION REQUIREMENTS:
- Include orientation information with specific axis details
- Describe visual characteristics (colors, materials, surface properties)
- Explain structural details (components, symmetry, complexity)
- Provide functional context (purpose, use case)

IMPORTANT: Return ONLY valid JSON, no markdown formatting or additional text."""


# Default category section (used when no custom config is provided)
DEFAULT_CATEGORY_SECTION = """CATEGORY OPTIONS (select one):
- Character: Living beings and characters
  Subcategories: Human, Animal, Monster, Robot, Fantasy Creature
- Environment: Large-scale environmental elements
  Subcategories: Building, Terrain, Vegetation, Sky, Water
- Prop: Interactive objects and items
  Subcategories: Furniture, Weapon, Tool, Container, Food, Electronics, Vehicle, Decoration
- Effect: Visual effects and particles
  Subcategories: Particle, Light, Smoke, Fire, Magic
- UI: User interface elements
  Subcategories: Button, Icon, Panel, HUD
- Prototype: Test objects and primitives
  Subcategories: Primitive, Placeholder, Debug, Test

STYLE OPTIONS (select one): Realistic, Stylized, LowPoly, Cartoon, Anime, Pixel, Abstract, Minimalist

MATERIAL OPTIONS (select multiple): Wood, Metal, Plastic, Fabric, Leather, Glass, Stone, Ceramic, Concrete, Rubber, Paper, Foam, Composite

COLOR OPTIONS (select multiple): Red, Orange, Yellow, Green, Blue, Purple, Pink, Brown, Black, White, Gray, Gold, Silver, Beige, Cyan, Magenta

IMPORTANT: You MUST select from the options above. Do not create new categories."""


def build_prompt(
    category_config: Optional[Dict[str, Any]] = None,
    asset_name: Optional[str] = None
) -> str:
    """
    Build the full prompt with category configuration and asset info

    Args:
        category_config: Optional category configuration dictionary.
                        If None, uses default categories.
        asset_name: Optional asset name for context hint.

    Returns:
        Complete prompt string for AI model
    """
    if category_config:
        # Import here to avoid circular dependency
        from category_config import build_category_prompt_section
        category_section = build_category_prompt_section(category_config)
    else:
        category_section = DEFAULT_CATEGORY_SECTION

    # Build asset info section
    if asset_name:
        asset_info_section = f"""ASSET INFORMATION (use as hint for classification):
- Asset Name: {asset_name}

"""
    else:
        asset_info_section = ""

    return STRUCTURED_METADATA_PROMPT_TEMPLATE.format(
        asset_info_section=asset_info_section,
        category_section=category_section
    )


def generate_structured_metadata(
    rendered_images: Dict[str, str],
    max_retries: int = AI_MODEL_DEFAULTS['max_retries'],
    category_config: Optional[Dict[str, Any]] = None,
    asset_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate structured metadata from rendered images using Bedrock Vision API

    This function analyzes 3D model renderings from multiple viewpoints to extract:
    - Descriptive tags for search
    - Structured metadata (colors, materials, category, style)
    - Detailed description including orientation information

    Args:
        rendered_images: Dictionary mapping view names to image file paths
                        Expected keys: 'front', 'back', 'left', 'right', 'top', 'bottom',
                                      'perspective_front', 'perspective_back'
        max_retries: Maximum number of retry attempts for API calls
        category_config: Optional category configuration for constrained classification.
                        If provided, AI will select from user-defined categories.
        asset_name: Optional asset name for classification hint.

    Returns:
        Dictionary containing:
            - 'tags': List of descriptive tag strings
            - 'structuredMetadata': Dictionary with colors, materials, category, style
            - 'description': Comprehensive model description

    Raises:
        Exception: If metadata generation fails after all retries
    """
    retry_count = 0
    last_error = None

    # Build prompt with category configuration and asset name
    prompt = build_prompt(category_config, asset_name)

    while retry_count < max_retries:
        try:
            # Prepare images for AI analysis
            image_data: List[Dict[str, str]] = []
            for view_name, image_path in rendered_images.items():
                with open(image_path, 'rb') as img_file:
                    image_bytes = img_file.read()
                    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                    image_data.append({
                        'view': view_name,
                        'data': image_base64
                    })

            # Prepare request for Claude model
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt}
                    ]
                }
            ]

            # Add images to the message in preferred order
            view_order = [
                'front', 'back', 'left', 'right', 'top', 'bottom',
                'perspective_front', 'perspective_back'
            ]
            for view_name in view_order:
                for img_info in image_data:
                    if img_info['view'] == view_name:
                        messages[0]["content"].append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": img_info["data"]
                            }
                        })
                        break

            # Add any remaining images not in the preferred order
            for img_info in image_data:
                if img_info['view'] not in view_order:
                    messages[0]["content"].append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_info["data"]
                        }
                    })

            # Call Bedrock API
            # Falls back to the same literal system_defaults carries, so the two
            # cannot disagree: the template sets AI_MODEL, and AI_MODEL_DEFAULTS
            # is the single place the default lives.
            model_id = os.environ.get('AI_MODEL', AI_MODEL_DEFAULTS['model_id'])

            log_event('bedrock_metadata_generation_started',
                      modelId=model_id,
                      attempt=retry_count + 1,
                      maxRetries=max_retries,
                      viewsProvided=list(rendered_images.keys()))

            response = bedrock_client.invoke_model(
                modelId=model_id,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": AI_MODEL_DEFAULTS['max_tokens'],
                    "messages": messages
                })
            )

            # Parse response
            response_body = json.loads(response['body'].read())
            content_text = response_body['content'][0]['text']

            # Extract JSON from response
            content_text = content_text.strip()

            # Remove markdown code blocks if present
            if content_text.startswith('```json'):
                content_text = content_text[7:]
            if content_text.startswith('```'):
                content_text = content_text[3:]
            if content_text.endswith('```'):
                content_text = content_text[:-3]
            content_text = content_text.strip()

            # Extract JSON object from text
            json_start = content_text.find('{')
            json_end = content_text.rfind('}')

            if json_start == -1 or json_end == -1:
                raise ValueError(f"No JSON object found in response: {content_text[:200]}")

            json_text = content_text[json_start:json_end + 1]

            # Parse JSON
            metadata = json.loads(json_text)

            # Validate required fields
            if 'tags' not in metadata:
                raise ValueError("Missing required field: 'tags'")
            if 'structuredMetadata' not in metadata:
                raise ValueError("Missing required field: 'structuredMetadata'")
            if 'description' not in metadata:
                raise ValueError("Missing required field: 'description'")

            # Validate tags
            if not isinstance(metadata['tags'], list):
                raise ValueError("'tags' must be a list")
            if not all(isinstance(tag, str) for tag in metadata['tags']):
                raise ValueError("All tags must be strings")

            # Validate structured metadata
            structured = metadata['structuredMetadata']
            required_fields = ['primaryColors', 'materials', 'category', 'style']
            for field in required_fields:
                if field not in structured:
                    log_event('structured_metadata_field_missing', field=field)

            log_event('bedrock_metadata_generation_success',
                      tagCount=len(metadata['tags']),
                      descriptionLength=len(metadata['description']),
                      category=structured.get('category', 'unknown'),
                      attempt=retry_count + 1)

            return metadata

        except json.JSONDecodeError as e:
            last_error = e
            retry_count += 1
            log_event('bedrock_metadata_generation_json_error',
                      error=str(e),
                      attempt=retry_count,
                      maxRetries=max_retries,
                      responseText=content_text[:500] if 'content_text' in locals() else 'N/A')

            if retry_count < max_retries:
                wait_time = 2 ** retry_count
                log_event('retry_wait', waitSeconds=wait_time)
                time.sleep(wait_time)

        except ValueError as e:
            last_error = e
            retry_count += 1
            log_event('bedrock_metadata_generation_validation_error',
                      error=str(e),
                      attempt=retry_count,
                      maxRetries=max_retries)

            if retry_count < max_retries:
                wait_time = 2 ** retry_count
                log_event('retry_wait', waitSeconds=wait_time)
                time.sleep(wait_time)

        except ClientError as e:
            last_error = e
            error_code = e.response['Error']['Code'] if hasattr(e, 'response') else 'Unknown'
            retry_count += 1

            log_event('bedrock_metadata_generation_client_error',
                      error=str(e),
                      errorCode=error_code,
                      attempt=retry_count,
                      maxRetries=max_retries)

            # Check if error is retryable
            retryable_errors = ['ThrottlingException', 'ServiceUnavailableException', 'InternalServerException']
            if error_code not in retryable_errors:
                log_event('non_retryable_error', errorCode=error_code)
                raise e

            if retry_count < max_retries:
                wait_time = 2 ** retry_count
                log_event('retry_wait', waitSeconds=wait_time)
                time.sleep(wait_time)

        except Exception as e:
            last_error = e
            retry_count += 1
            log_event('bedrock_metadata_generation_unexpected_error',
                      error=str(e),
                      errorType=type(e).__name__,
                      attempt=retry_count,
                      maxRetries=max_retries)

            if retry_count < max_retries:
                wait_time = 2 ** retry_count
                log_event('retry_wait', waitSeconds=wait_time)
                time.sleep(wait_time)

    # All retries exhausted
    log_event('bedrock_metadata_generation_failed', error=str(last_error), totalAttempts=retry_count)

    raise Exception(f"Failed to generate structured metadata after {max_retries} attempts: {str(last_error)}")


def classify_size_category(dimensions: Dict[str, float]) -> str:
    """
    Classify asset size category based on bounding box dimensions

    Size categories:
    - tiny: < 0.3m (accessories, small items)
    - small: 0.3m - 1.0m (chairs, small tables)
    - medium: 1.0m - 2.0m (sofas, desks)
    - large: 2.0m - 5.0m (beds, large furniture)
    - extra_large: > 5.0m (architecture, vehicles)

    Args:
        dimensions: Dictionary with 'width', 'height', 'depth' in meters

    Returns:
        Size category string
    """
    max_dimension = max(
        dimensions.get('width', 0),
        dimensions.get('height', 0),
        dimensions.get('depth', 0)
    )

    if max_dimension < 0.3:
        return 'tiny'
    elif max_dimension < 1.0:
        return 'small'
    elif max_dimension < 2.0:
        return 'medium'
    elif max_dimension < 5.0:
        return 'large'
    else:
        return 'extra_large'
