import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from test_contracts import compatible_scene_contract
from runtime_config import RuntimeConfig
from app_scene_config import apply_app_scene_config
from calibration import save_calibration
from calibration_io import read_calibration, SCHEMA


class CalibrationExportTests(unittest.TestCase):
    def test_full_precision_devices_and_relative_transform(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene.json"
            scene.write_text(json.dumps(compatible_scene_contract()))
            cfg = RuntimeConfig()
            apply_app_scene_config(cfg, scene)
            result = save_calibration(cfg, root, (800, 600))
            loaded = json.loads((root / "calibration.json").read_text())
            self.assertEqual(result, loaded)
            self.assertEqual(loaded["schema"], SCHEMA)
            self.assertEqual(loaded["calibration_result_source"], "blender_ground_truth")
            parsed = read_calibration(root / "calibration.json")
            self.assertEqual(set(parsed["devices"]), set(loaded["devices"]))
            self.assertFalse((root / "params.txt").exists())
            self.assertEqual(loaded["pixel_coordinate_contract"], "opencv_integer_centers_v1")
            for device in cfg.APP_CAMERA_DEVICES:
                np.testing.assert_array_equal(
                    np.array(loaded["devices"][device["name"]]["K"]["data"]).reshape(3,3),
                    device["intrinsic_matrix_px"])
            reference = loaded["reference_camera"]
            for target, value in loaded["device_transforms"][reference].items():
                rt_a = np.array(loaded["devices"][reference]["world_to_device"]["data"]).reshape(3,4)
                rt_b = np.array(loaded["devices"][target]["world_to_device"]["data"]).reshape(3,4)
                a = np.vstack((rt_a,[0,0,0,1])); b = np.vstack((rt_b,[0,0,0,1]))
                np.testing.assert_allclose(np.array(value["data"]).reshape(4,4) @ a, b, atol=1e-12)
