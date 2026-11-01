# -*- coding: utf-8 -*-
import glob
import json
import os
import sys
import traceback

import bpy
from mathutils import Matrix

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
SRC_DIR = os.path.join(REPO_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from calibration_board_plan import (
    load_calibration_board_plan,
    snapshot_calibration_board_plan,
)
from calibration import save_calibration
from blender_parameters import save_blender_parameters
from cli import parse_blender_args
from app_scene_config import snapshot_app_scene_config
from projector import SIM_TAG_KEY, TAG_PROJECTOR_ROOT, update_projector_image
from render import render_cameras, setup_render, validate_blender_camera_intrinsics
from render_manifest import RenderRun
from render_request import snapshot_render_request
from scene import CALIBRATION_BOARD_TAG, build_scene
from variant_api import get_variant


def _resolve_repo_path(path):
    if path.startswith("//"):
        return os.path.join(REPO_ROOT, path[2:])
    if os.path.isabs(path):
        return path
    return os.path.join(REPO_ROOT, path)


def _collect_projector_images(input_path):
    projection_files = []
    for ext in ("*.bmp", "*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff"):
        projection_files.extend(glob.glob(os.path.join(input_path, f"proj_{ext}")))
        projection_files.extend(glob.glob(os.path.join(input_path, f"proj_{ext.upper()}")))

    if projection_files:
        projection_files.sort()
        print(f"[Calibration] Using proj_* image sequence: {len(projection_files)} file(s)")
        return projection_files

    image_files = []
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tif", "*.tiff"):
        image_files.extend(glob.glob(os.path.join(input_path, ext)))
        image_files.extend(glob.glob(os.path.join(input_path, ext.upper())))
    image_files.sort()
    print(f"[Calibration] Using all image files in directory: {len(image_files)} file(s)")
    return image_files


def _read_projector_image_size(image_path):
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Projector image not found: {image_path}")
    image = bpy.data.images.load(filepath=image_path, check_existing=True)
    return int(image.size[0]), int(image.size[1])


def _resolve_projector_image_size(image_files):
    sizes = {}
    for image_file in image_files:
        size = _read_projector_image_size(image_file)
        sizes.setdefault(size, []).append(image_file)
    if len(sizes) != 1:
        lines = [
            f"{width}x{height}: {len(paths)} file(s)"
            for (width, height), paths in sorted(sizes.items())
        ]
        raise ValueError(
            "Projector image sequence must use one resolution, got: "
            + "; ".join(lines)
        )
    return next(iter(sizes))


def _projection_frame_name(image_path):
    stem = os.path.splitext(os.path.basename(image_path))[0]
    suffix = "_dlp4500_preprojected"
    if stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    return stem


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _find_calibration_board_root():
    for obj in bpy.data.objects:
        if obj.get(SIM_TAG_KEY) == CALIBRATION_BOARD_TAG:
            return obj
    obj = bpy.data.objects.get("Calibration_Circle_Grid")
    if obj is not None:
        return obj
    raise RuntimeError("Calibration circle grid board root not found")


def _find_projector_objects():
    return [
        obj for obj in bpy.data.objects
        if obj.get(SIM_TAG_KEY) == TAG_PROJECTOR_ROOT
    ]


def _pose_payload(projector_resolution, loaded_plan, pose_record):
    return {
        "schema": "core-sim.calibration_board.render_pose.v1",
        "calibration_board_plan_sha256": loaded_plan["sha256"],
        "projector_resolution": list(projector_resolution),
        "pose": pose_record,
    }


def main():
    cfg = parse_blender_args()
    cfg.CALIBRATION_CIRCLE_GRID = True

    if not cfg.PROJECTOR_IMAGE:
        raise ValueError("Circle-grid rendering requires --img")

    input_path = _resolve_repo_path(cfg.PROJECTOR_IMAGE)
    if os.path.isdir(input_path):
        sequence_name = os.path.basename(os.path.normpath(input_path))
        image_files = _collect_projector_images(input_path)
        if not image_files:
            raise FileNotFoundError(f"No projector images found in {input_path}")
        cfg.PROJECTOR_IMAGE = image_files[0]
    else:
        image_files = [input_path]
        sequence_name = os.path.splitext(os.path.basename(input_path))[0]

    projector_resolution = _resolve_projector_image_size(image_files)
    plan_path = str(getattr(cfg, "CALIBRATION_BOARD_PLAN_PATH", "") or "")
    if not plan_path:
        raise ValueError(
            "Calibration rendering requires --calibration-board-plan. "
            "Supply a JSON board plan with board dimensions and explicit poses."
        )
    loaded_plan = load_calibration_board_plan(
        plan_path,
        cfg,
        projector_resolution=projector_resolution,
    )
    if loaded_plan["board"].get("type") != "symmetric_circles_grid":
        raise ValueError(
            "render_calibration_circle_grid.py requires a "
            "symmetric_circles_grid board plan"
        )
    accepted = loaded_plan["poses"]
    print(f"[Calibration] Projector resolution: {projector_resolution[0]}x{projector_resolution[1]}")
    print(f"[Calibration] Board plan: {loaded_plan['path']}")
    print(f"[Calibration] Board plan SHA256: {loaded_plan['sha256']}")
    print(f"[Calibration] Poses loaded: {len(accepted)}")

    base_dir = bpy.path.abspath("//") or os.getcwd()
    sequence_dir = os.path.join(
        base_dir,
        cfg.OUTPUT_DIR,
        "calibration_circle_grid",
        sequence_name,
    )
    variant = get_variant()
    plan_record = {
        "path": loaded_plan["path"],
        "sha256": loaded_plan["sha256"],
        "schema": loaded_plan["raw"]["schema"],
    }
    with RenderRun(
        sequence_dir,
        cfg,
        repo_root=REPO_ROOT,
        variant_name=variant.name,
        run_type="calibration_circle_grid",
        projector_images=image_files,
        calibration_plan=plan_record,
        blender_version=bpy.app.version_string,
    ) as run:
        snapshot_app_scene_config(cfg, sequence_dir)
        snapshot_render_request(cfg, sequence_dir)
        snapshot_calibration_board_plan(loaded_plan, sequence_dir)
        save_calibration(
            cfg,
            sequence_dir,
            projector_resolution=projector_resolution,
        )

        manifest = {
            "sequence_name": sequence_name,
            "projector_image_count": len(image_files),
            "projector_resolution": list(projector_resolution),
            "projector_images": [os.path.relpath(path, REPO_ROOT) for path in image_files],
            "calibration_board_plan": plan_record,
            "board": loaded_plan["raw"]["board"],
            "pose_count": len(accepted),
            "poses": accepted,
        }
        _write_json(os.path.join(sequence_dir, "manifest.json"), manifest)
        for pose in accepted:
            pose_dir = os.path.join(sequence_dir, f"pose_{pose['pose_index']:03d}")
            _write_json(
                os.path.join(pose_dir, "pose.json"),
                _pose_payload(projector_resolution, loaded_plan, pose),
            )
        if cfg.METADATA_ONLY:
            run.complete(status="METADATA_ONLY")
            print("[Calibration] metadata and imported pose truth validated")
            return

        cfg.CALIB_BOARD_POSE_MATRIX = accepted[0]["matrix_world"]
        cameras = build_scene(cfg)
        setup_render(cfg)
        validate_blender_camera_intrinsics(cameras, cfg)
        save_blender_parameters(cfg, sequence_dir)
        board_root = _find_calibration_board_root()
        projector_objs = _find_projector_objects()
        if not projector_objs:
            raise RuntimeError("Projector objects not found")
        variant.after_scene_built(cfg, cameras, sequence_dir)

        if cfg.NO_RENDER:
            for projector_obj in projector_objs:
                update_projector_image(projector_obj, image_files[0])
            variant.prepare_no_render(image_files, projector_objs, cfg)
            run.complete(
                camera_names=[camera.name for camera in cameras],
                status="VALIDATED",
            )
            print("[Calibration] validation completed; board scene built without capture")
            return

        for pose in accepted:
            pose_index = pose["pose_index"]
            pose_dir = os.path.join(sequence_dir, f"pose_{pose_index:03d}")
            print(f"\n[Calibration] Rendering pose {pose_index:03d}")
            board_root.matrix_world = Matrix(pose["matrix_world"])
            bpy.context.view_layer.update()

            for image_path in image_files:
                frame_name = _projection_frame_name(image_path)
                print(f"[Calibration] Projection frame: {frame_name}")
                for projector_obj in projector_objs:
                    update_projector_image(projector_obj, image_path)
                frame_dir = os.path.join(pose_dir, frame_name)
                render_cameras(
                    cameras,
                    cfg,
                    output_dir=frame_dir,
                    image_format=getattr(cfg, "PROJECTOR_CAPTURE_IMAGE_FORMAT", "BMP"),
                )
        run.complete(
            camera_names=[camera.name for camera in cameras],
            expected_minimum=len(accepted) * len(image_files) * len(cameras),
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
