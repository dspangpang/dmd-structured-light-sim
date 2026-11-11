"""Independent 2-D cell-integral oracle and separable-table conformance."""
import math
from pathlib import Path
import sys
import unittest
import hashlib
import json
import tempfile
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from projector_psf import parse_settings, dense_x_table, kernel_table, support_radius, dense_profile, gaussian_pixel_field


def oracle(pixels, x, y, sigma, repeat=False):
    """Direct double sum over pixel rectangles, independent of table generation."""
    a = np.asarray(pixels)
    h, w = a.shape
    result = np.zeros_like(np.asarray(x), dtype=float)
    radius = int(math.ceil(8 * sigma + 1))
    for k, (px, py) in enumerate(zip(x, y)):
        for row in range(math.floor(py) - radius, math.floor(py) + radius + 2):
            if not repeat and not 0 <= row < h:
                continue
            wy = 0.5 * (math.erf((row + .5 - py) / (sigma * math.sqrt(2))) -
                        math.erf((row - .5 - py) / (sigma * math.sqrt(2))))
            for col in range(math.floor(px) - radius, math.floor(px) + radius + 2):
                if not repeat and not 0 <= col < w:
                    continue
                wx = 0.5 * (math.erf((col + .5 - px) / (sigma * math.sqrt(2))) -
                            math.erf((col - .5 - px) / (sigma * math.sqrt(2))))
                result[k] += a[row % h, col % w] * wx * wy
    return result


def lookup(pixels, x, y, sigma, q=32, repeat=False):
    settings = parse_settings(dict(sigma_px=sigma, subsamples_per_pixel=q,
                                   boundary="repeat" if repeat else "constant"))
    table, halo = dense_x_table(pixels, settings)
    kernel, extent = kernel_table(settings)
    h, width, _ = table.shape
    grid = (np.arange(width) + .5) / q - .5 - halo
    kgrid = (np.arange(len(kernel)) + .5) / q - extent
    result = np.zeros_like(np.asarray(x), dtype=float)
    for k, (px, py) in enumerate(zip(x, y)):
        if repeat:
            px = (px + .5) % pixels.shape[1] - .5
            # Texture REPEAT has periodic interpolation across the table seam.
            values = np.array([np.interp(px, np.r_[grid[-1]-pixels.shape[1], grid, grid[0]+pixels.shape[1]],
                                          np.r_[r[-1], r, r[0]]) for r in table[..., 0]])
        else:
            values = np.array([np.interp(px, grid, r, left=0, right=0) for r in table[..., 0]])
        nearest = math.floor(py + .5)
        for n in range(nearest-support_radius(sigma), nearest+support_radius(sigma)+1):
            if repeat or 0 <= n < h:
                result[k] += values[n % h] * np.interp(n-py, kgrid, kernel, left=0, right=0)
    return result


class ContinuousGaussianTests(unittest.TestCase):
    def test_noninvariant_2d_field_matches_direct_rectangle_integrals(self):
        pixels = np.random.default_rng(12).random((7, 9))
        x = np.linspace(-2, 10, 57)
        y = np.random.default_rng(22).uniform(-2, 9, len(x))
        for sigma in (.5, 1.):
            expected = oracle(pixels, x, y, sigma)
            error = np.max(np.abs(lookup(pixels, x, y, sigma)-expected))
            self.assertLess(error, .0004)

    def test_exact_dense_grid_matches_2d_cdf_without_row_averaging(self):
        pixels = np.random.default_rng(3).random((5, 6))
        q = 32
        x = (np.array([0, 5, 25, 90, 140, 175]) + .5) / q - .5
        y = (np.array([20, 60, 100, 140, 37, 89]) + .5) / q - .5
        np.testing.assert_allclose(lookup(pixels, x, y, 1), oracle(pixels, x, y, 1), atol=2e-7)

    def test_constant_zero_boundary_keeps_aperture_tails_and_does_not_wrap(self):
        pixels = np.zeros((5, 7)); pixels[2, 0] = 1
        x, y = np.array([-1., 0., 6., 7.]), np.full(4, 2.)
        result = lookup(pixels, x, y, .5)
        self.assertGreater(result[0], .05)
        self.assertLess(result[-1], 1e-12)
        np.testing.assert_allclose(result, oracle(pixels, x, y, .5), atol=.0004)

    def test_repeat_boundary_and_uniform_field_preserve_dc(self):
        x = np.linspace(-.5, 6.5, 43); y = np.linspace(-3.1, 9.7, 43)
        np.testing.assert_allclose(lookup(np.ones((5, 7)), x, y, 1, repeat=True), 1, atol=2e-6)
        pixels = np.random.default_rng(7).random((5, 7))
        np.testing.assert_allclose(lookup(pixels, x, y, 1, repeat=True),
                                   oracle(pixels, x, y, 1, repeat=True), atol=.0004)

    def test_sampling_converges_for_offgrid_coordinates(self):
        pixels = np.zeros((6, 9)); pixels[1:4, 2:7] = 1
        x = np.linspace(-.123, 8.43, 51); y = np.linspace(-.23, 5.71, 51)
        expected = oracle(pixels, x, y, .5)
        errors = [np.max(np.abs(lookup(pixels, x, y, .5, q=q)-expected)) for q in (8, 16, 32, 64)]
        self.assertTrue(all(b < a for a, b in zip(errors, errors[1:])), errors)

    def test_settings_and_large_allocation_fail_before_work(self):
        self.assertEqual(parse_settings()["subsamples_per_pixel"], 32)
        for value in (dict(sigma_px=-1), dict(sigma_px=float("nan")), dict(sigma_px=True),
                      dict(subsamples_per_pixel=3), dict(sigma_unit="diamond_native"), dict(unknown=1)):
            with self.assertRaises(ValueError):
                parse_settings(value)
        with self.assertRaisesRegex(ValueError, "original Closest"):
            dense_x_table(np.ones((2, 3)), dict(sigma_px=0))


