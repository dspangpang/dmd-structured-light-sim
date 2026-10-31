"""Load an experiment-owned calibration-board plan for Blender rendering."""

import hashlib
import json
import math
import os
import shutil
import warnings
from pathlib import Path


PLAN_SCHEMAS = {
    "gwcam_calibration_circle_grid_plan",
    "gwcam_calibration_board_plan",
}
APP_SCENE_SCHEMA = "gwcam_structured_light_scene"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Calibration-board plan root must be an object: {path}")
    return value


def _resolve_declared_path(value, base_dir):
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = Path(base_dir) / path
    return path.resolve()


def _positive_number(value, label):
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be positive, got {number}")
    return number


def _nonnegative_number(value, label):
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{label} must be non-negative, got {number}")
    return number


def _matrix4(value, label):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{label} must be a 4x4 matrix")
    matrix = []
    for row in value:
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError(f"{label} must be a 4x4 matrix")
        numeric_row = [float(item) for item in row]
        if not all(math.isfinite(item) for item in numeric_row):
            raise ValueError(f"{label} must contain only finite values")
        matrix.append(numeric_row)
    for index, expected in enumerate((0.0, 0.0, 0.0, 1.0)):
        if not math.isclose(matrix[3][index], expected, rel_tol=0.0, abs_tol=1.0e-9):
            raise ValueError(f"{label} has an invalid homogeneous row")
    rotation = [row[:3] for row in matrix[:3]]
    for row in range(3):
        for column in range(3):
            dot = sum(rotation[index][row] * rotation[index][column] for index in range(3))
            expected = 1.0 if row == column else 0.0
            if not math.isclose(dot, expected, rel_tol=0.0, abs_tol=1.0e-6):
                raise ValueError(f"{label} contains scale, shear, or a non-orthogonal rotation")
    determinant = (
        rotation[0][0] * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
        - rotation[0][1] * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
        + rotation[0][2] * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0])
    )
    if not math.isclose(determinant, 1.0, rel_tol=0.0, abs_tol=1.0e-6):
        raise ValueError(f"{label} must contain a proper rotation")
    return matrix


def _validated_board(board):
    if not isinstance(board, dict):
        raise ValueError("Calibration-board plan must contain a board object")
    board_type = str(board.get("type", ""))
    if board_type == "checkerboard":
        inner_cols = int(board.get("inner_cols", 0))
        inner_rows = int(board.get("inner_rows", 0))
        if inner_cols < 2 or inner_rows < 2:
            raise ValueError("Checkerboard inner corners must be at least 2x2")
        square_size = _positive_number(
            board.get("square_size_mm"), "board.square_size_mm"
        )
        margin = _nonnegative_number(board.get("margin_mm", 0.0), "board.margin_mm")
        thickness = _positive_number(board.get("thickness_mm"), "board.thickness_mm")
        width = _positive_number(board.get("width_mm"), "board.width_mm")
        height = _positive_number(board.get("height_mm"), "board.height_mm")
        expected_width = (inner_cols + 1) * square_size + 2.0 * margin
        expected_height = (inner_rows + 1) * square_size + 2.0 * margin
        if not math.isclose(width, expected_width, rel_tol=1.0e-12, abs_tol=1.0e-9):
            raise ValueError("board.width_mm disagrees with checkerboard geometry")
        if not math.isclose(height, expected_height, rel_tol=1.0e-12, abs_tol=1.0e-9):
            raise ValueError("board.height_mm disagrees with checkerboard geometry")
        return {
            "type": "checkerboard",
            "inner_cols": inner_cols,
            "inner_rows": inner_rows,
            "square_size_m": square_size * 1.0e-3,
            "margin_m": margin * 1.0e-3,
            "width_m": width * 1.0e-3,
            "height_m": height * 1.0e-3,
            "thickness_m": thickness * 1.0e-3,
        }
    if board_type != "symmetric_circles_grid":
        raise ValueError(
            "Calibration-board plan board.type must be symmetric_circles_grid "
            "or checkerboard"
        )
    cols = int(board.get("cols", 0))
    rows = int(board.get("rows", 0))
    if cols < 2 or rows < 2:
        raise ValueError("Circle-grid board rows and cols must both be at least 2")
    spacing = _positive_number(board.get("spacing_mm"), "board.spacing_mm")
    radius = _positive_number(board.get("dot_radius_mm"), "board.dot_radius_mm")
    margin = _positive_number(board.get("margin_mm"), "board.margin_mm")
    width = _positive_number(board.get("width_mm"), "board.width_mm")
    height = _positive_number(board.get("height_mm"), "board.height_mm")
    thickness = _positive_number(board.get("thickness_mm"), "board.thickness_mm")
    if radius >= 0.5 * spacing:
        raise ValueError("Calibration-board dots overlap at the declared spacing")
    if margin < radius:
        raise ValueError("Calibration-board margin must cover the full dot radius")

    quantization = board.get("spacing_quantization_mm")
    if quantization is not None:
        quantum = _positive_number(quantization, "board.spacing_quantization_mm")
        if not math.isclose(
            spacing / quantum,
            round(spacing / quantum),
            rel_tol=0.0,
            abs_tol=1.0e-9,
        ):
            raise ValueError(
                f"board.spacing_mm must be quantized in {quantum:g} mm increments"
            )

    expected_width = (cols - 1) * spacing + 2.0 * margin
    expected_height = (rows - 1) * spacing + 2.0 * margin
    if not math.isclose(width, expected_width, rel_tol=1.0e-12, abs_tol=1.0e-9):
        raise ValueError("board.width_mm disagrees with its spacing and margin")
    if not math.isclose(height, expected_height, rel_tol=1.0e-12, abs_tol=1.0e-9):
        raise ValueError("board.height_mm disagrees with its spacing and margin")

    centers = board.get("circle_centers_local_mm")
    if not isinstance(centers, list) or len(centers) != cols * rows:
        raise ValueError(
            "Calibration-board plan must contain rows*cols explicit circle centers"
        )
    x0 = -0.5 * (cols - 1) * spacing
    y0 = -0.5 * (rows - 1) * spacing
    normalized_centers = []
    for index, center in enumerate(centers):
        if not isinstance(center, list) or len(center) != 3:
            raise ValueError("Each calibration-board circle center must contain XYZ")
        actual = [float(value) for value in center]
        if not all(math.isfinite(value) for value in actual):
            raise ValueError("Calibration-board circle centers must be finite")
        row, column = divmod(index, cols)
        expected = (x0 + column * spacing, y0 + row * spacing, 0.0)
        if any(
            not math.isclose(a, b, rel_tol=1.0e-12, abs_tol=1.0e-9)
            for a, b in zip(actual, expected)
        ):
            raise ValueError(
                f"Calibration-board circle center {index} disagrees with board.spacing_mm"
            )
        normalized_centers.append([value * 1.0e-3 for value in actual])
    return {
        "type": "symmetric_circles_grid",
        "cols": cols,
        "rows": rows,
        "spacing_m": spacing * 1.0e-3,
        "dot_radius_m": radius * 1.0e-3,
        "margin_m": margin * 1.0e-3,
        "width_m": width * 1.0e-3,
        "height_m": height * 1.0e-3,
        "thickness_m": thickness * 1.0e-3,
        "circle_centers_local_m": normalized_centers,
    }


