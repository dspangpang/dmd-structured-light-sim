"""OpenCV calibration export. No Blender runtime or text parsing is required."""
import json
from pathlib import Path
import numpy as np
from calc_params import get_camera_parameters
from pixel_coordinates import PIXEL_COORDINATE_CONTRACT

from gwcam_calibration_io import (
    SCHEMA as CALIBRATION_SCHEMA, FILENAME as CALIBRATION_FILENAME, read_gwcam_calibration,
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
              "device_transforms": pairs,
              "camera_intrinsic_matrix": devices[reference]["K"],
              "camera_distortion": devices[reference]["distortion"],
              "camera_image_size": opencv_matrix(np.array(devices[reference]["image_size"]).reshape(2, 1))}
    if "Projector" in devices:
        pair = pairs[reference]["Projector"]
        transform = np.array(pair["data"]).reshape(4, 4)
        result.update(dlp_intrinsic_matrix=devices["Projector"]["K"],
                      dlp_distortion=devices["Projector"]["distortion"],
                      dlp_image_size=opencv_matrix(np.array(projector_resolution).reshape(2, 1)),
                      dlp_rotation_matrix=opencv_matrix(transform[:3, :3]),
                      dlp_tanslation_matrix=opencv_matrix(transform[:3, 3:4]),
                      T_cam_2_proj=pair)
    return result


def save_gwcam_calibration(cfg, output_dir, projector_resolution=None, *, parameters=None):
    result = calibration_structure(cfg, projector_resolution, parameters=parameters)
    path = Path(output_dir) / CALIBRATION_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return result


def load_reconstruction_calibrations(path):
    """Load gwCamSDK fields for every explicitly paired camera; JSON only."""
    parsed = read_gwcam_calibration(path)
    devices = parsed["devices"]
    cameras = {n:d for n,d in devices.items() if d["kind"] == "camera"}
    pairing = parsed["camera_projectors"]
    if not pairing:
        raise ValueError("Reconstruction requires a projector paired with each camera")
    result = {}
    for name, camera in cameras.items():
        projector = devices[pairing[name]]
        a = np.asarray(camera["world_to_device"], dtype=float)
        b = np.asarray(projector["world_to_device"], dtype=float)
        transform = np.eye(4)
        transform[:3,:3] = b[:,:3] @ a[:,:3].T
        transform[:3,3] = b[:,3] - transform[:3,:3] @ a[:,3]
        result[name] = {"_camera_world_to_camera_rt": a,
            "camera_intrinsic_matrix": np.asarray(camera["K"], dtype=float),
            "camera_distortion": np.asarray(camera.get("distortion", [[0.] * 5]), dtype=float),
            "camera_image_size": np.array(camera["image_size"],dtype=float).reshape(2,1),
            "dlp_intrinsic_matrix": np.asarray(projector["K"], dtype=float),
            "dlp_distortion": np.asarray(projector.get("distortion", [[0.] * 5]), dtype=float),
            "dlp_image_size": np.array(projector["image_size"],dtype=float).reshape(2,1),
            "dlp_rotation_matrix": transform[:3,:3],
            "dlp_tanslation_matrix": transform[:3,3:4], "T_cam_2_proj": transform}
    return result
