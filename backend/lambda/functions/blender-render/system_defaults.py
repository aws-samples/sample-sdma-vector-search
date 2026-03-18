# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""System defaults for render worker

Imports shared rendering constants and adds worker-specific settings.

For user-configurable parameters, see: config/rendering/default.yaml
"""

# Import all shared rendering constants
from rendering_constants import (
    VIEW_DIRECTIONS,
    DEFAULT_VIEWS,
    IMAGE_FORMAT,
    RENDER_ENGINE,
    CAMERA,
    LIGHTING,
    TIMEOUTS,
)

# Re-export for backward compatibility
__all__ = [
    'VIEW_DIRECTIONS',
    'DEFAULT_VIEWS',
    'IMAGE_FORMAT',
    'RENDER_ENGINE',
    'CAMERA',
    'LIGHTING',
    'DEFAULT_RENDER_TIMEOUT',
]

# Worker-specific settings
DEFAULT_RENDER_TIMEOUT = TIMEOUTS['render']
