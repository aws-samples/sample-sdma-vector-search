# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""System defaults for AI tagger

These parameters are NOT user-configurable. They are fixed for system
consistency and should only be modified by developers who understand
the pipeline architecture.

For user-configurable parameters, see: config/tagging/default.yaml
"""

# AI Model Configuration
# The single place these defaults live. metadata.py imports them rather than
# repeating the literals, which is how the model id came to be written twice.
AI_MODEL_DEFAULTS = {
    'model_id': 'global.anthropic.claude-haiku-4-5-20251001-v1:0',
    'max_tokens': 2048,
    'max_retries': 3,
    'retry_backoff_base': 2,
}

# Complexity Classification Rules
COMPLEXITY_RULES = {
    'low': 'Simple geometry, few materials, basic shapes',
    'medium': 'Moderate detail, multiple materials, some intricate parts',
    'high': 'Complex geometry, many materials, highly detailed',
}

# Default category configuration (used when no S3 config exists)
DEFAULT_CATEGORIES = {
    'Furniture': {
        'description': 'Seating, tables, storage, and other furniture',
        'subcategories': ['Chair', 'Stool', 'Bench', 'Sofa', 'Bed', 'Table', 'Desk', 'Bookcase', 'Cabinet']
    },
    'Prop': {
        'description': 'Interactive objects and items',
        'subcategories': ['Weapon', 'Tool', 'Container', 'Food', 'Electronics', 'Vehicle', 'Decoration']
    },
    'Decor': {
        'description': 'Decorative items and accessories',
        'subcategories': ['Plant', 'Pillow', 'Rug', 'Book', 'Toy']
    },
    'Lighting': {
        'description': 'Light fixtures',
        'subcategories': ['Floor Lamp', 'Table Lamp', 'Ceiling Lamp', 'Wall Lamp']
    },
    'Architecture': {
        'description': 'Structural elements',
        'subcategories': ['Wall', 'Door', 'Floor', 'Stairs']
    }
}

DEFAULT_STYLES = [
    'Realistic', 'Stylized', 'LowPoly', 'Modern',
    'Traditional', 'Minimalist', 'Industrial', 'Scandinavian'
]

DEFAULT_MATERIALS = [
    'Wood', 'Metal', 'Plastic', 'Fabric', 'Leather',
    'Glass', 'Stone', 'Ceramic', 'Porcelain'
]

DEFAULT_COLORS = [
    'Red', 'Orange', 'Yellow', 'Green', 'Blue', 'Purple',
    'Pink', 'Brown', 'Black', 'White', 'Gray', 'Beige', 'Cream'
]

DEFAULT_FALLBACKS = {
    'category': 'Furniture',
    'subcategory': 'Cabinet',
    'style': 'Realistic',
}

# Config file paths (relative to S3 bucket root)
CONFIG_PATHS = {
    'tagging': 'config/tagging/default.yaml',
    'tagging_json_fallback': 'config/tagging/default.json',  # Legacy fallback
}

# Cache settings
CACHE_TTL_MINUTES = 5

# SDMA Integration
# Note: All SDMA-related settings are provided via environment variables
SDMA = {
    'thumbnail_filename': '.spatial_data_mgmt_asset_thumbnail.jpg',
    'data_path_prefix': '/Data/',
    'hash_extension': '.xxh128',
}

# Required environment variables (no defaults - must be configured)
REQUIRED_ENV_VARS = [
    'SDMA_API_ENDPOINT',  # SDMA API Gateway URL
]