def _matrix_mm_to_blender_m(matrix_mm):
    matrix = [row[:] for row in matrix_mm]
    for row in range(3):
        matrix[row][3] *= 1.0e-3
    return matrix


def load_calibration_board_plan(path, cfg, projector_resolution):
    plan_path = Path(path).expanduser().resolve()
    if not plan_path.is_file():
        raise FileNotFoundError(f"Calibration-board plan not found: {plan_path}")
    plan = _read_json(plan_path)
    if plan.get("schema") not in PLAN_SCHEMAS:
        raise ValueError(
            f"Unsupported calibration-board plan schema {plan.get('schema')!r}; "
            f"expected one of {sorted(PLAN_SCHEMAS)!r}"
        )

    scene_contract = plan.get("scene_contract")
    if not isinstance(scene_contract, dict):
        raise ValueError("Calibration-board plan has no scene_contract")
    if scene_contract.get("schema") != APP_SCENE_SCHEMA:
        raise ValueError(f"Calibration-board plan must reference {APP_SCENE_SCHEMA}")
    scene_path = Path(str(getattr(cfg, "APP_SCENE_CONFIG_PATH", ""))).resolve()
    declared_scene = _resolve_declared_path(scene_contract.get("path", ""), plan_path.parent)
    if declared_scene != scene_path:
        warnings.warn(
            "Calibration-board plan scene path differs from the current location; "
            "using the current App scene while retaining the plan's explicit geometry: "
            f"plan={declared_scene}, Blender={scene_path}",
            RuntimeWarning,
            stacklevel=2,
        )

    common_view_contract = plan.get("common_view_contract")
    if isinstance(common_view_contract, dict):
        if common_view_contract.get("source") != "derived.preview.common_view_region":
            raise ValueError("Calibration-board plan has an unsupported common-view source")
        declared_resolution = tuple(
            int(value) for value in common_view_contract.get("projector_size_px", ())
        )
        if declared_resolution != tuple(int(value) for value in projector_resolution):
            raise ValueError(
                "Calibration-board plan/projector image resolution mismatch: "
                f"plan={declared_resolution}, image={tuple(projector_resolution)}"
            )
        if str(common_view_contract.get("definition", "")) != str(
            getattr(cfg, "APP_COMMON_VIEW_DEFINITION", "")
        ):
            raise ValueError(
                "Calibration-board plan common-view definition differs from the App scene"
            )
        if not math.isclose(
            float(common_view_contract.get("area_mm2", -1.0)),
            float(getattr(cfg, "APP_COMMON_VIEW_AREA_MM2", -2.0)),
            rel_tol=1.0e-9,
            abs_tol=1.0e-6,
        ):
            raise ValueError(
                "Calibration-board plan common-view area differs from the App scene"
            )
    else:
        # Backward compatibility for plans generated before common_view_contract
        # became the authoritative valid-region contract.
        mask_contract = plan.get("mask_contract")
        if not isinstance(mask_contract, dict):
            raise ValueError(
                "Calibration-board plan has neither common_view_contract nor mask_contract"
            )
        declared_resolution = tuple(
            int(value) for value in mask_contract.get("projector_size_px", ())
        )
        if declared_resolution != tuple(int(value) for value in projector_resolution):
            raise ValueError(
                "Calibration-board plan/projector image resolution mismatch: "
                f"plan={declared_resolution}, image={tuple(projector_resolution)}"
            )
        mask_source = Path(str(mask_contract.get("source", ""))).expanduser().resolve()
        if not mask_source.is_file():
            raise FileNotFoundError(
                f"Calibration-board plan mask source not found: {mask_source}"
            )
        if mask_contract.get("sha256") != _sha256(mask_source):
            raise ValueError(
                "Calibration-board plan is stale after its mask source changed; regenerate it"
            )
        if int(mask_contract.get("threshold", -1)) != int(
            getattr(cfg, "APP_MASK_THRESHOLD", -2)
        ):
            raise ValueError("Calibration-board plan mask threshold differs from the App scene")
        if int(mask_contract.get("active_pixel_count", -1)) != int(
            getattr(cfg, "APP_MASK_ACTIVE_PIXEL_COUNT", -2)
        ):
            raise ValueError(
                "Calibration-board plan active-pixel count differs from the App scene"
            )
        if str(mask_contract.get("active_region_sha256", "")) != str(
            getattr(cfg, "APP_MASK_ACTIVE_REGION_SHA256", "")
        ):
            raise ValueError("Calibration-board plan active region differs from the App scene")

    common_view = plan.get("common_view")
    if not isinstance(common_view, dict) or not isinstance(common_view.get("region"), dict):
        raise ValueError("Calibration-board plan has no current common-view region")

    normalized_board = _validated_board(plan.get("board"))

    poses = plan.get("poses")
    pose_count = int(plan.get("pose_count", 0))
    if not isinstance(poses, list) or pose_count <= 0 or len(poses) != pose_count:
        raise ValueError("Calibration-board plan pose count is invalid")
    normalized_poses = []
    for expected_index, pose in enumerate(poses):
        if not isinstance(pose, dict) or int(pose.get("pose_index", -1)) != expected_index:
            raise ValueError("Calibration-board poses must use consecutive pose_index values")
        matrix_mm = _matrix4(
            pose.get("board_to_world_mm_4x4"),
            f"poses[{expected_index}].board_to_world_mm_4x4",
        )
        center = pose.get("center_world_mm")
        if not isinstance(center, list) or len(center) != 3:
            raise ValueError(
                f"poses[{expected_index}].center_world_mm must contain XYZ"
            )
        center_mm = [float(value) for value in center]
        if any(
            not math.isfinite(value)
            or not math.isclose(value, matrix_mm[axis][3], rel_tol=0.0, abs_tol=1.0e-6)
            for axis, value in enumerate(center_mm)
        ):
            raise ValueError(
                f"poses[{expected_index}] center disagrees with its board-to-world matrix"
            )
        record = dict(pose)
        record["matrix_world"] = _matrix_mm_to_blender_m(matrix_mm)
        normalized_poses.append(record)

    cfg.CALIBRATION_BOARD_PLAN_PATH = str(plan_path)
    cfg.CALIBRATION_BOARD_PLAN_SHA256 = _sha256(plan_path)
    cfg.CALIBRATION_BOARD_DEFINITION = normalized_board
    return {
        "path": str(plan_path),
        "sha256": cfg.CALIBRATION_BOARD_PLAN_SHA256,
        "schema": str(plan["schema"]),
        "raw": plan,
        "board": normalized_board,
        "poses": normalized_poses,
    }


def snapshot_calibration_board_plan(loaded_plan, output_dir):
    source = Path(loaded_plan["path"]).resolve()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "calibration_board.plan.json"
    manifest_path = output / "calibration_board.plan.manifest.json"
    digest = str(loaded_plan["sha256"])
    if manifest_path.is_file():
        existing = _read_json(manifest_path)
        if str(existing.get("sha256", "")) != digest:
            raise ValueError(
                "Output directory already belongs to a different calibration-board plan: "
                f"{output}"
            )
    shutil.copy2(source, snapshot)
    manifest = {
        "schema": str(loaded_plan["schema"]),
        "sha256": digest,
        "source": str(source),
        "snapshot": snapshot.name,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[CalibrationPlan] Snapshotted board plan: {snapshot}")
    return snapshot
