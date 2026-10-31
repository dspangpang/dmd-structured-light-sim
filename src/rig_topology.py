from rig_geometry import (
    camera_devices,
    camera_local_position,
    camera_local_rotation_matrix,
    projector_local_rotation_matrix,
)


def normalized_camera_topology(cfg):
    normalize = getattr(cfg, "normalized_camera_topology", None)
    if callable(normalize):
        return normalize()

    topology = str(getattr(cfg, "CAMERA_TOPOLOGY", "dual") or "dual").strip().lower()
    aliases = {
        "stereo": "dual",
        "dual-camera": "dual",
        "dual_camera": "dual",
        "single-camera": "single",
        "single_camera": "single",
    }
    topology = aliases.get(topology, topology)
    if topology not in {"dual", "single"}:
        raise ValueError(f"Unsupported CAMERA_TOPOLOGY: {topology}")
    return topology


def camera_specs(cfg, rig_idx=0, multi_rig=False):
    model_rotation = (
        cfg.CAMERA_MODEL_ROT_X_DEG,
        cfg.CAMERA_MODEL_ROT_Y_DEG,
        cfg.CAMERA_MODEL_ROT_Z_DEG,
    )
    specs = []
    for device in camera_devices(cfg):
        base_name = str(device["name"])
        name = f"rig{rig_idx:02d}_{base_name}" if multi_rig else base_name
        specs.append(
            {
                "name": name,
                "role": str(device["role"]),
                "source_key": str(device["source_key"]),
                "local_position": tuple(camera_local_position(cfg, device)),
                "local_rotation": tuple(camera_local_rotation_matrix(cfg, device)),
                "intrinsic_matrix_px": device["intrinsic_matrix_px"],
                "projection_matrix_px": device["projection_matrix_px_3x4"],
                "model_rotation": model_rotation,
            }
        )
    return specs


def projector_name(rig_idx=0, multi_rig=False):
    return f"rig{rig_idx:02d}_projector" if multi_rig else "Projector"


def projector_local_position(_cfg):
    return (0.0, 0.0, 0.0)
