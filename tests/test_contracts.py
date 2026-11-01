import hashlib
import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app_scene_config import apply_app_scene_config
from calibration_board_plan import load_calibration_board_plan
from calc_params import get_camera_parameters
from render_manifest import RenderRun, completed_render_manifest
from render_request import (
    RENDER_REQUEST_SCHEMA,
    apply_render_request,
    load_render_request,
    snapshot_render_request,
)
from runtime_config import RuntimeConfig
from variant_api import get_variant


def intrinsic(width, height, fx, fy=None):
    return [
        [float(fx), 0.0, (width - 1.0) * 0.5],
        [0.0, float(fx if fy is None else fy), (height - 1.0) * 0.5],
        [0.0, 0.0, 1.0],
    ]


def device_record(matrix, role):
    return {
        "role": role,
        "intrinsic_matrix_px": matrix,
        "geometry": {
            "valid": True,
            "projection_definition": "homogeneous_world_mm_to_top_left_pixel",
            "center_world_mm": [0.0, 0.0, 0.0],
            "device_to_world_3x3": [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ],
            "projection_matrix_px_3x4": [row + [0.0] for row in matrix],
        },
    }


def scene_contract(
    camera_size=(640, 480),
    camera_pitch=(5.0, 5.0),
    projector_size=(800, 600),
    stereo=False,
):
    camera_width, camera_height = camera_size
    focal_mm = 10.0
    camera_k = intrinsic(
        camera_width,
        camera_height,
        focal_mm / (camera_pitch[0] * 1.0e-3),
        focal_mm / (camera_pitch[1] * 1.0e-3),
    )
    projector_width, projector_height = projector_size
    projector_k = intrinsic(projector_width, projector_height, 0.5 * projector_width)
    camera_record = device_record(camera_k, "right_primary")
    camera_records = {"right": camera_record}
    if stereo:
        camera_records["left"] = device_record(camera_k, "left")
    projector_record = device_record(projector_k, "projector")
    return {
        "schema": "dmd-sim.render-scene.v1",
        "camera": {
            "model": "pinhole",
            "width_px": camera_width,
            "height_px": camera_height,
            "pixel_pitch_x_um": camera_pitch[0],
            "pixel_pitch_y_um": camera_pitch[1],
            "focal_length_mm": focal_mm,
        },
        "projector": {
            "model": "pinhole",
            "width_px": projector_width,
            "height_px": projector_height,
            "pixel_pitch_x_um": 7.5,
            "pixel_pitch_y_um": 7.5,
            "throw_ratio": 0.5,
        },
        "rig": {
            "stereo_enabled": bool(stereo),
            "camera_count": 2 if stereo else 1,
            "work_distance_mm": 500.0,
            "baseline_mm": 100.0,
            "baseline_angle_deg": 0.0,
            "camera_toe_in_deg": 0.0,
            "projector_roll_deg": 0.0,
        },
        "mask": {
            "enabled": False,
            "active_regions_px": [],
            "active_pixel_count": 0,
            "threshold": 0,
        },
        "target_reference": {"origin_world_mm": [0.0, 0.0, 0.0]},
        "derived": {
            "camera": camera_record,
            "cameras": camera_records,
            "projector": projector_record,
            "preview": {
                "mask_region": {"valid": False, "components": []},
                "common_view_definition": "test",
                "common_view_area_mm2": 1.0,
                "common_view_region": {
                    "valid": True,
                    "components": [
                        {
                            "outer": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
                            "holes": [],
                        }
                    ],
                },
            },
        },
    }


def compatible_scene_contract(**kwargs):
    stereo = get_variant().allowed_camera_counts[0] == 2
    return scene_contract(stereo=stereo, **kwargs)


