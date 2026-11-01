"""Import and validate the expanded DMD simulation scene contract."""

import json
from pixel_coordinates import camera_lens_shift, projector_lens_shift

import hashlib
import math
import os
import shutil
from pathlib import Path


SCENE_SCHEMA = "dmd-sim.render-scene.v1"


def _canonical_json_sha256(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_region_components(value, label, require_nonempty):
    if not isinstance(value, list) or (require_nonempty and not value):
        raise ValueError(f"{label} must be a non-empty component array")
    for component in value:
        if not isinstance(component, dict):
            raise ValueError(f"{label} components must be objects")
        outer = component.get("outer")
        holes = component.get("holes")
        if not isinstance(outer, list) or len(outer) < 3 or not isinstance(holes, list):
            raise ValueError(f"{label} components require outer and holes")
        for ring in [outer, *holes]:
            if (
                not isinstance(ring, list)
                or len(ring) < 3
                or any(not isinstance(point, list) or len(point) != 2 for point in ring)
            ):
                raise ValueError(f"{label} contains an invalid ring")


def _validate_current_scene(root):
    schema = str(root.get("schema", ""))
    if schema != SCENE_SCHEMA:
        raise ValueError(
            f"Unsupported simulation scene schema {schema!r}; "
            f"expected the current {SCENE_SCHEMA!r} contract"
        )
    mask = root.get("mask")
    preview = root.get("derived", {}).get("preview")
    if not isinstance(mask, dict) or not isinstance(preview, dict):
        raise ValueError("Current scene must contain mask and derived.preview objects")
    enabled = bool(mask.get("enabled", False))
    _validate_region_components(
        mask.get("active_regions_px"), "mask.active_regions_px", enabled
    )
    if not isinstance(mask.get("active_pixel_count"), (int, float)):
        raise ValueError("mask.active_pixel_count is required")
    standalone_preview = (root.get("authority") == "dmd-sim.scene.v1"
                          and preview.get("common_view_definition") == "not_computed")
    for key, required in (("mask_region", enabled), ("common_view_region", not standalone_preview)):
        region = preview.get(key)
        if not isinstance(region, dict) or (
            required and not bool(region.get("valid", False))
        ):
            raise ValueError(f"derived.preview.{key} must be valid")
        _validate_region_components(
            region.get("components"), f"derived.preview.{key}.components", required
        )


def _positive_number(obj, key, context):
    value = float(obj[key])
    if value <= 0.0:
        raise ValueError(f"{context}.{key} must be positive, got {value}")
    return value


def _positive_int(obj, key, context):
    value = int(obj[key])
    if value <= 0:
        raise ValueError(f"{context}.{key} must be positive, got {value}")
    return value


def _intrinsic_matrix(device, label):
    rows = device.get("intrinsic_matrix_px") if isinstance(device, dict) else None
    if rows is None:
        return None
    if not isinstance(rows, list) or len(rows) != 3:
        raise ValueError(f"{label}.intrinsic_matrix_px must be 3x3")
    matrix = tuple(tuple(float(value) for value in row) for row in rows)
    if any(len(row) != 3 for row in matrix) or any(
        not math.isfinite(value) for row in matrix for value in row
    ):
        raise ValueError(f"{label}.intrinsic_matrix_px must be 3x3")
    return matrix


def _validate_intrinsic(matrix, label, width, height):
    if matrix is None:
        raise ValueError(f"{label}.intrinsic_matrix_px is required")
    fx, fy = matrix[0][0], matrix[1][1]
    cx, cy = matrix[0][2], matrix[1][2]
    if fx <= 0.0 or fy <= 0.0:
        raise ValueError(f"{label} focal lengths must be positive")
    if not (0.0 <= cx < float(width) and 0.0 <= cy < float(height)):
        raise ValueError(f"{label} principal point lies outside the image")
    expected_zero = (matrix[0][1], matrix[1][0], matrix[2][0], matrix[2][1])
    if any(not math.isclose(value, 0.0, rel_tol=0.0, abs_tol=1.0e-9) for value in expected_zero):
        raise ValueError(f"{label} must use a zero-skew pinhole intrinsic matrix")
    if not math.isclose(matrix[2][2], 1.0, rel_tol=0.0, abs_tol=1.0e-9):
        raise ValueError(f"{label} intrinsic homogeneous scale must be 1")
    return matrix


def _assert_close(actual, expected, label, *, rel_tol=1.0e-9, abs_tol=1.0e-6):
    if not math.isclose(actual, expected, rel_tol=rel_tol, abs_tol=abs_tol):
        raise ValueError(
            f"{label} disagrees with the App device parameters: "
            f"actual={actual:.12g}, expected={expected:.12g}"
        )


def _validate_rotation_matrix(matrix, label):
    for row in range(3):
        for column in range(3):
            dot = sum(matrix[index][row] * matrix[index][column] for index in range(3))
            expected = 1.0 if row == column else 0.0
            if not math.isclose(dot, expected, rel_tol=0.0, abs_tol=1.0e-6):
                raise ValueError(f"{label} must be an orthonormal rotation")
    determinant = (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )
    if not math.isclose(abs(determinant), 1.0, rel_tol=0.0, abs_tol=1.0e-6):
        raise ValueError(
            f"{label} must be an orthonormal device basis with |determinant|=1"
        )


def _geometry_matrix(device, key, rows, columns, label):
    geometry = device.get("geometry") if isinstance(device, dict) else None
    if not isinstance(geometry, dict) or not bool(geometry.get("valid", False)):
        raise ValueError(f"{label}.geometry must be valid in the App scene")
    if geometry.get("projection_definition") != "homogeneous_world_mm_to_top_left_pixel":
        raise ValueError(f"{label}.geometry has an unsupported projection definition")
    values = geometry.get(key)
    if not isinstance(values, list) or len(values) != rows:
        raise ValueError(f"{label}.geometry.{key} must be {rows}x{columns}")
    matrix = tuple(tuple(float(value) for value in row) for row in values)
    if any(len(row) != columns for row in matrix) or any(
        not math.isfinite(value) for row in matrix for value in row
    ):
        raise ValueError(f"{label}.geometry.{key} must be {rows}x{columns}")
    if key == "device_to_world_3x3":
        _validate_rotation_matrix(matrix, f"{label}.geometry.{key}")
    return matrix


def _validate_renderer_contract(
    camera,
    projector,
    camera_intrinsic,
    projector_intrinsic,
    camera_devices,
):
    """Reject App contracts the current Blender adapters cannot render exactly."""
    if str(camera.get("model", "")) != "pinhole":
        raise ValueError("camera.model must be 'pinhole'")
    if str(projector.get("model", "")) != "pinhole":
        raise ValueError("projector.model must be 'pinhole'")

    camera_width = int(camera["width_px"])
    camera_height = int(camera["height_px"])
    projector_width = int(projector["width_px"])
    projector_height = int(projector["height_px"])
    camera_intrinsic = _validate_intrinsic(
        camera_intrinsic, "derived.camera", camera_width, camera_height
    )
    projector_intrinsic = _validate_intrinsic(
        projector_intrinsic, "derived.projector", projector_width, projector_height
    )

    camera_pitch_x = float(camera["pixel_pitch_x_um"])
    camera_pitch_y = float(camera["pixel_pitch_y_um"])
    projector_pitch_x = float(projector["pixel_pitch_x_um"])
    projector_pitch_y = float(projector["pixel_pitch_y_um"])
    if not math.isclose(camera_pitch_x, camera_pitch_y, rel_tol=1.0e-9, abs_tol=1.0e-9):
        raise ValueError(
            "The current Blender camera adapter requires square camera pixels; "
            "export equal pixel_pitch_x_um/pixel_pitch_y_um"
        )
    if not math.isclose(projector_pitch_x, projector_pitch_y, rel_tol=1.0e-9, abs_tol=1.0e-9):
        raise ValueError(
            "The vendored Projectors adapter requires square projector pixels; "
            "export equal pixel_pitch_x_um/pixel_pitch_y_um"
        )
    focal_mm = float(camera["focal_length_mm"])
    expected_camera = (
        focal_mm / (camera_pitch_x * 1.0e-3),
        focal_mm / (camera_pitch_y * 1.0e-3),
        (camera_width - 1.0) * 0.5,
        (camera_height - 1.0) * 0.5,
    )
    for actual, expected, name in zip(
        (camera_intrinsic[0][0], camera_intrinsic[1][1], camera_intrinsic[0][2], camera_intrinsic[1][2]),
        expected_camera,
        ("fx", "fy", "cx", "cy"),
    ):
        _assert_close(actual, expected, f"derived.camera K.{name}")

    projector_focal_mm = (
        float(projector["throw_ratio"]) * projector_width * projector_pitch_x * 1.0e-3
    )
    expected_projector = (
        projector_focal_mm / (projector_pitch_x * 1.0e-3),
        projector_focal_mm / (projector_pitch_y * 1.0e-3),
        (projector_width - 1.0) * 0.5,
        (projector_height - 1.0) * 0.5,
    )
    for actual, expected, name in zip(
        (projector_intrinsic[0][0], projector_intrinsic[1][1], projector_intrinsic[0][2], projector_intrinsic[1][2]),
        expected_projector,
        ("fx", "fy", "cx", "cy"),
    ):
        _assert_close(actual, expected, f"derived.projector K.{name}")

    for device in camera_devices:
        intrinsic = _validate_intrinsic(
            device["intrinsic_matrix_px"],
            f"derived.cameras.{device['source_key']}",
            camera_width,
            camera_height,
        )
        for row in range(3):
            for column in range(3):
                _assert_close(
                    intrinsic[row][column],
                    camera_intrinsic[row][column],
                    f"derived.cameras.{device['source_key']} K[{row},{column}]",
                )


def _geometry_center_m(device, label):
    geometry = device.get("geometry") if isinstance(device, dict) else None
    center = geometry.get("center_world_mm") if isinstance(geometry, dict) else None
    if not isinstance(center, list) or len(center) != 3:
        raise ValueError(f"{label}.geometry.center_world_mm must contain XYZ")
    return tuple(float(value) * 1.0e-3 for value in center)


def _camera_devices(root, camera_intrinsics):
    rig = root["rig"]
    derived = root.get("derived", {})
    stereo = bool(rig.get("stereo_enabled", False))
    declared_count = int(rig.get("camera_count", 2 if stereo else 1))
    expected_count = 2 if stereo else 1
    if declared_count != expected_count:
        raise ValueError(
            "rig.camera_count disagrees with rig.stereo_enabled: "
            f"count={declared_count}, stereo_enabled={stereo}"
        )

    cameras = derived.get("cameras")
    if not isinstance(cameras, dict):
        raise ValueError("Current App scene must contain derived.cameras")
    requested = (("left", "left"), ("right", "right")) if stereo else (("right", "right_primary"),)
    records = []
    for index, (key, fallback_role) in enumerate(requested):
        device = cameras.get(key)
        if not isinstance(device, dict) and not stereo and key == "right":
            device = derived.get("camera")
        label = f"derived.cameras.{key}"
        if not isinstance(device, dict):
            raise ValueError(f"Current App scene must contain {label}")
        intrinsic = _intrinsic_matrix(device, label) or camera_intrinsics
        if intrinsic is None:
            raise ValueError(f"{label} has no intrinsic matrix")
        role = str(device.get("role", fallback_role) or fallback_role)
        records.append(
            {
                "name": f"camera_{index:03d}",
                "source_key": key,
                "role": role,
                "intrinsic_matrix_px": intrinsic,
                "center_world_m": _geometry_center_m(device, label),
                "device_to_world_3x3": _geometry_matrix(
                    device, "device_to_world_3x3", 3, 3, label
                ),
                "projection_matrix_px_3x4": _geometry_matrix(
                    device, "projection_matrix_px_3x4", 3, 4, label
                ),
            }
        )
    return tuple(records)


def load_app_scene_config(path):
    config_path = Path(path).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    config_path = config_path.resolve()
    with config_path.open("r", encoding="utf-8") as stream:
        root = json.load(stream)
    if not isinstance(root, dict):
        raise ValueError("simulation scene config root must be a JSON object")
    _validate_current_scene(root)

    for section in ("camera", "projector", "rig"):
        if not isinstance(root.get(section), dict):
            raise ValueError(f"Missing App scene section: {section}")
    return config_path, root


def apply_app_scene_config(cfg, path):
    config_path, root = load_app_scene_config(path)
    camera = root["camera"]
    projector = root["projector"]
    rig = root["rig"]
    board = root.get("board", {})
    target_reference = root.get("target_reference", {})
    mask = root.get("mask", {})
    preview = root.get("derived", {}).get("preview", {})

    camera_width = _positive_int(camera, "width_px", "camera")
    camera_height = _positive_int(camera, "height_px", "camera")
    camera_pitch_x = _positive_number(camera, "pixel_pitch_x_um", "camera")
    camera_pitch_y = _positive_number(camera, "pixel_pitch_y_um", "camera")
    camera_focal = _positive_number(camera, "focal_length_mm", "camera")

    projector_width = _positive_int(projector, "width_px", "projector")
    projector_height = _positive_int(projector, "height_px", "projector")
    projector_pitch_x = _positive_number(
        projector, "pixel_pitch_x_um", "projector"
    )
    projector_pitch_y = _positive_number(
        projector, "pixel_pitch_y_um", "projector"
    )
    projector_throw_ratio = _positive_number(
        projector, "throw_ratio", "projector"
    )

    derived = root.get("derived", {})
    camera_derived = derived.get("camera", {})
    projector_derived = derived.get("projector", {})
    camera_intrinsic = _intrinsic_matrix(camera_derived, "derived.camera")
    projector_intrinsic = _intrinsic_matrix(projector_derived, "derived.projector")
    camera_devices = _camera_devices(root, camera_intrinsic)
    _validate_renderer_contract(
        camera,
        projector,
        camera_intrinsic,
        projector_intrinsic,
        camera_devices,
    )

    cfg.APP_SCENE_CONFIG_PATH = str(config_path)
    cfg.APP_SCENE_CONFIG_SCHEMA = str(root["schema"])
    cfg.APP_SCENE_CONFIG_AUTHORITY = str(root.get("authority", "dmd-structured-light-sim"))
    cfg.APP_SCENE_CONFIG_SHA256 = hashlib.sha256(config_path.read_bytes()).hexdigest()

    cfg.CAMERA_TOPOLOGY = "dual" if bool(rig.get("stereo_enabled", False)) else "single"
    cfg.RES_X = camera_width
    cfg.RES_Y = camera_height
    cfg.CAMERA_PIXEL_PITCH_X_UM = camera_pitch_x
    cfg.CAMERA_PIXEL_PITCH_Y_UM = camera_pitch_y
    cfg.f_mm = camera_focal
    cfg.sensor_w = camera_width * camera_pitch_x * 1.0e-3
    cfg.sensor_h = camera_height * camera_pitch_y * 1.0e-3
    cfg.CAMERA_LENS_SHIFT_X, cfg.CAMERA_LENS_SHIFT_Y = camera_lens_shift(
        camera_intrinsic[0][2], camera_intrinsic[1][2], camera_width, camera_height
    )

    cfg.PROJECTOR_RES_X = projector_width
    cfg.PROJECTOR_RES_Y = projector_height
    cfg.APP_PROJECTOR_RES_X = projector_width
    cfg.APP_PROJECTOR_RES_Y = projector_height
    cfg.PROJECTOR_PIXEL_PITCH_X_UM = projector_pitch_x
    cfg.PROJECTOR_PIXEL_PITCH_Y_UM = projector_pitch_y
    cfg.PROJECTOR_THROW_RATIO = projector_throw_ratio
    cfg.PROJECTOR_LENS_SHIFT_X, cfg.PROJECTOR_LENS_SHIFT_Y = projector_lens_shift(
        projector_intrinsic[0][2], projector_intrinsic[1][2],
        projector_width, projector_height,
    )

    work_distance_mm = _positive_number(rig, "work_distance_mm", "rig")
    baseline_mm = float(rig["baseline_mm"])
    if baseline_mm < 0.0:
        raise ValueError(f"rig.baseline_mm must be non-negative, got {baseline_mm}")
    cfg.APP_RIG_METADATA = {
        "work_distance_m": work_distance_mm * 1.0e-3,
        "camera_projector_baseline_m": baseline_mm * 1.0e-3,
        "stereo_camera_baseline_m": float(
            rig.get("stereo_camera_baseline_mm", 2.0 * baseline_mm if rig.get("stereo_enabled") else 0.0)
        ) * 1.0e-3,
        "baseline_angle_deg": float(rig["baseline_angle_deg"]),
        "camera_toe_in_deg": float(rig["camera_toe_in_deg"]),
        "projector_roll_deg": float(rig["projector_roll_deg"]),
        "stereo_enabled": bool(rig.get("stereo_enabled", False)),
        "camera_count": int(rig.get("camera_count", 1)),
    }

    origin = target_reference.get("origin_world_mm", (0.0, 0.0, 0.0))
    if len(origin) != 3:
        raise ValueError("target_reference.origin_world_mm must contain 3 values")
    cfg.TARGET_CENTER_X = float(origin[0]) * 1.0e-3
    cfg.TARGET_CENTER_Y = float(origin[1]) * 1.0e-3
    cfg.TARGET_CENTER_Z = float(origin[2]) * 1.0e-3
    cfg.APP_BOARD_WIDTH_M = float(board.get("width_mm", 0.0)) * 1.0e-3
    cfg.APP_BOARD_HEIGHT_M = float(board.get("height_mm", 0.0)) * 1.0e-3

    cfg.CAMERA_INTRINSIC_MATRIX_PX = camera_intrinsic
    cfg.PROJECTOR_INTRINSIC_MATRIX_PX = projector_intrinsic
    cfg.APP_CAMERA_DEVICES = camera_devices
    primary = next(
        (record for record in cfg.APP_CAMERA_DEVICES if record["source_key"] == "right"),
        cfg.APP_CAMERA_DEVICES[0],
    )
    cfg.APP_CAMERA_CENTER_WORLD_M = primary["center_world_m"]
    cfg.APP_CAMERA_DEVICE_TO_WORLD_MATRIX = primary["device_to_world_3x3"]
    cfg.APP_CAMERA_PROJECTION_MATRIX_PX = primary["projection_matrix_px_3x4"]
    cfg.APP_PROJECTOR_CENTER_WORLD_M = _geometry_center_m(
        projector_derived, "derived.projector"
    )
    cfg.APP_PROJECTOR_DEVICE_TO_WORLD_MATRIX = _geometry_matrix(
        projector_derived, "device_to_world_3x3", 3, 3, "derived.projector"
    )
    cfg.APP_PROJECTOR_PROJECTION_MATRIX_PX = _geometry_matrix(
        projector_derived, "projection_matrix_px_3x4", 3, 4, "derived.projector"
    )
    cfg.APP_MASK_THRESHOLD = int(mask.get("threshold", 0))
    cfg.APP_MASK_ACTIVE_PIXEL_COUNT = int(mask.get("active_pixel_count", 0))
    cfg.APP_MASK_ACTIVE_REGION_SHA256 = _canonical_json_sha256(
        mask.get("active_regions_px")
    )
    cfg.APP_COMMON_VIEW_DEFINITION = str(preview.get("common_view_definition", ""))
    cfg.APP_COMMON_VIEW_AREA_MM2 = float(preview.get("common_view_area_mm2", 0.0))
    cfg.APP_COMMON_VIEW_REGION_SHA256 = _canonical_json_sha256(
        preview.get("common_view_region")
    )

    # Fixed basis adapter required by the vendored Projectors add-on.
    cfg.PROJECTOR_TEXTURE_MIRROR_X = True
    cfg.PROJECTOR_TEXTURE_MIRROR_Y = True

    from variant_api import get_variant

    get_variant().validate_scene_contract(cfg, root, cfg.APP_CAMERA_DEVICES)
    print(f"[AppScene] Applied authoritative App scene config: {config_path}")
    return root


def snapshot_app_scene_config(cfg, output_dir):
    """Copy the exact authoritative scene JSON beside each Blender result."""
    source_text = str(getattr(cfg, "APP_SCENE_CONFIG_PATH", "") or "")
    if not source_text:
        raise ValueError("Cannot snapshot scene config before App scene injection")
    source = Path(source_text).resolve()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "structured_light_scene.config.json"
    manifest_path = output / "structured_light_scene.manifest.json"
    current_digest = str(cfg.APP_SCENE_CONFIG_SHA256)
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing_digest = str(existing.get("sha256", ""))
        if existing_digest and existing_digest != current_digest:
            raise ValueError(
                "Output directory already belongs to a different App scene: "
                f"existing={existing_digest}, requested={current_digest}, "
                f"output={output}. Remove the old output or choose another --out."
            )
    elif snapshot.is_file():
        existing_digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        if existing_digest != current_digest:
            raise ValueError(
                "Output directory contains an unmanifested scene snapshot with a "
                f"different SHA256: output={output}. Remove it or choose another --out."
            )

    shutil.copy2(source, snapshot)
    manifest = {
        "schema": str(cfg.APP_SCENE_CONFIG_SCHEMA),
        "authority": str(cfg.APP_SCENE_CONFIG_AUTHORITY),
        "sha256": current_digest,
        "source": str(source),
        "snapshot": snapshot.name,
        "camera_devices": [
            {
                "name": str(device["name"]),
                "source_key": str(device["source_key"]),
                "role": str(device["role"]),
            }
            for device in (getattr(cfg, "APP_CAMERA_DEVICES", None) or ())
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[AppScene] Snapshotted scene contract: {snapshot}")
    return snapshot


def scene_config_from_argv(argv):
    path = os.environ.get("DMD_SCENE_CONFIG", "").strip()
    for index, key in enumerate(argv):
        if key in {"--scene-config", "--structured-light-scene-config"}:
            if index + 1 >= len(argv):
                raise ValueError(f"{key} requires a JSON path")
            path = argv[index + 1]
    return path
