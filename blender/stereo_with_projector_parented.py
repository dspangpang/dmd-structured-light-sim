# -*- coding: utf-8 -*-
"""Shared pure-render entry point; branch behavior is provided by variant.py."""

import glob
import os
import sys
import traceback

import bpy

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
SRC_DIR = os.path.join(REPO_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from app_scene_config import snapshot_app_scene_config
from gwcam_calibration import save_gwcam_calibration
from blender_parameters import save_blender_parameters
from cli import parse_blender_args
from projector import SIM_TAG_KEY, TAG_PROJECTOR_ROOT, update_projector_image
from render import (
    prepare_camera_preview,
    render_cameras,
    setup_render,
    validate_blender_camera_intrinsics,
)
from render_manifest import RenderRun
from render_request import snapshot_render_request
from scene import build_scene
from variant_api import get_variant


def _merge_artifact_groups(*groups):
    merged = {}
    for group_set in groups:
        for group_name, records in (group_set or {}).items():
            merged.setdefault(str(group_name), []).extend(list(records))
    return merged


def _resolve_repo_path(path):
    if path.startswith("//"):
        return os.path.join(REPO_ROOT, path[2:])
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)


def _collect_projector_images(input_path):
    projection_files = []
    for extension in ("bmp", "png", "jpg", "jpeg", "tif", "tiff"):
        projection_files.extend(glob.glob(os.path.join(input_path, f"proj_*.{extension}")))
        projection_files.extend(glob.glob(os.path.join(input_path, f"proj_*.{extension.upper()}")))
    if projection_files:
        return sorted(set(projection_files))
    image_files = []
    for extension in ("png", "jpg", "jpeg", "bmp", "tif", "tiff"):
        image_files.extend(glob.glob(os.path.join(input_path, f"*.{extension}")))
        image_files.extend(glob.glob(os.path.join(input_path, f"*.{extension.upper()}")))
    return sorted(set(image_files))


