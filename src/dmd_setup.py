"""Standalone DMD scene preparation; Blender only consumes the resulting files."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from dmd_diamond import (
    build_carrier_mapping_contract, generate_diamond_blender_texture,
)

SCHEMA = "dmd-sim.scene.v1"


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown {label} fields: {', '.join(sorted(unknown))}")


def _positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return float(value)


def _size(value, label):
    if not isinstance(value, list) or len(value) != 2 or any(
        isinstance(x, bool) or not isinstance(x, int) or x < 2 for x in value
    ):
        raise ValueError(f"{label} must contain two integers >= 2")
    return tuple(value)


def _vector(value, label):
    if not isinstance(value, list) or len(value) != 3 or any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value
    ):
        raise ValueError(f"{label} must contain three finite numbers")
    return np.array(value, dtype=float)


def _basis(device, roll_deg=0.0):
    center = _vector(device["position_m"], "position_m")
    forward = _vector(device["look_at_m"], "look_at_m") - center
    up = _vector(device.get("up", [0, 1, 0]), "up")
    if np.linalg.norm(forward) < 1e-12:
        raise ValueError("position_m and look_at_m must differ")
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    if np.linalg.norm(right) < 1e-12:
        raise ValueError("up must not be parallel to the viewing direction")
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    angle = math.radians(roll_deg)
    c, s = math.cos(angle), math.sin(angle)
    rotation = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    return center, np.column_stack((right, down, forward)) @ rotation


def _device(device, size, role, roll_deg=0.0):
    width, height = size
    pitch = _positive(device["pixel_pitch_um"], "pixel_pitch_um")
    focal = _positive(device["focal_length_mm"], "focal_length_mm")
    f = focal / (pitch * 1e-3)
    k = np.array([[f, 0, (width - 1) / 2], [0, f, (height - 1) / 2], [0, 0, 1]])
    center, basis = _basis(device, roll_deg)
    rt = np.column_stack((basis.T, -basis.T @ (center * 1000)))
    record = {"role": role, "intrinsic_matrix_px": k.tolist(), "geometry": {
        "valid": True, "projection_definition": "homogeneous_world_mm_to_top_left_pixel",
        "center_world_mm": (center * 1000).tolist(), "device_to_world_3x3": basis.tolist(),
        "projection_matrix_px_3x4": (k @ rt).tolist(),
    }}
    parameters = {"model": "pinhole", "width_px": width, "height_px": height,
                  "pixel_pitch_x_um": pitch, "pixel_pitch_y_um": pitch}
    if role == "projector":
        parameters["throw_ratio"] = f / width
    else:
        parameters["focal_length_mm"] = focal
    return parameters, record


def validate_config(raw):
    _keys(raw, {"schema", "camera", "projector", "patterns", "target", "render", "capture", "visualization"}, "scene")
    if raw.get("schema") != SCHEMA:
        raise ValueError(f"Expected schema {SCHEMA}")
    pose_keys = {"position_m", "look_at_m", "up", "pixel_pitch_um", "focal_length_mm"}
    _keys(raw["camera"], pose_keys | {"resolution"}, "camera")
    _keys(raw["projector"], pose_keys | {"type", "resolution"}, "projector")
    _size(raw["camera"]["resolution"], "camera.resolution")
    projector = raw["projector"]
    kind = projector["type"]
    if kind not in {"orthogonal", "diamond"}:
        raise ValueError("projector.type must be orthogonal or diamond")
    _size(projector["resolution"], "projector.resolution")
    for device in (raw["camera"], projector):
        _positive(device["pixel_pitch_um"], "pixel_pitch_um")
        _positive(device["focal_length_mm"], "focal_length_mm")
        _basis(device)
    pattern = raw["patterns"]
    _keys(pattern, {"kind", "period_px", "phase_steps", "axis", "files"}, "patterns")
    if pattern["kind"] == "sinusoidal":
        if "files" in pattern:
            raise ValueError("sinusoidal patterns do not accept files")
        _positive(pattern["period_px"], "patterns.period_px")
        steps = pattern["phase_steps"]
        if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 64:
            raise ValueError("phase_steps must be an integer from 1 to 64")
        if pattern["axis"] not in {"x", "y"}:
            raise ValueError("patterns.axis must be x or y")
    elif pattern["kind"] == "files":
        if set(pattern) != {"kind", "files"} or not isinstance(pattern["files"], list) or not pattern["files"]:
            raise ValueError("file patterns require only kind and a nonempty ordered files list")
        if any(not isinstance(x, str) or not x for x in pattern["files"]):
            raise ValueError("pattern file paths must be nonempty strings")
    else:
        raise ValueError("patterns.kind must be sinusoidal or files")
    _keys(raw["target"], {"asset", "primitive", "center_world_m", "rotation_deg", "scale", "material"}, "target")
    # The established render-request parser performs nested render/target validation.
    from render_request import apply_render_request, RENDER_REQUEST_SCHEMA
    from runtime_config import RuntimeConfig
    from variant import VARIANT
    apply_render_request(RuntimeConfig(), {"directory": str(Path.cwd()), "repo_root": str(Path.cwd()),
        "path": "standalone-config-validation", "sha256": "",
        "raw": {"schema": RENDER_REQUEST_SCHEMA, "target": raw["target"],
                "render": raw.get("render", {}), "capture": raw.get("capture", {}),
                "visualization": raw.get("visualization", {})}}, VARIANT)
    if "resolution" in raw.get("render", {}):
        raise ValueError("Set image dimensions in camera.resolution, not render.resolution")
    return raw


def build_scene_contract(raw, mapping):
    camera_size = tuple(raw["camera"]["resolution"])
    projector_size = tuple(mapping["carrier_texture_size_wh"])
    roll = mapping["projector_roll_deg"]
    camera, camera_record = _device(raw["camera"], camera_size, "right_primary")
    projector, projector_record = _device(raw["projector"], projector_size, "projector", roll)
    center, _ = _basis(raw["projector"])
    distance = float(np.linalg.norm(_vector(raw["projector"]["look_at_m"], "look_at_m") - center))
    baseline = float(np.linalg.norm(_vector(raw["camera"]["position_m"], "position_m") - center))
    baseline_vector = _vector(raw["camera"]["position_m"], "position_m") - center
    _, camera_basis = _basis(raw["camera"])
    _, projector_basis = _basis(raw["projector"])
    toe_in = math.degrees(math.acos(float(np.clip(camera_basis[:, 2] @ projector_basis[:, 2], -1, 1))))
    return {"schema": "dmd-sim.render-scene.v1", "authority": SCHEMA,
        "camera": camera, "projector": projector,
        "rig": {"camera_count": 1, "stereo_enabled": False, "work_distance_mm": distance * 1000,
                "baseline_mm": baseline * 1000,
                "baseline_angle_deg": math.degrees(math.atan2(baseline_vector[1], baseline_vector[0])),
                "camera_toe_in_deg": toe_in,
                "projector_roll_deg": roll},
        "target_reference": {"origin_world_mm": [x * 1000 for x in raw["target"].get("center_world_m", [0, 0, 0])]},
        "mask": {"enabled": False, "active_regions_px": [], "active_pixel_count": 0, "threshold": 0},
        "derived": {"camera": camera_record, "cameras": {"right": camera_record},
                    "projector": projector_record,
                    "preview": {"mask_region": {"valid": False, "components": []},
                                "common_view_definition": "not_computed", "common_view_area_mm2": 0,
                                "common_view_region": {"valid": False, "components": []}}},
        "dmd_mapping": mapping}


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    raw = validate_config(json.loads(config_path.read_text(encoding="utf-8")))
    if output.exists():
        raise FileExistsError(f"Output already exists; choose a new directory: {output}")
    kind = raw["projector"]["type"]
    size = tuple(raw["projector"]["resolution"])
    mapping = build_carrier_mapping_contract(logical_size=size) if kind == "diamond" else {
        "schema": "dmd-sim.orthogonal_mapping.v1", "logical_dmd_size_wh": list(size),
        "carrier_texture_size_wh": list(size), "projector_roll_deg": 0.0,
        "native_to_carrier_homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "carrier_to_native_homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
    pattern = raw["patterns"]
    images, sources = [], []
    if pattern["kind"] == "files":
        for name in pattern["files"]:
            source = (config_path.parent / name).resolve()
            with Image.open(source) as image:
                if image.size != size or image.mode not in {"1", "L"}:
                    raise ValueError(f"Expected an 8-bit grayscale logical image of size {size}: {source}")
                images.append(image.convert("L"))
            sources.append({"file": name, "sha256": sha256(source)})
    else:
        yy, xx = np.indices((size[1], size[0]))
        coordinate = xx if pattern["axis"] == "x" else yy
        for step in range(pattern["phase_steps"]):
            phase = 2 * np.pi * (coordinate / pattern["period_px"] + step / pattern["phase_steps"])
            images.append(Image.fromarray(np.rint(127.5 + 100 * np.cos(phase)).astype(np.uint8)))
    scene = build_scene_contract(raw, mapping)
    inputs = output / "inputs"
    (inputs / "logical").mkdir(parents=True)
    (inputs / "projector").mkdir()
    write_json(inputs / "config.json", raw)
    write_json(inputs / "dmd_mapping.json", mapping)
    write_json(inputs / "scene.json", scene)
    from app_scene_config import apply_app_scene_config
    from runtime_config import RuntimeConfig
    apply_app_scene_config(RuntimeConfig(), inputs / "scene.json")
    frames = []
    for index, image in enumerate(images):
        name = f"proj_{index:05d}.bmp"
        image.save(inputs / "logical" / name)
        texture = generate_diamond_blender_texture(image) if kind == "diamond" else image
        texture.save(inputs / "projector" / name)
        frames.append({"logical": "logical/" + name, "projector": "projector/" + name,
                       "logical_sha256": sha256(inputs / "logical" / name),
                       "projector_sha256": sha256(inputs / "projector" / name)})
    target = copy.deepcopy(raw["target"])
    assets = {}
    if "asset" in target:
        asset = (config_path.parent / target["asset"]).resolve()
        if not asset.is_file():
            raise FileNotFoundError(f"Target asset does not exist: {asset}")
        target["asset"] = str(asset)
        assets["target"] = {"sha256": sha256(asset)}
    visualization = copy.deepcopy(raw.get("visualization", {}))
    for key in ("camera_model", "projector_model"):
        model = visualization.get(key)
        if not model:
            continue
        if model.get("enabled", False) and not model.get("path"):
            raise ValueError(f"visualization.{key}.path is required when enabled")
        if "path" in model:
            asset = (config_path.parent / model["path"]).resolve()
            if not asset.is_file():
                raise FileNotFoundError(f"Model asset does not exist: {asset}")
            model["path"] = str(asset)
            assets[key] = {"sha256": sha256(asset)}
    request = {"schema": "core-sim.render_request.v1", "scene_config": "scene.json",
        "projector_images": "projector", "output_dir": str(output / "render"), "target": target,
        "render": raw.get("render", {}), "capture": raw.get("capture", {}),
        "visualization": visualization,
        "variant": {"name": "dmd-structured-light-sim", "settings": {}}}
    write_json(inputs / "render_request.json", request)
    write_json(inputs / "preparation_manifest.json", {"schema": "dmd-sim.preparation.v1",
        "dmd_type": kind, "config_sha256": sha256(config_path), "source_images": sources,
        "frames": frames, "assets": assets, "dmd_mapping_sha256": sha256(inputs / "dmd_mapping.json"),
        "scene_sha256": sha256(inputs / "scene.json")})
    return inputs / "render_request.json"
