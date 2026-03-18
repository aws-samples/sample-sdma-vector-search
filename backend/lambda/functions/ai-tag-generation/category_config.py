# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Category configuration management for AI metadata generation

Loads user-defined category lists from S3 and provides them to the AI
for constrained classification. Categories can be customized per project.
"""
import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

import yaml
from aws_clients import s3_client
from log_utils import log_event

# Import system defaults
from system_defaults import (
    DEFAULT_CATEGORIES, DEFAULT_STYLES, DEFAULT_MATERIALS, DEFAULT_COLORS,
    DEFAULT_FALLBACKS, CONFIG_PATHS, CACHE_TTL_MINUTES
)


# Cache for category config to avoid repeated S3 reads
_config_cache: Dict[str, Any] = {}
_cache_expiry: Optional[datetime] = None


def normalize_config(raw_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize config to flat structure for internal use.

    Supports multiple config formats:
    1. New format: filter_attributes + embedding_hints (current)
    2. Legacy: user_configurable + system
    3. Flat format (oldest)

    Args:
        raw_config: Raw config from S3

    Returns:
        Flattened config dictionary
    """
    # New format: filter_attributes + embedding_hints
    if 'filter_attributes' in raw_config:
        filter_cfg = raw_config.get('filter_attributes', {})
        embed_cfg = raw_config.get('embedding_hints', {})

        return {
            'version': raw_config.get('version', '1.0'),
            # Filter attributes (A)
            'categories': filter_cfg.get('categories', {}),
            'styles': filter_cfg.get('styles', []),
            'materials': filter_cfg.get('materials', []),
            'colors': filter_cfg.get('colors', []),
            'size_thresholds': filter_cfg.get('size_thresholds', {}),
            'allow_unlisted': False,
            'fallback_category': filter_cfg.get('fallbacks', {}).get('category', 'Furniture'),
            'fallback_subcategory': filter_cfg.get('fallbacks', {}).get('subcategory', 'Cabinet'),
            'fallback_style': filter_cfg.get('fallbacks', {}).get('style', 'Realistic'),
            # Embedding hints (B)
            'max_tags': embed_cfg.get('tag_generation', {}).get('max_tags', 12),
            'include_synonyms': embed_cfg.get('tag_generation', {}).get('include_synonyms', True),
            'description_max_length': embed_cfg.get('description_generation', {}).get('max_length', 400),
        }

    # Legacy format: user_configurable + system
    if 'user_configurable' in raw_config:
        user_cfg = raw_config.get('user_configurable', {})
        return {
            'version': raw_config.get('version', '1.0'),
            'categories': user_cfg.get('categories', {}),
            'styles': user_cfg.get('styles', []),
            'materials': user_cfg.get('materials', []),
            'colors': user_cfg.get('colors', []),
            'allow_unlisted': user_cfg.get('validation', {}).get('allow_unlisted', False),
            'fallback_category': user_cfg.get('validation', {}).get('fallback_category', 'Furniture'),
            'fallback_subcategory': user_cfg.get('validation', {}).get('fallback_subcategory', 'Cabinet'),
            'fallback_style': user_cfg.get('validation', {}).get('fallback_style', 'Realistic'),
            'max_tags': user_cfg.get('output', {}).get('max_tags', 12),
            'description_max_length': user_cfg.get('output', {}).get('description_max_length', 350),
        }

    # Already flat format
    return raw_config


def get_default_config() -> Dict[str, Any]:
    """
    Return default category configuration

    Used when no custom config is found in S3.
    """
    return {
        "version": "1.0",
        "categories": DEFAULT_CATEGORIES,
        "styles": DEFAULT_STYLES,
        "materials": DEFAULT_MATERIALS,
        "colors": DEFAULT_COLORS,
        "allow_unlisted": False,
        "fallback_category": DEFAULT_FALLBACKS['category'],
        "fallback_subcategory": DEFAULT_FALLBACKS['subcategory'],
        "fallback_style": DEFAULT_FALLBACKS['style']
    }