class AppSceneContractTests(unittest.TestCase):
    def apply(self, root):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "scene.json"
        path.write_text(json.dumps(root), encoding="utf-8")
        cfg = RuntimeConfig()
        apply_app_scene_config(cfg, path)
        return cfg, path

    def test_landscape_contract_and_projection_are_imported(self):
        cfg, _path = self.apply(compatible_scene_contract())
        self.assertAlmostEqual(cfg.CAMERA_LENS_SHIFT_X, 0.0)
        self.assertAlmostEqual(cfg.CAMERA_LENS_SHIFT_Y, 0.0)
        params = get_camera_parameters(cfg, projector_resolution=(800, 600))
        self.assertEqual(len(params), len(cfg.APP_CAMERA_DEVICES) + 1)
        self.assertIn("Projector", params)

    def test_portrait_projector_contract_is_supported(self):
        cfg, _path = self.apply(compatible_scene_contract(projector_size=(1481, 1482)))
        self.assertEqual((cfg.PROJECTOR_RES_X, cfg.PROJECTOR_RES_Y), (1481, 1482))
        self.assertAlmostEqual(cfg.PROJECTOR_LENS_SHIFT_X, 0.0)
        self.assertAlmostEqual(cfg.PROJECTOR_LENS_SHIFT_Y, 0.0)
        params = get_camera_parameters(cfg, projector_resolution=(1481, 1482))
        self.assertIn("Projector", params)

    def test_portrait_shift_uses_sensor_fit_dimension(self):
        cfg, _path = self.apply(compatible_scene_contract(camera_size=(480, 640)))
        self.assertAlmostEqual(cfg.CAMERA_LENS_SHIFT_X, 0.0)
        self.assertAlmostEqual(cfg.CAMERA_LENS_SHIFT_Y, 0.0)

    def test_non_square_camera_pixels_are_rejected_explicitly(self):
        with self.assertRaisesRegex(ValueError, "square camera pixels"):
            self.apply(compatible_scene_contract(camera_pitch=(5.0, 6.0)))

    def test_non_pinhole_model_is_rejected(self):
        root = compatible_scene_contract()
        root["camera"]["model"] = "fisheye"
        with self.assertRaisesRegex(ValueError, "camera.model"):
            self.apply(root)

    def test_non_rotation_device_matrix_is_rejected(self):
        root = compatible_scene_contract()
        root["derived"]["cameras"]["right"]["geometry"]["device_to_world_3x3"][0][0] = 2.0
        with self.assertRaisesRegex(ValueError, "orthonormal"):
            self.apply(root)


class CalibrationPlanTests(unittest.TestCase):
    def test_current_plan_needs_geometry_contracts_but_not_asset_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene_path = root / "moved_scene.json"
            scene_path.write_text("{}", encoding="utf-8")
            cfg = SimpleNamespace(
                APP_SCENE_CONFIG_PATH=str(scene_path),
                APP_COMMON_VIEW_DEFINITION="test",
                APP_COMMON_VIEW_AREA_MM2=1.0,
            )
            plan = {
                "schema": "dmd-sim.calibration-board-plan.v1",
                "scene_contract": {
                    "schema": "dmd-sim.render-scene.v1",
                    "path": "/old/machine/scene.json",
                },
                "common_view_contract": {
                    "source": "derived.preview.common_view_region",
                    "projector_size_px": [800, 600],
                    "definition": "test",
                    "area_mm2": 1.0,
                },
                "common_view": {"region": {}},
                "board": {
                    "type": "checkerboard",
                    "inner_cols": 2,
                    "inner_rows": 2,
                    "square_size_mm": 10.0,
                    "margin_mm": 1.0,
                    "thickness_mm": 1.0,
                    "width_mm": 32.0,
                    "height_mm": 32.0,
                },
                "pose_count": 1,
                "poses": [
                    {
                        "pose_index": 0,
                        "center_world_mm": [0.0, 0.0, 0.0],
                        "board_to_world_mm_4x4": [
                            [1.0, 0.0, 0.0, 0.0],
                            [0.0, 1.0, 0.0, 0.0],
                            [0.0, 0.0, 1.0, 0.0],
                            [0.0, 0.0, 0.0, 1.0],
                        ],
                    }
                ],
            }
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            with warnings.catch_warnings(record=True) as caught:
                loaded = load_calibration_board_plan(plan_path, cfg, (800, 600))
            self.assertEqual(len(loaded["poses"]), 1)
            self.assertTrue(any("scene path differs" in str(item.message) for item in caught))


