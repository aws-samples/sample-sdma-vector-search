# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Single view rendering script for Lambda-based Blender rendering

This script renders a single view of a 3D model. It's designed to be
invoked in parallel (8 Lambda instances for 8 views).

Usage:
    blender --background --python render_single_view.py -- \
        --model_path /path/to/model.glb \
        --view_name front \
        --output_path /path/to/output.png
"""
import bpy
import sys
import os
import argparse
import json
from datetime import datetime
from mathutils import Vector
from typing import Dict, List, Tuple, Optional


# Default view direction definitions (can be overridden by config)
DEFAULT_VIEW_DIRECTIONS = {
    'front': {'direction': [0, -1, 0], 'is_perspective': False},
    'back': {'direction': [0, 1, 0], 'is_perspective': False},
    'left': {'direction': [1, 0, 0], 'is_perspective': False},
    'right': {'direction': [-1, 0, 0], 'is_perspective': False},
    'top': {'direction': [0, 0, 1], 'is_perspective': False},
    'bottom': {'direction': [0, 0, -1], 'is_perspective': False},
    'perspective_front': {'direction': [1, -1, 0.5], 'is_perspective': True},
    'perspective_back': {'direction': [-1, 1, 0.5], 'is_perspective': True},
}

# Supported file extensions
SUPPORTED_EXTENSIONS = ['.blend', '.obj', '.fbx', '.glb', '.gltf']


# System defaults
SYSTEM_DEFAULTS = {
    'image_format': {'format': 'PNG', 'color_mode': 'RGBA'},
    'render_engine': {
        'engine': 'CYCLES', 'device': 'CPU',
        'preview_samples': 16, 'use_adaptive_sampling': True, 'adaptive_threshold': 0.1
    },
    'camera': {
        'perspective_distance_multiplier': 1.8,
        'perspective_lens': 50,
        'perspective_sensor_width': 36
    },
    'lighting': {
        'light_type': 'SUN',
        'key_light_angle': 0.15,
        'fill_light_angle': 0.3,
        'rim_light_angle': 0.2
    }
}


def normalize_config(raw_config: dict) -> dict:
    """
    Normalize config to internal format.
    Config is typically pre-normalized by prepare-render, but handle edge cases.
    """
    if not raw_config:
        return None

    # Already normalized (has render_engine with engine key)
    if 'render_engine' in raw_config and 'engine' in raw_config.get('render_engine', {}):
        return raw_config

    # New simplified format from user config (quality instead of render_engine)
    if 'quality' in raw_config:
        return {
            'views': {
                'enabled': raw_config.get('views', {}).get('enabled', list(DEFAULT_VIEW_DIRECTIONS.keys())),
                'directions': DEFAULT_VIEW_DIRECTIONS
            },
            'image': {
                **SYSTEM_DEFAULTS['image_format'],
                'width': raw_config.get('image', {}).get('width', 512),
                'height': raw_config.get('image', {}).get('height', 512)
            },
            'render_engine': {
                **SYSTEM_DEFAULTS['render_engine'],
                'samples': raw_config.get('quality', {}).get('samples', 32),
                'use_denoising': raw_config.get('quality', {}).get('use_denoising', True)
            },
            'camera': {
                **SYSTEM_DEFAULTS['camera'],
                'distance_factor': raw_config.get('camera', {}).get('distance_factor', 3.0),
                'ortho_padding_factor': raw_config.get('camera', {}).get('ortho_padding_factor', 1.02)
            },
            'lighting': {
                'key_light': {
                    **raw_config.get('lighting', {}).get('key_light', {}),
                    'type': SYSTEM_DEFAULTS['lighting']['light_type'],
                    'angle': SYSTEM_DEFAULTS['lighting']['key_light_angle']
                },
                'fill_light': {
                    **raw_config.get('lighting', {}).get('fill_light', {}),
                    'type': SYSTEM_DEFAULTS['lighting']['light_type'],
                    'angle': SYSTEM_DEFAULTS['lighting']['fill_light_angle']
                },
                'rim_light': {
                    **raw_config.get('lighting', {}).get('rim_light', {}),
                    'type': SYSTEM_DEFAULTS['lighting']['light_type'],
                    'angle': SYSTEM_DEFAULTS['lighting']['rim_light_angle']
                },
                'background': raw_config.get('lighting', {}).get('background', {})
            },
            'timeouts': {
                'render_timeout_seconds': raw_config.get('timeout', {}).get('render_seconds', 90)
            }
        }

    # Return as-is (assume already normalized or will use defaults)
    return raw_config


def get_view_directions(config: dict = None) -> Dict[str, dict]:
    """Get view directions from config or defaults"""
    if config and 'views' in config and 'directions' in config['views']:
        directions = {}
        for name, data in config['views']['directions'].items():
            dir_list = data.get('direction', [0, 0, 1])
            directions[name] = {
                'direction': Vector(dir_list).normalized(),
                'is_perspective': data.get('is_perspective', False)
            }
        return directions

    # Return defaults with Vector conversion
    return {
        name: {
            'direction': Vector(data['direction']).normalized(),
            'is_perspective': data['is_perspective']
        }
        for name, data in DEFAULT_VIEW_DIRECTIONS.items()
    }


def log(event: str, **kwargs):
    """Log structured JSON message"""
    print(json.dumps({
        'event': event,
        'timestamp': datetime.now().isoformat(),
        **kwargs
    }))


def clear_scene():
    """Clear all objects from the scene"""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    log('scene_cleared')


def import_model(model_path: str) -> List:
    """Import 3D model and return mesh objects"""
    file_ext = os.path.splitext(model_path)[1].lower()
    log('importing_model', path=model_path, format=file_ext)

    if file_ext == '.blend':
        with bpy.data.libraries.load(model_path) as (data_from, data_to):
            data_to.objects = data_from.objects
        for obj in data_to.objects:
            if obj is not None:
                bpy.context.collection.objects.link(obj)

    elif file_ext == '.obj':
        bpy.ops.wm.obj_import(
            filepath=model_path,
            use_split_objects=False,
            use_split_groups=False
        )

    elif file_ext == '.fbx':
        bpy.ops.import_scene.fbx(
            filepath=model_path,
            use_image_search=True,
            use_anim=False,
            axis_forward='-Z',
            axis_up='Y'
        )

    elif file_ext in ['.glb', '.gltf']:
        bpy.ops.import_scene.gltf(
            filepath=model_path,
            import_pack_images=True
        )

    else:
        raise ValueError(f"Unsupported format: {file_ext}")

    bpy.context.view_layer.update()

    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    if not mesh_objects:
        raise ValueError("No mesh objects found in the model")

    log('model_imported', mesh_count=len(mesh_objects))
    return mesh_objects


def calculate_bounds(mesh_objects: List) -> Tuple[Vector, Vector]:
    """Calculate combined bounding box for mesh objects"""
    all_corners = []
    for obj in mesh_objects:
        bbox_corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        all_corners.extend(bbox_corners)

    min_x = min(corner.x for corner in all_corners)
    max_x = max(corner.x for corner in all_corners)
    min_y = min(corner.y for corner in all_corners)
    max_y = max(corner.y for corner in all_corners)
    min_z = min(corner.z for corner in all_corners)
    max_z = max(corner.z for corner in all_corners)

    # Calculate actual bounding box center and size
    center = Vector((
        (min_x + max_x) / 2,
        (min_y + max_y) / 2,
        (min_z + max_z) / 2
    ))
    size = Vector((
        max_x - min_x,
        max_y - min_y,
        max_z - min_z
    ))

    log('bounds_calculated', center=list(center), size=list(size))
    return center, size


def ensure_visibility(mesh_objects: List):
    """Ensure all objects are visible and renderable"""
    for obj in mesh_objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.select_set(False)


def process_materials(mesh_objects: List):
    """Process materials for mesh objects"""
    for obj in mesh_objects:
        if obj.data.materials:
            for mat in obj.data.materials:
                if mat and not mat.use_nodes:
                    mat.use_nodes = True
        else:
            mat = bpy.data.materials.new(name=f"Material_{obj.name}")
            mat.use_nodes = False
            mat.diffuse_color = (0.8, 0.8, 0.8, 1.0)
            obj.data.materials.append(mat)


def setup_camera():
    """Create and configure render camera"""
    bpy.ops.object.camera_add(location=(0, 0, 0))
    camera = bpy.context.object
    camera.name = "RenderCamera"
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 10.0
    bpy.context.scene.camera = camera
    return camera


def setup_lighting(config: dict = None):
    """Setup lighting for rendering with improved brightness"""
    # Get lighting config or use defaults
    lighting_config = config.get('lighting', {}) if config else {}

    # Key light defaults
    key_cfg = lighting_config.get('key_light', {})
    key_location = key_cfg.get('location', [5, -3, 10])
    key_energy = key_cfg.get('energy', 5.0)
    key_angle = key_cfg.get('angle', 0.15)

    # Fill light defaults
    fill_cfg = lighting_config.get('fill_light', {})
    fill_location = fill_cfg.get('location', [-5, 3, 5])
    fill_energy = fill_cfg.get('energy', 2.0)
    fill_angle = fill_cfg.get('angle', 0.3)

    # Rim light defaults
    rim_cfg = lighting_config.get('rim_light', {})
    rim_location = rim_cfg.get('location', [0, 5, 8])
    rim_energy = rim_cfg.get('energy', 1.5)
    rim_angle = rim_cfg.get('angle', 0.2)

    # Background defaults
    bg_cfg = lighting_config.get('background', {})
    bg_color = bg_cfg.get('color', [0.4, 0.4, 0.4, 1.0])
    bg_strength = bg_cfg.get('strength', 1.0)

    # Key light (main light from upper front)
    bpy.ops.object.light_add(type='SUN', location=tuple(key_location))
    key_light = bpy.context.object
    key_light.data.energy = key_energy
    key_light.name = "KeyLight"
    key_light.data.angle = key_angle

    # Fill light (soften shadows from opposite side)
    bpy.ops.object.light_add(type='SUN', location=tuple(fill_location))
    fill_light = bpy.context.object
    fill_light.data.energy = fill_energy
    fill_light.name = "FillLight"
    fill_light.data.angle = fill_angle

    # Rim light (back lighting for edge definition)
    bpy.ops.object.light_add(type='SUN', location=tuple(rim_location))
    rim_light = bpy.context.object
    rim_light.data.energy = rim_energy
    rim_light.name = "RimLight"
    rim_light.data.angle = rim_angle

    # World background (brighter for better visibility)
    world = bpy.context.scene.world
    if world.use_nodes:
        world_nodes = world.node_tree.nodes
        world_nodes.clear()
        bg_node = world_nodes.new(type='ShaderNodeBackground')
        bg_node.inputs['Color'].default_value = tuple(bg_color)
        bg_node.inputs['Strength'].default_value = bg_strength
        output_node = world_nodes.new(type='ShaderNodeOutputWorld')
        world.node_tree.links.new(bg_node.outputs['Background'], output_node.inputs['Surface'])

    log('lighting_setup', key_energy=key_energy, fill_energy=fill_energy, rim_energy=rim_energy, bg_strength=bg_strength)


def configure_render_settings(config: dict = None):
    """Configure Cycles render settings for CPU-based headless rendering"""
    # Get settings from config or use defaults
    image_config = config.get('image', {}) if config else {}
    engine_config = config.get('render_engine', {}) if config else {}

    resolution_x = image_config.get('width', 512)
    resolution_y = image_config.get('height', 512)
    file_format = image_config.get('format', 'PNG')
    color_mode = image_config.get('color_mode', 'RGBA')

    engine = engine_config.get('engine', 'CYCLES')
    device = engine_config.get('device', 'CPU')
    samples = engine_config.get('samples', 32)
    preview_samples = engine_config.get('preview_samples', 16)
    use_denoising = engine_config.get('use_denoising', True)
    use_adaptive_sampling = engine_config.get('use_adaptive_sampling', True)
    adaptive_threshold = engine_config.get('adaptive_threshold', 0.1)

    bpy.context.scene.render.engine = engine
    bpy.context.scene.render.resolution_x = resolution_x
    bpy.context.scene.render.resolution_y = resolution_y
    bpy.context.scene.render.image_settings.file_format = file_format
    bpy.context.scene.render.image_settings.color_mode = color_mode

    # CPU rendering for Lambda (no GPU available)
    bpy.context.scene.cycles.device = device

    # Optimize Cycles for speed (fewer samples = faster but noisier)
    bpy.context.scene.cycles.samples = samples
    bpy.context.scene.cycles.preview_samples = preview_samples
    bpy.context.scene.cycles.use_denoising = use_denoising
    bpy.context.scene.cycles.use_adaptive_sampling = use_adaptive_sampling
    bpy.context.scene.cycles.adaptive_threshold = adaptive_threshold

    # Use fast GI approximation
    try:
        bpy.context.scene.cycles.fast_gi_method = 'REPLACE'
    except AttributeError:
        pass

    log('render_settings_configured',
        engine=engine,
        resolution=f'{resolution_x}x{resolution_y}',
        samples=samples)


def position_camera(
    camera,
    center: Vector,
    size: Vector,
    view_direction: Vector,
    is_perspective: bool,
    config: dict = None
):
    """Position camera for the specified view"""
    # Get camera config or use defaults
    camera_config = config.get('camera', {}) if config else {}
    distance_factor = camera_config.get('distance_factor', 3.0)
    perspective_distance_multiplier = camera_config.get('perspective_distance_multiplier', 1.8)
    perspective_lens = camera_config.get('perspective_lens', 50)
    perspective_sensor_width = camera_config.get('perspective_sensor_width', 36)
    ortho_padding_factor = camera_config.get('ortho_padding_factor', 1.1)

    max_dimension = max(size)

    if is_perspective:
        # Calculate distance from lens FOV to fit object in frame
        import math
        fov = 2 * math.atan(perspective_sensor_width / (2 * perspective_lens))
        distance = max((max_dimension / 2) / math.tan(fov / 2) * 1.4, 1.0)
    else:
        distance = max(max_dimension * distance_factor, 1.0)

    camera_location = center + (view_direction * distance)
    camera.location = camera_location

    direction = center - camera_location
    rot_quat = direction.to_track_quat('-Z', 'Y')
    camera.rotation_euler = rot_quat.to_euler()

    if is_perspective:
        camera.data.type = 'PERSP'
        camera.data.lens = perspective_lens
        camera.data.sensor_width = perspective_sensor_width
    else:
        camera.data.type = 'ORTHO'
        # Calculate ortho_scale based on the two axes visible from this view direction
        abs_dir = Vector((abs(view_direction.x), abs(view_direction.y), abs(view_direction.z)))
        if abs_dir.x >= abs_dir.y and abs_dir.x >= abs_dir.z:
            visible_size = max(size.y, size.z)  # Looking along X
        elif abs_dir.y >= abs_dir.x and abs_dir.y >= abs_dir.z:
            visible_size = max(size.x, size.z)  # Looking along Y
        else:
            visible_size = max(size.x, size.y)  # Looking along Z
        camera.data.ortho_scale = max(visible_size * ortho_padding_factor, 1.0)


def render_view(camera, center: Vector, size: Vector, view_name: str, output_path: str, config: dict = None):
    """Render a single view"""
    view_directions = get_view_directions(config)
    view_config = view_directions.get(view_name)
    if not view_config:
        raise ValueError(f"Unknown view: {view_name}")

    log('rendering_view', view=view_name)

    # Position camera
    position_camera(
        camera,
        center,
        size,
        view_config['direction'],
        view_config['is_perspective'],
        config
    )

    bpy.context.view_layer.update()

    # Render - remove extension since Blender adds it based on file format
    output_base = os.path.splitext(output_path)[0]
    bpy.context.scene.render.filepath = output_base
    bpy.ops.render.render(write_still=True)

    # Blender adds the extension, so the actual file is at output_base + '.png'
    actual_output = output_base + '.png'
    log('view_rendered', view=view_name, output=actual_output)

    # Rename to expected path if different
    if actual_output != output_path and os.path.exists(actual_output):
        os.rename(actual_output, output_path)


def main():
    """Main entry point"""
    # Parse arguments after '--'
    try:
        separator_index = sys.argv.index('--')
        script_args = sys.argv[separator_index + 1:]
    except ValueError:
        script_args = sys.argv[1:]

    parser = argparse.ArgumentParser(description='Render single view of 3D model')
    parser.add_argument('--model_path', required=True, help='Path to 3D model file')
    parser.add_argument('--view_name', required=True, help='View name to render')
    parser.add_argument('--output_path', required=True, help='Output PNG path')
    parser.add_argument('--config', help='JSON string with rendering configuration')

    args = parser.parse_args(script_args)

    # Parse config if provided
    config = None
    if args.config:
        try:
            raw_config = json.loads(args.config)
            config = normalize_config(raw_config)
        except json.JSONDecodeError as e:
            log('config_parse_error', error=str(e))

    log('render_started', model=args.model_path, view=args.view_name, has_config=config is not None)

    try:
        # Clear scene
        clear_scene()

        # Import model
        mesh_objects = import_model(args.model_path)

        # Calculate bounds
        center, size = calculate_bounds(mesh_objects)

        # Configure render
        configure_render_settings(config)

        # Process objects
        ensure_visibility(mesh_objects)
        process_materials(mesh_objects)

        # Setup camera and lighting
        camera = setup_camera()
        setup_lighting(config)

        # Render the view
        render_view(camera, center, size, args.view_name, args.output_path, config)

        log('render_completed', view=args.view_name)

    except Exception as e:
        log('render_failed', error=str(e), error_type=type(e).__name__)
        raise


if __name__ == "__main__":
    main()
