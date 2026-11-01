"""OpenCV calibration export. No Blender runtime or text parsing is required."""
import json
from pathlib import Path
import numpy as np
from calc_params import get_camera_parameters
from pixel_coordinates import PIXEL_COORDINATE_CONTRACT

from calibration_io import (
    SCHEMA as CALIBRATION_SCHEMA, FILENAME as CALIBRATION_FILENAME,
)


def opencv_matrix(value):
    array = np.asarray(value, dtype=float)
    if array.ndim != 2 or not np.all(np.isfinite(array)):
        raise ValueError("Calibration matrices must be finite and two dimensional")
    return {"type_id": "opencv-matrix", "rows": array.shape[0],
            "cols": array.shape[1], "dt": "d", "data": array.ravel().tolist()}


def calibration_structure(cfg, projector_resolution=None, *, parameters=None):
    params = get_camera_parameters(cfg, projector_resolution) if parameters is None else parameters
    devices = {}
    for name, data in params.items():
        projector = name == "Projector"
        size = projector_resolution if projector else (cfg.RES_X, cfg.RES_Y)
        rt = np.column_stack((data["R"], data["t"]))
        devices[name] = {"kind": "projector" if projector else "camera",
                         "image_size": list(size), "K": opencv_matrix(data["K"]),
                         "distortion": opencv_matrix(np.zeros((1, 5))),
                         "world_to_device": opencv_matrix(rt),
                         "P": opencv_matrix(data["P"])}
    cameras = [name for name in devices if devices[name]["kind"] == "camera"]
    if not cameras:
        raise ValueError("Calibration requires at least one camera")
    reference = cameras[0]
    pairs = {}
    for source in cameras:
        pairs[source] = {}
        for target in devices:
            if target == source:
                continue
            a, b = params[source], params[target]
            rotation = b["R"] @ a["R"].T
            translation = b["t"] - rotation @ a["t"]
            transform = np.eye(4)
            transform[:3, :3], transform[:3, 3] = rotation, translation
            pairs[source][target] = opencv_matrix(transform)
    result = {"schema": CALIBRATION_SCHEMA, "pixel_coordinate_contract": PIXEL_COORDINATE_CONTRACT,
              "translation_unit": "meter", "device_axes": "x_right_y_down_z_forward",
              "calibration_result_source": "blender_ground_truth",
              "projector_coordinate_domain": "blender_projector_raster",
              "scene_config_sha256": cfg.APP_SCENE_CONFIG_SHA256,
              "reference_camera": reference, "devices": devices,
              "device_transforms": pairs}
    return result


def save_calibration(cfg, output_dir, projector_resolution=None, *, parameters=None):
    result = calibration_structure(cfg, projector_resolution, parameters=parameters)
    path = Path(output_dir) / CALIBRATION_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return result
