import ast
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dmd_diamond import (
    build_carrier_mapping_contract, logical_to_native, native_to_logical,
    map_logical_to_carrier_pixel, generate_diamond_blender_texture,
)
from dmd_setup import prepare, validate_config, build_scene_contract
from app_scene_config import apply_app_scene_config
from runtime_config import RuntimeConfig


class StandaloneTests(unittest.TestCase):
    def config(self, kind="orthogonal"):
        config_path = ROOT / "examples" / kind / "scene.json"
        cfg = json.loads(config_path.read_text())
        cfg["target"]["asset"] = str((config_path.parent / cfg["target"]["asset"]).resolve())
        for model in cfg["visualization"].values():
            model["path"] = str((config_path.parent / model["path"]).resolve())
        return cfg

    def test_all_python_entries_parse(self):
        # Includes Blender-only entries omitted by unittest discovery.
        for directory in ("src", "scripts", "blender", "tests"):
            for path in (ROOT / directory).rglob("*.py"):
                with self.subTest(path=str(path.relative_to(ROOT))):
                    ast.parse(path.read_text(), filename=str(path))

    def test_unknown_type_and_configuration_typos_fail(self):
        cfg = self.config()
        cfg["projector"]["type"] = "diamnod"
        with self.assertRaisesRegex(ValueError, "projector.type"):
            validate_config(cfg)
        cfg = self.config()
        cfg["render"]["sampels"] = 8
        with self.assertRaisesRegex(ValueError, "sampels"):
            validate_config(cfg)

    def test_invalid_resolution_and_camera_basis_fail(self):
        cfg = self.config("diamond")
        for size in ([0, 480], [640, -1], [640.5, 480], [True, 480], [640]):
            cfg["projector"]["resolution"] = size
            with self.assertRaisesRegex(ValueError, "projector.resolution"):
                validate_config(cfg)
        cfg = self.config()
        cfg["camera"]["look_at_m"] = cfg["camera"]["position_m"]
        with self.assertRaisesRegex(ValueError, "must differ"):
            validate_config(cfg)

    def test_diamond_pixel_permutation_and_inverse(self):
        for size in ((1, 1), (1, 2), (2, 1), (2, 2), (7, 5), (8, 6),
                     (640, 480), (641, 481), (912, 1140)):
            for orientation in ("plus45", "minus45"):
                for padding in (0, 2):
                    with self.subTest(size=size, orientation=orientation, padding=padding):
                        self.check_diamond_mapping(size, orientation, padding)

    def check_diamond_mapping(self, size, orientation, padding):
        width, height = size
        yy, xx = np.indices((height, width))
        source = ((xx * 17 + yy * 31) % 256).astype(np.uint8)
        mapping = build_carrier_mapping_contract(orientation, padding, logical_size=size)
        self.assertEqual(mapping["logical_dmd_size_wh"], list(size))
        self.assertEqual(mapping["diamond_native_size_wh"], [2 * width, height])
        h = np.array(mapping["native_to_carrier_homography"])
        hinv = np.array(mapping["carrier_to_native_homography"])
        np.testing.assert_allclose(hinv @ h, np.eye(3), atol=1e-12)
        native_u, native_v = 2 * xx + (yy & 1), yy
        cx = (h[0, 0] * native_u + h[0, 1] * native_v + h[0, 2]).astype(int)
        cy = (h[1, 0] * native_u + h[1, 1] * native_v + h[1, 2]).astype(int)
        texture = np.asarray(generate_diamond_blender_texture(
            Image.fromarray(source), orientation=orientation, padding=padding, background_gray=199))
        self.assertEqual(list(texture.shape[::-1]), mapping["carrier_texture_size_wh"])
        self.assertTrue(((cx >= 0) & (cx < texture.shape[1]) & (cy >= 0) & (cy < texture.shape[0])).all())
        self.assertEqual(len(np.unique(cy * texture.shape[1] + cx)), source.size)
        np.testing.assert_array_equal(texture[cy, cx], source)
        active = np.zeros(texture.shape, dtype=bool)
        active[cy, cx] = True
        self.assertTrue((texture[~active] == 199).all())
        for row, col in ((0, 0), (height - 1, width - 1), (height // 2, width // 2)):
            self.assertEqual(native_to_logical(*logical_to_native(row, col, size), size), (row, col))
            self.assertEqual(map_logical_to_carrier_pixel(row, col, orientation, padding, logical_size=size),
                             (cx[row, col], cy[row, col]))

    def test_diamond_bounds_use_configured_size(self):
        size = (7, 5)
        for row, col in ((-1, 0), (5, 0), (0, 7)):
            with self.assertRaises(ValueError):
                logical_to_native(row, col, size)
        for u, v in ((14, 0), (0, 5), (1, 0), (0, 1)):
            with self.assertRaises(ValueError):
                native_to_logical(u, v, size)
        self.assertEqual(build_carrier_mapping_contract()["carrier_texture_size_wh"], [1481, 1482])

    def test_generated_scene_matrices_project_device_axis_to_principal_point(self):
        for kind in ("orthogonal", "diamond"):
            cfg = self.config(kind)
            mapping = build_carrier_mapping_contract(logical_size=cfg["projector"]["resolution"]) if kind == "diamond" else {
                "carrier_texture_size_wh": cfg["projector"]["resolution"], "projector_roll_deg": 0}
            scene = build_scene_contract(cfg, mapping)
            for name in ("camera", "projector"):
                device = scene["derived"][name]
                geometry = device["geometry"]
                basis = np.array(geometry["device_to_world_3x3"])
                np.testing.assert_allclose(basis.T @ basis, np.eye(3), atol=1e-12)
                center = np.array(geometry["center_world_mm"])
                point = center + basis[:, 2] * 500
                projected = np.array(geometry["projection_matrix_px_3x4"]) @ np.r_[point, 1]
                k = np.array(device["intrinsic_matrix_px"])
                np.testing.assert_allclose(projected[:2] / projected[2], k[:2, 2], atol=1e-9)

    def test_diamond_native_horizontal_direction_survives_carrier_roll(self):
        cfg = self.config("diamond")
        mapping = build_carrier_mapping_contract(logical_size=cfg["projector"]["resolution"])
        scene = build_scene_contract(cfg, mapping)
        device = scene["derived"]["projector"]
        k = np.array(device["intrinsic_matrix_px"])
        basis = np.array(device["geometry"]["device_to_world_3x3"])
        h = np.array(mapping["native_to_carrier_homography"])
        p0 = h @ [500, 500, 1]
        p1 = h @ [502, 500, 1]
        direction = basis @ np.linalg.inv(k) @ (p1 - p0)
        forward = np.array(cfg["projector"]["look_at_m"]) - cfg["projector"]["position_m"]
        expected = np.cross(forward, cfg["projector"].get("up", [0, 1, 0]))
        np.testing.assert_allclose(direction / np.linalg.norm(direction),
                                   expected / np.linalg.norm(expected), atol=1e-12)

    def test_requested_auxiliary_outputs_are_required(self):
        from types import SimpleNamespace
        from variant import DmdSimulationVariant
        variant = DmdSimulationVariant()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            Image.new("L", (8, 6), 23).save(output / "camera_000.png")
            cfg = SimpleNamespace(SAVE_RENDER_FLOAT_EXR=False, SAVE_DEPTH=True, SAVE_POINTCLOUD=False)
            variant.after_scene_built(cfg, [SimpleNamespace(name="camera_000")], output)
            with self.assertRaisesRegex(RuntimeError, "Missing requested"):
                variant.capture_artifact_groups()

    def test_prepare_preserves_explicit_frame_order_and_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg = self.config()
            cfg["projector"]["resolution"] = [8, 6]
            cfg["patterns"] = {"kind": "files", "files": ["second.bmp", "first.bmp"]}
            Image.new("L", (8, 6), 23).save(base / "first.bmp")
            Image.new("L", (8, 6), 147).save(base / "second.bmp")
            config = base / "scene.json"
            config.write_text(json.dumps(cfg))
            request = prepare(config, base / "out")
            with Image.open(request.parent / "projector/proj_00000.bmp") as frame:
                self.assertEqual(frame.getpixel((2, 3)), 147)
            with Image.open(request.parent / "projector/proj_00001.bmp") as frame:
                self.assertEqual(frame.getpixel((2, 3)), 23)
            runtime = RuntimeConfig()
            apply_app_scene_config(runtime, request.parent / "scene.json")
            self.assertEqual(runtime.APP_SCENE_CONFIG_AUTHORITY, "dmd-sim.scene.v1")
            with self.assertRaises(FileExistsError):
                prepare(config, base / "out")

    def test_both_examples_prepare_without_external_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kind in ("orthogonal", "diamond"):
                request = prepare(ROOT / "examples" / kind / "scene.json", Path(tmp) / kind)
                prepared = json.loads(request.read_text())
                self.assertTrue(Path(prepared["target"]["asset"]).is_file())
                for model in prepared["visualization"].values():
                    self.assertTrue(Path(model["path"]).is_file())
                self.assertEqual(len(list((request.parent / "projector").glob("*.bmp"))), 4)

    def test_custom_diamond_preparation_and_ordered_external_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg = self.config("diamond")
            cfg["projector"]["resolution"] = [7, 5]
            for kind in ("sinusoidal", "files"):
                if kind == "files":
                    Image.new("L", (7, 5), 23).save(base / "first.bmp")
                    Image.new("L", (7, 5), 147).save(base / "second.bmp")
                    cfg["patterns"] = {"kind": "files", "files": ["second.bmp", "first.bmp"]}
                config = base / "scene.json"
                config.write_text(json.dumps(cfg))
                request = prepare(config, base / kind)
                mapping = json.loads((request.parent / "dmd_mapping.json").read_text())
                self.assertEqual(mapping["logical_dmd_size_wh"], [7, 5])
                self.assertEqual(mapping["carrier_texture_size_wh"], [9, 9])
                runtime = RuntimeConfig()
                apply_app_scene_config(runtime, request.parent / "scene.json")
                self.assertEqual((runtime.PROJECTOR_RES_X, runtime.PROJECTOR_RES_Y), (9, 9))
                if kind == "files":
                    pixel = map_logical_to_carrier_pixel(3, 2, logical_size=(7, 5))
                    for i, value in enumerate((147, 23)):
                        with Image.open(request.parent / f"projector/proj_{i:05d}.bmp") as image:
                            self.assertEqual(image.getpixel(pixel), value)
            Image.new("L", (8, 5)).save(base / "first.bmp")
            with self.assertRaisesRegex(ValueError, "logical image of size"):
                prepare(config, base / "wrong_size")

    def test_visualization_rejects_typos_and_missing_model(self):
        cfg = self.config()
        cfg["visualization"]["camera_model"]["enabeld"] = True
        with self.assertRaisesRegex(ValueError, "enabeld"):
            validate_config(cfg)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config()
            cfg["visualization"]["camera_model"]["path"] = "missing.obj"
            source = Path(tmp) / "scene.json"
            source.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(FileNotFoundError, "Model asset"):
                prepare(source, Path(tmp) / "out")


if __name__ == "__main__":
    unittest.main()