def _read_projector_image_size(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Projector image not found: {path}")
    image = bpy.data.images.load(filepath=path, check_existing=True)
    return int(image.size[0]), int(image.size[1])


def _resolve_projector_image_size(image_files):
    sizes = {_read_projector_image_size(path) for path in image_files}
    if len(sizes) != 1:
        raise ValueError(f"Projector sequence mixes image resolutions: {sorted(sizes)}")
    return next(iter(sizes))


def _projector_objects():
    return [
        obj for obj in bpy.data.objects
        if obj.get(SIM_TAG_KEY) == TAG_PROJECTOR_ROOT
    ]


def _normalized_capture_mode(cfg):
    mode = str(getattr(cfg, "CAPTURE_MODE", "projector_sequence") or "projector_sequence")
    mode = mode.strip().lower().replace("-", "_")
    return {
        "default": "projector_sequence",
        "sequence": "projector_sequence",
        "projector": "projector_sequence",
    }.get(mode, mode)


def _render_projector_sequence(image_files, projectors, cameras, cfg, sequence_dir):
    image_format = getattr(cfg, "PROJECTOR_CAPTURE_IMAGE_FORMAT", "BMP")
    for image_path in image_files:
        for projector in projectors:
            update_projector_image(projector, image_path)
        if len(image_files) > 1:
            stem = os.path.splitext(os.path.basename(image_path))[0]
            output_dir = os.path.join(sequence_dir, stem)
        else:
            output_dir = sequence_dir
        render_cameras(
            cameras,
            cfg,
            output_dir=output_dir,
            image_format=image_format,
        )


def main():
    cfg = parse_blender_args()
    variant = get_variant()
    if not cfg.PROJECTOR_IMAGE:
        raise ValueError("--img is required for the main projector render entry")

    input_path = _resolve_repo_path(cfg.PROJECTOR_IMAGE)
    if os.path.isdir(input_path):
        image_files = _collect_projector_images(input_path)
        sequence_name = os.path.basename(os.path.normpath(input_path))
    else:
        image_files = [input_path]
        sequence_name = os.path.splitext(os.path.basename(input_path))[0]
    if not image_files:
        raise FileNotFoundError(f"No projector images found: {input_path}")
    cfg.PROJECTOR_IMAGE = image_files[0]

    projector_resolution = _resolve_projector_image_size(image_files)
    expected_resolution = (int(cfg.APP_PROJECTOR_RES_X), int(cfg.APP_PROJECTOR_RES_Y))
    if projector_resolution != expected_resolution:
        raise ValueError(
            "Projector image resolution disagrees with the App scene: "
            f"image={projector_resolution}, App={expected_resolution}"
        )

    target_name = "board"
    target_path = None
    if cfg.USE_CUSTOM_TARGET and cfg.CUSTOM_TARGET_PATH:
        target_name = os.path.splitext(os.path.basename(cfg.CUSTOM_TARGET_PATH))[0]
        target_path = _resolve_repo_path(cfg.CUSTOM_TARGET_PATH)
        if not os.path.isfile(target_path):
            raise FileNotFoundError(f"Custom target not found: {target_path}")
    base_dir = bpy.path.abspath("//") or os.getcwd()
    sequence_dir = os.path.join(
        base_dir,
        cfg.OUTPUT_DIR,
        f"{sequence_name}_{target_name}",
    )
    with RenderRun(
        sequence_dir,
        cfg,
        repo_root=REPO_ROOT,
        variant_name=variant.name,
        run_type="projector_sequence",
        projector_images=image_files,
        target_path=target_path,
        blender_version=bpy.app.version_string,
    ) as run:
        snapshot_app_scene_config(cfg, sequence_dir)
        snapshot_render_request(cfg, sequence_dir)
        save_gwcam_calibration(
            cfg,
            sequence_dir,
            projector_resolution=projector_resolution,
        )
        if cfg.METADATA_ONLY:
            run.complete(status="METADATA_ONLY")
            print("[Main] METADATA_ONLY set; validated and snapshotted contracts")
            return

        cameras = build_scene(
            cfg,
            bake_dir=os.path.join(sequence_dir, "baked_textures"),
        )
        setup_render(cfg)
        validate_blender_camera_intrinsics(cameras, cfg)
        save_blender_parameters(cfg, sequence_dir)
        projectors = _projector_objects()
        if not projectors:
            raise RuntimeError("Projector objects not found after building the scene")
        variant.after_scene_built(cfg, cameras, sequence_dir)

        if cfg.NO_RENDER:
            for projector in projectors:
                update_projector_image(projector, image_files[0])
            variant.prepare_no_render(image_files, projectors, cfg)
            prepare_camera_preview(cameras)
            run.complete(
                camera_names=[camera.name for camera in cameras],
                status="VALIDATED",
            )
            print("[Main] validation completed; scene built without capture")
            return

        pre_capture_artifacts = variant.capture_pre_render_artifacts(
            cameras,
            cfg,
            sequence_dir,
        )
        if variant.render_capture(
            image_files,
            projectors,
            cameras,
            cfg,
            sequence_dir,
        ):
            extra_artifacts = _merge_artifact_groups(
                pre_capture_artifacts,
                variant.capture_artifact_groups(),
            )
            run.complete(
                camera_names=[camera.name for camera in cameras],
                expected_minimum=variant.expected_main_camera_file_count(
                    image_files, cameras, cfg
                ),
                extra_artifacts=extra_artifacts,
            )
            return
        mode = _normalized_capture_mode(cfg)
        if mode != "projector_sequence":
            raise ValueError(
                f"Capture mode {cfg.CAPTURE_MODE!r} is not implemented by variant {variant.name!r}"
            )
        _render_projector_sequence(
            image_files,
            projectors,
            cameras,
            cfg,
            sequence_dir,
        )
        run.complete(
            camera_names=[camera.name for camera in cameras],
            expected_minimum=variant.expected_main_camera_file_count(
                image_files, cameras, cfg
            ),
            extra_artifacts=_merge_artifact_groups(
                pre_capture_artifacts,
                variant.capture_artifact_groups(),
            ),
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
