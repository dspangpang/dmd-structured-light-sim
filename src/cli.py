"""Blender CLI: render options only; all device geometry comes from the App."""

import sys
from pathlib import Path

from app_scene_config import apply_app_scene_config, scene_config_from_argv
from config import SimConfig
from render_presets import apply_render_preset, render_preset_from_argv
from render_request import (
    apply_render_request,
    load_render_request,
    render_request_from_argv,
    render_request_preset,
)
from variant_api import get_variant


def _parse_bool(value):
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


STRUCTURAL_OVERRIDE_FLAGS = {
    "--res",
    "--f-mm",
    "--focal-mm",
    "--camera-focal-mm",
    "--camera-topology",
    "--topology",
    "--baseline",
    "--stereo-baseline",
    "--toe-in-deg",
    "--cam-proj-baseline",
    "--camera-projector-baseline",
    "--cam-proj-baseline-angle-deg",
    "--camera-projector-baseline-angle-deg",
    "--camera-yaw-deg",
    "--auto-camera-toe-in",
    "--projector-yaw-deg",
    "--projector-roll-deg",
    "--projector-throw-ratio",
    "--projector-lens-shift-x",
    "--projector-shift-x",
    "--projector-lens-shift-y",
    "--projector-shift-y",
    "--projector-texture-mirror-x",
    "--projector-mirror-x",
    "--projector-texture-mirror-y",
    "--projector-mirror-y",
    "--multi-rig",
    "--rig-rows",
    "--rig-cols",
    "--rig-spacing-x",
    "--rig-spacing-y",
    "--rig-yaw-step-deg",
    "--rig-pitch-step-deg",
}


def _required_value(argv, index, key):
    if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
        raise ValueError(f"{key} requires a value")
    return argv[index + 1]


def parse_blender_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []

    variant = get_variant()
    cfg = SimConfig()
    variant.configure_defaults(cfg)
    repo_root = Path(__file__).resolve().parents[1]
    request_path = render_request_from_argv(argv, variant, repo_root)
    loaded_request = load_render_request(request_path, repo_root=repo_root)
    apply_render_preset(
        cfg,
        render_preset_from_argv(argv, default=render_request_preset(loaded_request)),
    )
    # Resolve the scene path from the request before loading the authoritative
    # camera/projector contract.  The per-render target block is applied again
    # below so target translation/rotation/scale override the scene contract's
    # default target reference instead of being silently reset to zero.
    request_scene_path = apply_render_request(
        cfg, loaded_request, variant
    )
    scene_config_path = scene_config_from_argv(argv) or request_scene_path
    if not scene_config_path:
        raise ValueError(
            "A --scene-config or standalone scripts/run_simulation.py config is required; "
            "Blender has no fallback camera/projector parameter model"
        )
    apply_app_scene_config(cfg, scene_config_path)
    # apply_app_scene_config initializes TARGET_CENTER_* from the scene's
    # target_reference.  A render request is target-specific, so it must win
    # over that default.  Do this before parsing explicit CLI overrides so
    # command-line options retain their existing precedence.
    apply_render_request(cfg, loaded_request, variant)

    i = 0
    while i < len(argv):
        key = argv[i]
        if not key.startswith("--"):
            raise ValueError(f"Unexpected positional Blender argument: {key}")
        if key in {"--no-render", "--validate-only"}:
            cfg.NO_RENDER = True
            i += 1
            continue
        if key == "--metadata-only":
            cfg.METADATA_ONLY = True
            i += 1
            continue

        value = _required_value(argv, i, key)
        if key in STRUCTURAL_OVERRIDE_FLAGS:
            raise ValueError(
                f"{key} is a structural override; change it in "
                "the source scene configuration and generate a new --scene-config"
            )
        if key == "--img":
            cfg.PROJECTOR_IMAGE = value
        elif key in {"--render-preset", "--render-request"}:
            pass
        elif key in {"--scene-config", "--structured-light-scene-config"}:
            scene_config_path = value
        elif key == "--out":
            cfg.OUTPUT_DIR = value
        elif key == "--samples":
            cfg.SAMPLES = int(value)
        elif key == "--use-gpu":
            cfg.USE_GPU = _parse_bool(value)
        elif key == "--cycles-device":
            cfg.CYCLES_DEVICE = value.upper()
        elif key == "--cycles-device-name":
            cfg.CYCLES_DEVICE_NAME = value
        elif key == "--persistent-data":
            cfg.USE_PERSISTENT_DATA = _parse_bool(value)
        elif key == "--compositor-device":
            cfg.COMPOSITOR_DEVICE = value.upper()
        elif key == "--render-threads":
            cfg.RENDER_THREADS = int(value)
        elif key == "--cycles-denoising":
            cfg.CYCLES_USE_DENOISING = _parse_bool(value)
        elif key == "--cycles-pixel-filter":
            cfg.CYCLES_PIXEL_FILTER_TYPE = value.upper()
        elif key == "--cycles-filter-width":
            cfg.CYCLES_FILTER_WIDTH = float(value)
        elif key == "--render-res":
            width, height = value.lower().split("x")
            cfg.RENDER_RES_X = int(width)
            cfg.RENDER_RES_Y = int(height)
        elif key in {"--projector-texture-interpolation", "--projector-interpolation"}:
            cfg.PROJECTOR_TEXTURE_INTERPOLATION = value
        elif key == "--use-projector-model":
            cfg.USE_PROJECTOR_MODEL = _parse_bool(value)
        elif key == "--use-camera-model":
            cfg.USE_CAMERA_MODEL = _parse_bool(value)
        elif key == "--capture-mode":
            cfg.CAPTURE_MODE = value
        elif key == "--projector-capture-dir":
            cfg.PROJECTOR_CAPTURE_OUTPUT_DIR = value
        elif key == "--projector-capture-format":
            cfg.PROJECTOR_CAPTURE_IMAGE_FORMAT = value.upper()
        elif key == "--target":
            cfg.CUSTOM_TARGET_PATH = value
            cfg.USE_CUSTOM_TARGET = True
        elif key == "--use-custom-target":
            cfg.USE_CUSTOM_TARGET = _parse_bool(value)
        elif key == "--target-shape":
            cfg.TARGET_SHAPE = value.lower()
        elif key == "--energy":
            cfg.PROJECTOR_ENERGY = float(value)
        elif key == "--mat-albedo":
            cfg.MAT_ALBEDO = float(value)
        elif key == "--mat-roughness":
            cfg.MAT_ROUGHNESS = float(value)
        elif key == "--mat-metallic":
            cfg.MAT_METALLIC = float(value)
        elif key == "--mat-subsurface":
            cfg.MAT_SUBSURFACE = float(value)
        elif key == "--use-ply-brdf":
            cfg.USE_PLY_BRDF = _parse_bool(value)
        elif key == "--save-depth":
            cfg.SAVE_DEPTH = _parse_bool(value)
        elif key in {"--save-render-float-exr", "--save-float-exr"}:
            cfg.SAVE_RENDER_FLOAT_EXR = _parse_bool(value)
        elif variant.parse_cli_option(cfg, key, value, _parse_bool):
            pass
        else:
            raise ValueError(f"Unknown Blender render option for {variant.name}: {key}")
        i += 2

    print(f"[CLI] Variant: {variant.name}")
    print("[CLI] Parsed config:", cfg)
    return cfg
