# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Shared rendering constants for Lambda functions

This module contains constants shared between prepare-render,
blender-render, and finalize-render Lambda functions.

These parameters are NOT user-configurable. They are fixed for system
consistency and should only be modified by developers who understand
the pipeline architecture.

For user-configurable parameters, see: config/rendering/default.yaml
"""

# View definitions in Blender coordinate system (Z-up, Y-forward)
VIEW_DIRECTIONS = {
    'front': {'direction': [0, -1, 0], 'is_perspective': False},
    'back': {'direction': [0, 1, 0], 'is_perspective': False},
    'left': {'direction': [1, 0, 0], 'is_perspective': False},
    'right': {'direction': [-1, 0, 0], 'is_perspective': False},
    'top': {'direction': [0, 0, 1], 'is_perspective': False},
    'bottom': {'direction': [0, 0, -1], 'is_perspective': False},
    'perspective_front': {'direction': [1, -1, 0.5], 'is_perspective': True},
    'perspective_back': {'direction': [-1, 1, 0.5], 'is_perspective': True},
}

# Default view list (derived from VIEW_DIRECTIONS)
DEFAULT_VIEWS = list(VIEW_DIRECTIONS.keys())

# Image format (fixed for AI tagging pipeline compatibility)
IMAGE_FORMAT = {
    'format': 'PNG',
    'color_mode': 'RGBA',
}

# Render engine configuration
RENDER_ENGINE = {
    'engine': 'CYCLES',
    'device': 'CPU',
    'preview_samples': 16,
    'use_adaptive_sampling': True,
    'adaptive_threshold': 0.1,
}

# Advanced camera parameters
CAMERA = {
    'perspective_distance_multiplier': 1.8,
    'perspective_lens': 50,  # mm
    'perspective_sensor_width': 36,  # mm (full-frame equivalent)
}

# Advanced lighting parameters
LIGHTING = {
    'light_type': 'SUN',
    'key_light_angle': 0.15,  # radians
    'fill_light_angle': 0.3,
    'rim_light_angle': 0.2,
}

# Supported 3D file formats
SUPPORTED_EXTENSIONS = {'.glb', '.gltf', '.fbx', '.obj', '.blend'}

# Config file paths (relative to S3 bucket root)
CONFIG_PATHS = {
    'rendering': 'config/rendering/default.yaml',
    'rendering_json_fallback': 'config/rendering/default.json',  # Legacy fallback
}

# S3 path templates
S3_PATHS = {
    'metadata_file': 'metadata.json',
}

# Timeouts
TIMEOUTS = {
    'lambda': 120,  # seconds
    'render': 90,   # seconds per view
}