class SharedPsfConfigurationTests(unittest.TestCase):
    def test_independent_defaults_and_zero_control(self):
        from runtime_config import RuntimeConfig
        from render_request import _apply_render
        a, b = RuntimeConfig(), RuntimeConfig()
        self.assertEqual(a.PROJECTOR_TEXTURE_INTERPOLATION, "Closest")
        self.assertEqual(a.PROJECTOR_PSF, parse_settings())
        _apply_render(a, {"projector_psf": {"sigma_px": 0}})
        _apply_render(a, {"projector_psf": {"sigma_px": 0}})
        self.assertEqual(a.PROJECTOR_PSF["sigma_px"], 0)
        self.assertEqual(b.PROJECTOR_PSF["sigma_px"], 1)
        for bad in (None, False, {"unknown": 1}):
            with self.assertRaises(ValueError):
                _apply_render(a, {"projector_psf": bad})

    def test_implementation_is_part_of_render_identity(self):
        from runtime_config import RuntimeConfig
        from render_manifest import _render_config_snapshot
        from unittest.mock import patch
        cfg = RuntimeConfig()
        baseline = _render_config_snapshot(cfg)
        with patch("projector_psf.implementation_sha256", return_value="different implementation"):
            self.assertNotEqual(baseline, _render_config_snapshot(cfg))

    def test_periodic_profile_is_equal_to_independent_2d_integral(self):
        rng = np.random.default_rng(45)
        profile = rng.random(9)
        q = 32
        x = (np.array([0, 25, 180, 274])+.5)/q-.5
        y = np.array([-.4, .2, 3.2, 7.1])
        for sigma in (.5, 1):
            table = dense_profile(profile, parse_settings(dict(boundary="repeat", sigma_px=sigma)))
            np.testing.assert_allclose(table[[0,25,180,274]],
                oracle(np.broadcast_to(profile,(5,9)),x,y,sigma,repeat=True),atol=1e-7)
            np.testing.assert_allclose(gaussian_pixel_field(profile,x,sigma),
                oracle(np.broadcast_to(profile,(5,9)),x,y,sigma,repeat=True),atol=1e-12)
        with self.assertRaisesRegex(ValueError,"repeat boundary"):
            dense_profile(profile,parse_settings())


class PsfManifestTests(unittest.TestCase):
    def test_psf_receipt_is_required_and_hashed_for_complete_and_preview(self):
        from render_manifest import RenderRun
        for status in ("COMPLETE", "VALIDATED"):
            for has_receipt in (True, False):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp); scene = root / "scene.json"; scene.write_text("{}")
                    cfg = SimpleNamespace(APP_SCENE_CONFIG_PATH=str(scene),
                        APP_SCENE_CONFIG_SHA256=hashlib.sha256(scene.read_bytes()).hexdigest(),
                        APP_SCENE_CONFIG_SCHEMA="gwcam_structured_light_scene", RENDER_PRESET="default",
                        ENGINE="CYCLES", SAMPLES=1, CAPTURE_MODE="projector_sequence",
                        PROJECTOR_CAPTURE_IMAGE_FORMAT="BMP", SAVE_DEPTH=False,
                        SAVE_POINTCLOUD=False, PROJECTOR_PSF=parse_settings())
                    try:
                        with RenderRun(root/"output", cfg, repo_root=Path(__file__).resolve().parents[1],
                                       variant_name="core-sim", run_type="test") as run:
                            if has_receipt:
                                (root/"output/projector_psf_updates.jsonl").write_text('{"effective_source":"test"}\n')
                            run.complete(status=status)
                    except RuntimeError as exc:
                        self.assertFalse(has_receipt)
                        self.assertIn("no effective-state", str(exc))
                    else:
                        self.assertTrue(has_receipt)
                        payload = json.loads((root/"output/render_manifest.json").read_text())
                        self.assertEqual(payload["status"], status)
                        self.assertEqual(payload["artifacts"]["projector_psf"][0]["path"], "projector_psf_updates.jsonl")


if __name__ == "__main__":
    unittest.main()