class RenderRequestTests(unittest.TestCase):
    def write_request(self, root, payload):
        path = root / "request.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_request_applies_non_structural_fields_and_relative_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scene.json").write_text("{}", encoding="utf-8")
            (root / "pattern.bmp").write_bytes(b"BM-test")
            request = self.write_request(
                root,
                {
                    "schema": RENDER_REQUEST_SCHEMA,
                    "scene_config": "scene.json",
                    "projector_images": "pattern.bmp",
                    "output_dir": "output/request-test",
                    "render": {
                        "resolution": [320, 240],
                        "samples": 8,
                        "projector_energy": 3.5,
                    },
                    "capture": {"save_depth": True},
                    "target": {
                        "primitive": {"shape": "box", "size_m": [0.1, 0.2, 0.3]},
                        "rotation_deg": [1.0, 2.0, 3.0],
                        "scale": 0.31,
                    },
                },
            )
            loaded = load_render_request(request, repo_root=REPO_ROOT)
            cfg = RuntimeConfig()
            scene_path, plan_path = apply_render_request(cfg, loaded, get_variant())
            self.assertEqual(scene_path, str((root / "scene.json").resolve()))
            self.assertEqual(plan_path, "")
            self.assertEqual(cfg.PROJECTOR_IMAGE, str((root / "pattern.bmp").resolve()))
            self.assertEqual((cfg.RENDER_RES_X, cfg.RENDER_RES_Y), (320, 240))
            self.assertEqual(cfg.SAMPLES, 8)
            self.assertTrue(cfg.SAVE_DEPTH)
            self.assertEqual(cfg.CUSTOM_TARGET_ROT_Z_DEG, 3.0)
            self.assertEqual(cfg.TARGET_SCALE, 0.31)
            self.assertFalse(cfg.USE_CUSTOM_TARGET)

    def test_request_rejects_unknown_nested_field(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.write_request(
                root,
                {
                    "schema": RENDER_REQUEST_SCHEMA,
                    "render": {"samplez": 8},
                },
            )
            loaded = load_render_request(request, repo_root=REPO_ROOT)
            with self.assertRaisesRegex(ValueError, "Unknown render field"):
                apply_render_request(RuntimeConfig(), loaded, get_variant())

    def test_variant_capture_hook_consumes_branch_field(self):
        class CaptureExtension:
            name = "capture-extension"

            def apply_capture_request(self, cfg, capture, resolve_input_path):
                del resolve_input_path
                if "extension" not in capture:
                    return ()
                cfg.CAPTURE_EXTENSION = capture["extension"]
                return ("extension",)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.write_request(
                root,
                {
                    "schema": RENDER_REQUEST_SCHEMA,
                    "capture": {"extension": {"enabled": True}},
                },
            )
            loaded = load_render_request(request, repo_root=REPO_ROOT)
            cfg = RuntimeConfig()
            apply_render_request(cfg, loaded, CaptureExtension())
            self.assertEqual(cfg.CAPTURE_EXTENSION, {"enabled": True})

    def test_request_rejects_wrong_variant(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.write_request(
                root,
                {
                    "schema": RENDER_REQUEST_SCHEMA,
                    "variant": {"name": "different-branch", "settings": {}},
                },
            )
            loaded = load_render_request(request, repo_root=REPO_ROOT)
            with self.assertRaisesRegex(ValueError, "different-branch"):
                apply_render_request(RuntimeConfig(), loaded, get_variant())

    def test_request_snapshot_preserves_exact_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self.write_request(root, {"schema": RENDER_REQUEST_SCHEMA})
            loaded = load_render_request(request, repo_root=REPO_ROOT)
            cfg = RuntimeConfig()
            apply_render_request(cfg, loaded, get_variant())
            output = root / "output"
            snapshot_render_request(cfg, output)
            self.assertEqual((output / "render_request.json").read_bytes(), request.read_bytes())
            manifest = json.loads((output / "render_request.manifest.json").read_text())
            self.assertEqual(manifest["sha256"], loaded["sha256"])

    def test_branch_default_request_is_valid_when_declared(self):
        variant = get_variant()
        request = variant.default_render_request_path(REPO_ROOT)
        if not request:
            self.skipTest(f"{variant.name} has no branch-default render request")
        loaded = load_render_request(request, repo_root=REPO_ROOT)
        cfg = RuntimeConfig()
        variant.configure_defaults(cfg)
        apply_render_request(cfg, loaded, variant)
        self.assertEqual(cfg.RENDER_REQUEST_SCHEMA, RENDER_REQUEST_SCHEMA)


class RenderManifestTests(unittest.TestCase):
    def config(self, scene_path):
        return SimpleNamespace(
            APP_SCENE_CONFIG_PATH=str(scene_path),
            APP_SCENE_CONFIG_SHA256=hashlib.sha256(scene_path.read_bytes()).hexdigest(),
            APP_SCENE_CONFIG_SCHEMA="dmd-sim.render-scene.v1",
            RENDER_PRESET="default",
            ENGINE="CYCLES",
            SAMPLES=1,
            CAPTURE_MODE="projector_sequence",
            PROJECTOR_CAPTURE_IMAGE_FORMAT="BMP",
            SAVE_DEPTH=False,
            SAVE_POINTCLOUD=False,
        )

    def test_complete_manifest_requires_declared_camera_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene.json"
            image = root / "pattern.bmp"
            scene.write_text("{}", encoding="utf-8")
            image.write_bytes(b"BM-test")
            output = root / "output"
            with RenderRun(
                output,
                self.config(scene),
                repo_root=REPO_ROOT,
                variant_name="core-sim",
                run_type="test",
                projector_images=[image],
            ) as run:
                (output / "camera_000.bmp").write_bytes(b"render")
                # Device selection happens after the manifest starts.
                run.cfg.ACTUAL_CYCLES_DEVICE = {"device": "CPU", "fallback_reason": "no GPU"}
                run.cfg.ACTUAL_COMPOSITOR_DEVICE = {
                    "requested": "GPU",
                    "device": "CPU",
                    "fallback_reason": "headless",
                }
                run.complete(camera_names=["camera_000"], expected_minimum=1)
            self.assertEqual(completed_render_manifest(output), (True, ""))
            manifest = json.loads((output / "render_manifest.json").read_text())
            self.assertEqual(manifest["actual_cycles_device"]["device"], "CPU")
            self.assertEqual(manifest["actual_compositor_device"]["device"], "CPU")

    def test_same_size_camera_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene.json"
            scene.write_text("{}")
            cfg = self.config(scene)
            output = root / "render"
            with RenderRun(output, cfg, repo_root=REPO_ROOT,
                           variant_name=get_variant().name, run_type="test") as run:
                camera = output / "camera_000.bmp"
                camera.write_bytes(b"original-image")
                run.complete(camera_names=["camera_000"], expected_minimum=1)
            self.assertEqual(completed_render_manifest(output), (True, ""))
            camera.write_bytes(b"modified-image")
            valid, reason = completed_render_manifest(output)
            self.assertFalse(valid)
            self.assertIn("hash mismatch", reason)

    def test_legacy_coordinate_manifest_cannot_be_resumed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene.json"
            scene.write_text("{}", encoding="utf-8")
            output = root / "output"
            kwargs = dict(repo_root=REPO_ROOT, variant_name="core-sim", run_type="test")
            with RenderRun(output, self.config(scene), **kwargs) as run:
                (output / "camera_000.bmp").write_bytes(b"render")
                run.complete(camera_names=["camera_000"], expected_minimum=1)
            manifest = output / "render_manifest.json"
            payload = json.loads(manifest.read_text())
            del payload["request"]["pixel_coordinate_contract"]
            # Keep the hash unchanged: checking only a request hash is insufficient.
            manifest.write_text(json.dumps(payload))
            self.assertFalse(completed_render_manifest(output)[0])
            with self.assertRaisesRegex(ValueError, "pixel coordinate"):
                with RenderRun(output, self.config(scene), **kwargs):
                    pass

    def test_exception_marks_manifest_failed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene.json"
            scene.write_text("{}", encoding="utf-8")
            output = root / "output"
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with RenderRun(
                    output,
                    self.config(scene),
                    repo_root=REPO_ROOT,
                    variant_name="core-sim",
                    run_type="test",
                ):
                    raise RuntimeError("boom")
            payload = json.loads((output / "render_manifest.json").read_text())
            self.assertEqual(payload["status"], "FAILED")

    def test_batch_status_file_does_not_make_output_look_legacy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene.json"
            scene.write_text("{}", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            (output / "batch_status.txt").write_text("status=RUNNING\n", encoding="utf-8")
            with RenderRun(
                output,
                self.config(scene),
                repo_root=REPO_ROOT,
                variant_name="core-sim",
                run_type="test",
            ) as run:
                (output / "camera_000.bmp").write_bytes(b"render")
                # Device selection happens after the manifest starts.
                run.cfg.ACTUAL_CYCLES_DEVICE = {"device": "CPU", "fallback_reason": "no GPU"}
                run.complete(camera_names=["camera_000"], expected_minimum=1)
            self.assertEqual(completed_render_manifest(output), (True, ""))
            manifest = json.loads((output / "render_manifest.json").read_text())
            self.assertEqual(manifest["actual_cycles_device"]["device"], "CPU")

    def test_empty_camera_artifact_cannot_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene.json"
            scene.write_text("{}", encoding="utf-8")
            output = root / "output"
            with self.assertRaisesRegex(RuntimeError, "empty camera artifacts"):
                with RenderRun(
                    output,
                    self.config(scene),
                    repo_root=REPO_ROOT,
                    variant_name="core-sim",
                    run_type="test",
                ) as run:
                    (output / "camera_000.bmp").touch()
                    run.complete(camera_names=["camera_000"], expected_minimum=1)

    def test_complete_manifest_tracks_extra_artifact_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene.json"
            image = root / "pattern.bmp"
            scene.write_text("{}", encoding="utf-8")
            image.write_bytes(b"BM-test")
            output = root / "output"
            extra = output / "ground_truth" / "metadata.json"
            with RenderRun(
                output,
                self.config(scene),
                repo_root=REPO_ROOT,
                variant_name="core-sim",
                run_type="test",
                projector_images=[image],
            ) as run:
                (output / "camera_000.bmp").write_bytes(b"render")
                extra.parent.mkdir(parents=True)
                extra.write_text('{"schema":"test"}\n', encoding="utf-8")
                run.complete(
                    camera_names=["camera_000"],
                    expected_minimum=1,
                    extra_artifacts={
                        "ground_truth": [
                            {"path": "ground_truth/metadata.json", "kind": "metadata"}
                        ]
                    },
                )
            payload = json.loads((output / "render_manifest.json").read_text())
            record = payload["artifacts"]["ground_truth"][0]
            self.assertEqual(record["kind"], "metadata")
            self.assertTrue(record["sha256"])
            self.assertEqual(completed_render_manifest(output), (True, ""))
            extra.write_text('{"schema":"xxxx"}\n', encoding="utf-8")
            self.assertEqual(
                completed_render_manifest(output),
                (False, "ground_truth artifact hash mismatch: ground_truth/metadata.json"),
            )

    def test_extra_artifact_path_must_stay_inside_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene.json"
            scene.write_text("{}", encoding="utf-8")
            output = root / "output"
            with self.assertRaisesRegex(ValueError, "output-relative"):
                with RenderRun(
                    output,
                    self.config(scene),
                    repo_root=REPO_ROOT,
                    variant_name="core-sim",
                    run_type="test",
                ) as run:
                    (output / "camera_000.bmp").write_bytes(b"render")
                    run.complete(
                        camera_names=["camera_000"],
                        expected_minimum=1,
                        extra_artifacts={
                            "ground_truth": [{"path": "../escape.bin"}]
                        },
                    )


if __name__ == "__main__":
    unittest.main()
