"""Validate and serialize the matrices imported from the App scene contract."""

import numpy as np

from config import SimConfig
from rig_geometry import camera_devices


def _require_matrix(value, shape, label):
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != shape or not np.all(np.isfinite(matrix)):
        raise ValueError(f"{label} must be a finite {shape[0]}x{shape[1]} matrix")
    return matrix


def _imported_device_parameters(
    intrinsic_matrix,
    center_world_m,
    device_to_world,
    projection_matrix_px,
    label,
):
    """Convert only units/basis; never infer missing intrinsics or poses."""
    K = _require_matrix(intrinsic_matrix, (3, 3), f"{label} intrinsic")
    center = np.asarray(center_world_m, dtype=float)
    if center.shape != (3,) or not np.all(np.isfinite(center)):
        raise ValueError(f"{label} center_world_m must contain finite XYZ")
    R_device_to_world = _require_matrix(
        device_to_world, (3, 3), f"{label} device_to_world"
    )
    R_world_to_device = R_device_to_world.T
    t_world_to_device = -R_world_to_device @ center
    RT = np.hstack((R_world_to_device, t_world_to_device.reshape(3, 1)))
    calculated_projection = K @ RT

    # The App projection accepts world coordinates in millimetres. Blender and
    # calibration exports use metres, so only the homogeneous translation column changes.
    expected_projection = _require_matrix(
        projection_matrix_px, (3, 4), f"{label} projection"
    ).copy()
    expected_projection[:, 3] *= 1.0e-3
    if not np.allclose(
        calculated_projection,
        expected_projection,
        rtol=1.0e-9,
        atol=1.0e-8,
    ):
        error = float(np.max(np.abs(calculated_projection - expected_projection)))
        raise ValueError(
            f"{label} device pose/intrinsic disagrees with its App projection "
            f"matrix (max abs error={error:.6g})"
        )
    return {
        "K": K,
        "R": R_world_to_device,
        "t": t_world_to_device,
        "P": expected_projection,
        "pos_world": center,
        "rot_world": R_device_to_world,
    }


def _require_projector_resolution(cfg, projector_resolution):
    if projector_resolution is None:
        raise ValueError("Projector image resolution is required")
    actual = tuple(int(value) for value in projector_resolution)
    expected = (int(cfg.APP_PROJECTOR_RES_X), int(cfg.APP_PROJECTOR_RES_Y))
    if len(actual) != 2 or min(actual) <= 0:
        raise ValueError(f"Invalid projector image resolution: {actual}")
    if actual != expected:
        raise ValueError(
            "Projector image resolution does not match the App scene: "
            f"image={actual}, App={expected}"
        )
    return actual


def get_camera_parameters(cfg: SimConfig, projector_resolution=None, include_projector=True):
    results = {}
    for device in camera_devices(cfg):
        results[str(device["name"])] = _imported_device_parameters(
            device["intrinsic_matrix_px"],
            device["center_world_m"],
            device["device_to_world_3x3"],
            device["projection_matrix_px_3x4"],
            f"camera {device['role']}",
        )

    if not include_projector:
        return results
    _require_projector_resolution(cfg, projector_resolution)
    results["Projector"] = _imported_device_parameters(
        cfg.PROJECTOR_INTRINSIC_MATRIX_PX,
        cfg.APP_PROJECTOR_CENTER_WORLD_M,
        cfg.APP_PROJECTOR_DEVICE_TO_WORLD_MATRIX,
        cfg.APP_PROJECTOR_PROJECTION_MATRIX_PX,
        "projector",
    )
    return results
