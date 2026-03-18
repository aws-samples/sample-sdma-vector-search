# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""System defaults for vector-search-api Lambda

These parameters are NOT user-configurable. They are fixed for system
consistency and should only be modified by developers who understand
the pipeline architecture.

"""

# Config file paths (relative to S3 bucket root)
CONFIG_PATHS = {
    # The AI tagger's vocabulary doubles as the UI's filter options, so both
    # read this one file rather than keeping a second copy in step.
    'tagging': 'config/tagging/default.yaml',
}

# Presigned URL expiry time in seconds (1 hour)
PRESIGNED_URL_EXPIRY = 3600

# Thumbnail filename priority (first match wins)
THUMBNAIL_FILENAMES = [
    'perspective_front.png',
    'front.png',
]

# Default category config (fallback when S3 config not found)
DEFAULT_CATEGORY_CONFIG = {
    'categories': {},
    'styles': [],
    'materials': [],
    'colors': []
}