def load_category_config(
    bucket: str,
    project_id: Optional[str] = None,
    library_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Load category configuration from S3

    Looks for config in the following order (first found wins):
    1. Project-specific: config/tagging/{project_id}.yaml (then .json)
    2. Library-specific: config/tagging/{library_id}.yaml (then .json)
    3. Global default: config/tagging/default.yaml (then .json)
    4. Built-in default (if no S3 config exists)

    Args:
        bucket: S3 bucket name
        project_id: Optional project ID for project-specific config
        library_id: Optional library ID for library-specific config

    Returns:
        Category configuration dictionary
    """
    global _config_cache, _cache_expiry

    # Check cache
    cache_key = f"{bucket}:{project_id or ''}:{library_id or ''}"
    if _cache_expiry and datetime.now() < _cache_expiry:
        if cache_key in _config_cache:
            log_event('category_config_cache_hit', cacheKey=cache_key)
            return _config_cache[cache_key]

    # Config paths to try (in priority order)
    # Try YAML first, then JSON for backward compatibility
    config_paths = []
    if project_id:
        config_paths.append(f"config/tagging/{project_id}.yaml")
        config_paths.append(f"config/tagging/{project_id}.json")
    if library_id:
        config_paths.append(f"config/tagging/{library_id}.yaml")
        config_paths.append(f"config/tagging/{library_id}.json")
    config_paths.append(CONFIG_PATHS['tagging'])
    config_paths.append(CONFIG_PATHS['tagging_json_fallback'])

    # Try each path
    for config_path in config_paths:
        try:
            response = s3_client.get_object(Bucket=bucket, Key=config_path)
            content = response['Body'].read().decode('utf-8')

            # Parse based on file extension
            if config_path.endswith('.yaml') or config_path.endswith('.yml'):
                config_data = yaml.safe_load(content)
            else:
                config_data = json.loads(content)

            log_event('category_config_loaded',
                      bucket=bucket,
                      path=config_path,
                      projectId=project_id,
                      libraryId=library_id)

            # Normalize config structure (handles both old and new formats)
            normalized_config = normalize_config(config_data)

            # Merge with defaults to ensure all fields exist
            merged_config = get_default_config()
            merged_config.update(normalized_config)

            # Update cache
            _config_cache[cache_key] = merged_config
            _cache_expiry = datetime.now() + timedelta(minutes=CACHE_TTL_MINUTES)

            return merged_config

        except s3_client.exceptions.NoSuchKey:
            continue
        except Exception as e:
            log_event('category_config_load_error',
                      bucket=bucket,
                      path=config_path,
                      error=str(e))
            continue

    # No custom config found, use default
    log_event('category_config_using_default',
              bucket=bucket,
              projectId=project_id,
              libraryId=library_id)

    default_config = get_default_config()
    _config_cache[cache_key] = default_config
    _cache_expiry = datetime.now() + timedelta(minutes=CACHE_TTL_MINUTES)

    return default_config


def build_category_prompt_section(config: Dict[str, Any]) -> str:
    """
    Build the category selection section of the AI prompt

    Args:
        config: Category configuration dictionary

    Returns:
        Formatted string for inclusion in AI prompt
    """
    lines = []

    # Categories with subcategories
    lines.append("CATEGORY OPTIONS (select one):")
    for category, details in config.get('categories', {}).items():
        if isinstance(details, dict):
            desc = details.get('description', '')
            subcats = details.get('subcategories', [])
            lines.append(f"- {category}: {desc}")
            if subcats:
                lines.append(f"  Subcategories: {', '.join(subcats)}")
        else:
            # Simple list format (backwards compatibility)
            lines.append(f"- {category}")

    # Style options
    styles = config.get('styles', [])
    if styles:
        lines.append("")
        lines.append(f"STYLE OPTIONS (select one): {', '.join(styles)}")

    # Material options
    materials = config.get('materials', [])
    if materials:
        lines.append("")
        lines.append(f"MATERIAL OPTIONS (select multiple): {', '.join(materials)}")

    # Color options
    colors = config.get('colors', [])
    if colors:
        lines.append("")
        lines.append(f"COLOR OPTIONS (select multiple): {', '.join(colors)}")

    # Allow unlisted note
    if config.get('allow_unlisted', False):
        lines.append("")
        lines.append("Note: You may use values not in the lists above if none fit well.")
    else:
        lines.append("")
        lines.append("IMPORTANT: You MUST select from the options above. Do not create new categories.")

    return "\n".join(lines)


def validate_metadata_against_config(
    metadata: Dict[str, Any],
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Validate and normalize AI-generated metadata against config

    If AI returns values not in the config, replace with fallbacks.

    Args:
        metadata: AI-generated metadata
        config: Category configuration

    Returns:
        Validated and normalized metadata
    """
    structured = metadata.get('structuredMetadata', {})
    validated = structured.copy()
    corrections = []

    # Validate category
    valid_categories = list(config.get('categories', {}).keys())
    if structured.get('category') not in valid_categories:
        if not config.get('allow_unlisted', False):
            old_val = structured.get('category')
            validated['category'] = config.get('fallback_category', 'Prop')
            corrections.append(f"category: {old_val} -> {validated['category']}")

    # Validate subcategory
    category = validated.get('category', '')
    category_config = config.get('categories', {}).get(category, {})
    valid_subcats = category_config.get('subcategories', []) if isinstance(category_config, dict) else []

    if valid_subcats and structured.get('subcategory') not in valid_subcats:
        if not config.get('allow_unlisted', False):
            old_val = structured.get('subcategory')
            validated['subcategory'] = config.get('fallback_subcategory', 'Other')
            corrections.append(f"subcategory: {old_val} -> {validated['subcategory']}")

    # Validate style
    valid_styles = config.get('styles', [])
    if valid_styles and structured.get('style') not in valid_styles:
        if not config.get('allow_unlisted', False):
            old_val = structured.get('style')
            validated['style'] = valid_styles[0] if valid_styles else 'Other'
            corrections.append(f"style: {old_val} -> {validated['style']}")

    # Validate the remaining inline filter attributes. These are the vector
    # index's INLINE_FILTER columns and the UI's dropdowns are built from the
    # same config, so a value outside it makes the asset unreachable by that
    # filter -- silently, because the write succeeds and the asset still appears
    # in unfiltered search. Observed in practice: the model returned Cardboard,
    # Paper, Drywall and Teal, none of which the vocabulary offers.
    #
    # The model returns these as *lists* (`materials`, `primaryColors`), and
    # vector_indexer.py indexes the first element of each. Correct the lists, not
    # a scalar: writing a `primaryMaterial` string here would be ignored by the
    # indexer and the uncorrected list would be indexed instead.
    for field, config_key in (('materials', 'materials'),
                              ('primaryColors', 'colors')):
        valid = config.get(config_key, [])
        values = structured.get(field)
        if not valid or not isinstance(values, list):
            continue
        if config.get('allow_unlisted', False):
            continue
        corrected = [v for v in values if v in valid]
        if corrected != values:
            # Keep the order the model chose for the values it got right; fall
            # back only when nothing it offered is in the vocabulary, because an
            # empty list would leave the inline filter attribute blank and the
            # asset unfilterable.
            validated[field] = corrected or [valid[0]]
            corrections.append(f"{field}: {values} -> {validated[field]}")

    # Log corrections
    if corrections:
        log_event('metadata_validation_corrections',
                  corrections=corrections,
                  allowUnlisted=config.get('allow_unlisted', False))

    # Update metadata
    result = metadata.copy()
    result['structuredMetadata'] = validated
    return result
