# -*- coding: utf-8 -*-
"""Render externally planned checkerboard poses; never generate poses here."""

import json
import os
import struct
import sys
import traceback

import bpy
from mathutils import Matrix

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
SRC_DIR = os.path.join(REPO_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from app_scene_config import snapshot_app_scene_config
from gwcam_calibration import save_gwcam_calibration
from blender_parameters import save_blender_parameters
from calibration_board_plan import (
    load_calibration_board_plan,
    snapshot_calibration_board_plan,
)
from cli import parse_blender_args
from projector import (
    SIM_TAG_KEY,
    TAG_PROJECTOR_ROOT,
    set_projector_pixel_grid,
    set_projector_energy,
    update_projector_image,
    validate_projector_projection_state,
)
from render import render_cameras, setup_render, validate_blender_camera_intrinsics
from render_manifest import RenderRun
from render_request import snapshot_render_request
from scene import CALIBRATION_CHECKERBOARD_TAG, build_scene
from variant_api import get_variant


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")


def _create_white_projector_image(cfg, sequence_dir):
    width = int(cfg.APP_PROJECTOR_RES_X)
    height = int(cfg.APP_PROJECTOR_RES_Y)
    value = max(0, min(255, int(cfg.CALIB_WHITE_PROJECTOR_VALUE)))
    path = os.path.join(sequence_dir, "white_projector.bmp")
    row_bytes = width * 3
    padding = (4 - row_bytes % 4) % 4
    row_stride = row_bytes + padding
    pixel_data_size = row_stride * height
    with open(path, "wb") as stream:
        stream.write(struct.pack("<2sIHHI", b"BM", 54 + pixel_data_size, 0, 0, 54))
        stream.write(
            struct.pack(
                "<IIIHHIIIIII",
                40,
                width,
                height,
                1,
                24,
                0,
                pixel_data_size,
                2835,
                2835,
                0,
                0,
            )
        )
        row = bytes((value, value, value)) * width + b"\x00" * padding
        for _ in range(height):
            stream.write(row)
    return path


def _find_board_root():
    for obj in bpy.data.objects:
        if obj.get(SIM_TAG_KEY) == CALIBRATION_CHECKERBOARD_TAG:
            return obj
    raise RuntimeError("Checkerboard root not found")


def _find_projectors():
    return [
        obj for obj in bpy.data.objects
        if obj.get(SIM_TAG_KEY) == TAG_PROJECTOR_ROOT
    ]


def _configure_white_projectors(projectors, white_image):
    states = []
    for projector in projectors:
        update_projector_image(projector, white_image)
        set_projector_pixel_grid(projector, False)
        states.append(
            validate_projector_projection_state(
                projector,
                white_image,
                pixel_grid_enabled=False,
            )
        )
    return states


def main():
    cfg = parse_blender_args()
    cfg.CALIBRATION_CHECKERBOARD = True
    cfg.USE_CUSTOM_TARGET = False

    plan_path = str(getattr(cfg, "CALIBRATION_BOARD_PLAN_PATH", "") or "")
    if not plan_path:
        raise ValueError(
            "Checkerboard rendering requires --calibration-board-plan; "
            "Blender does not generate board poses"
        )
    projector_resolution = (
        int(cfg.APP_PROJECTOR_RES_X),
        int(cfg.APP_PROJECTOR_RES_Y),
    )
    loaded_plan = load_calibration_board_plan(
        plan_path,
        cfg,
        projector_resolution=projector_resolution,
    )
    if loaded_plan["board"].get("type") != "checkerboard":
        raise ValueError(
            "render_calibration_checkerboard.py requires a checkerboard board plan"
        )
    poses = loaded_plan["poses"]

    base_dir = bpy.path.abspath("//") or os.getcwd()
    sequence_dir = os.path.join(base_dir, cfg.OUTPUT_DIR, "calibration_checkerboard")
    variant = get_variant()
    plan_record = {
        "path": loaded_plan["path"],
        "sha256": loaded_plan["sha256"],
        "schema": loaded_plan["schema"],
    }
    with RenderRun(
        sequence_dir,
        cfg,
        repo_root=REPO_ROOT,
        variant_name=variant.name,
        run_type="calibration_checkerboard",
        calibration_plan=plan_record,
        blender_version=bpy.app.version_string,
        extra_request={"white_projector_value": int(cfg.CALIB_WHITE_PROJECTOR_VALUE)},
    ) as run:
        snapshot_app_scene_config(cfg, sequence_dir)
        snapshot_render_request(cfg, sequence_dir)
        snapshot_calibration_board_plan(loaded_plan, sequence_dir)

        white_image = _create_white_projector_image(cfg, sequence_dir)
        cfg.PROJECTOR_IMAGE = white_image
        save_gwcam_calibration(
            cfg,
            sequence_dir,
            projector_resolution=projector_resolution,
        )
        _write_json(
            os.path.join(sequence_dir, "manifest.json"),
            {
                "capture_type": "externally_planned_checkerboard",
                "calibration_board_plan": plan_record,
                "board": loaded_plan["raw"]["board"],
                "projector_resolution": list(projector_resolution),
                "projector_image": os.path.basename(white_image),
                "projection_contract": {
                    "texture_mode": "custom_white_image",
                    "white_projector_value": int(cfg.CALIB_WHITE_PROJECTOR_VALUE),
                    "pixel_grid_enabled": False,
                    "checkerboard_surface_model": "coplanar_printed_material",
                },
                "pose_count": len(poses),
                "poses": poses,
            },
        )
        for pose in poses:
            _write_json(
                os.path.join(
                    sequence_dir,
                    f"pose_{int(pose['pose_index']):03d}",
                    "pose.json",
                ),
                {
                    "schema": "core-sim.calibration_board.render_pose.v1",
                    "calibration_board_plan_sha256": loaded_plan["sha256"],
                    "pose": pose,
                },
            )
        if cfg.METADATA_ONLY:
            run.complete(status="METADATA_ONLY")
            print("[Calibration] metadata and imported pose truth validated")
            return

        cfg.CALIB_BOARD_POSE_MATRIX = poses[0]["matrix_world"]
        cameras = build_scene(cfg)
        setup_render(cfg)
        validate_blender_camera_intrinsics(cameras, cfg)
        variant.after_scene_built(cfg, cameras, sequence_dir)
        board_root = _find_board_root()
        projectors = _find_projectors()
        if not projectors:
            raise RuntimeError("Projector objects not found")
        projector_states = _configure_white_projectors(projectors, white_image)
        save_blender_parameters(cfg, sequence_dir)
        _write_json(
            os.path.join(sequence_dir, "projector_state.json"),
            {
                "texture_mode": "custom_white_image",
                "white_projector_value": int(cfg.CALIB_WHITE_PROJECTOR_VALUE),
                "pixel_grid_enabled": False,
                "projectors": projector_states,
            },
        )

        if cfg.NO_RENDER:
            variant.prepare_no_render([white_image], projectors, cfg)
            projector_states = _configure_white_projectors(projectors, white_image)
            save_blender_parameters(cfg, sequence_dir)
            _write_json(
                os.path.join(sequence_dir, "projector_state.json"),
                {
                    "texture_mode": "custom_white_image",
                    "white_projector_value": int(cfg.CALIB_WHITE_PROJECTOR_VALUE),
                    "pixel_grid_enabled": False,
                    "projectors": projector_states,
                },
            )
            run.complete(
                camera_names=[camera.name for camera in cameras],
                status="VALIDATED",
            )
            print("[Calibration] validation completed; board scene built without capture")
            return

        variant_captures = []
        for pose in poses:
            pose_index = int(pose["pose_index"])
            board_root.matrix_world = Matrix(pose["matrix_world"])
            bpy.context.view_layer.update()
            for projector in projectors:
                set_projector_energy(projector, cfg.PROJECTOR_ENERGY)
            render_cameras(
                cameras,
                cfg,
                output_dir=os.path.join(sequence_dir, f"pose_{pose_index:03d}"),
                image_format=getattr(cfg, "PROJECTOR_CAPTURE_IMAGE_FORMAT", "BMP"),
            )
            variant_captures.extend(
                variant.render_calibration_pose(
                    projectors,
                    cameras,
                    cfg,
                    sequence_dir,
                    os.path.join(sequence_dir, f"pose_{pose_index:03d}"),
                    pose_index,
                )
            )
        if variant_captures:
            _write_json(
                os.path.join(sequence_dir, "variant_capture_plan.json"),
                {
                    "variant": variant.name,
                    "captures": variant_captures,
                },
            )
        run.complete(
            camera_names=[camera.name for camera in cameras],
            expected_minimum=variant.expected_checkerboard_camera_file_count(
                len(poses), cameras, cfg
            ),
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
