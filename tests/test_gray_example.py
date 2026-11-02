"""Check Gray unwrapping and ray/plane triangulation independently of Blender."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from generate_gray import generate, make_patterns
from reconstruct_gray import decode, reconstruct, triangulate
from dmd_diamond import build_carrier_mapping_contract
from dmd_setup import prepare


class GrayExampleTests(unittest.TestCase):
    def test_generated_codes_recover_every_column_including_wraps(self):
        for size, period in (((641, 31), 48), ((33, 21), 12.5), ((7, 5), 48)):
            with self.subTest(size=size, period=period):
                frames, bits = make_patterns(*size, period)
                for image in frames:
                    np.testing.assert_array_equal(image, np.broadcast_to(image[0], image.shape))
                images = np.asarray(frames) / 255
                # Reflectance gain and ambient offset vary over the image.
                _, xx = np.indices(size[::-1])
                images = images * (.2 + .5 * xx / size[0]) + .03
                u, valid = decode(images, period)
                self.assertEqual(len(frames), bits + 5)
                self.assertTrue(valid.all())
                np.testing.assert_allclose(u, xx, atol=.05, rtol=0)

    def test_same_patterns_for_both_layouts_and_mapping_only_during_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sequences, manifests = [], []
            for family in ('orthogonal', 'diamond'):
                output = root / family
                generate(ROOT / 'examples' / family / 'scene.json', output, 48)
                sequence = json.loads((output / 'sequence.json').read_text())
                sequences.append(sequence)
                prepare(output / 'scene.json', output / 'render')
                manifests.append(json.loads((output / 'render/inputs/preparation_manifest.json').read_text()))
            self.assertEqual(sequences[0], sequences[1])
            self.assertEqual(len(sequences[0]['files']), 9)
            for a, b in zip(manifests[0]['frames'], manifests[1]['frames']):
                self.assertEqual(a['logical_sha256'], b['logical_sha256'])
                self.assertEqual(a['logical_sha256'], a['projector_sha256'])
                self.assertNotEqual(b['logical_sha256'], b['projector_sha256'])

    def test_only_undefined_or_nonfinite_phase_is_rejected(self):
        frames, _ = make_patterns(64, 8, 16)
        images = np.asarray(frames) / 255
        images[:, :, 0] = 0
        images[:, :, 1] = .8
        images[4, :, 2] = np.nan
        u, valid = decode(images, 16)
        self.assertFalse(valid[:, :3].any())
        self.assertTrue(np.isnan(u[:, :3]).all())
        self.assertTrue(valid[:, 3:].all())

    def test_low_modulation_and_ambiguous_gray_are_not_filtered(self):
        frames, _ = make_patterns(64, 8, 16)
        images = np.asarray(frames) / 255 * 1e-6 + .3
        _, xx = np.indices(images.shape[1:])
        u, valid = decode(images, 16)
        self.assertTrue(valid.all())
        np.testing.assert_allclose(u, xx, atol=.05, rtol=0)
        images[4, :, 2] = images[:4, :, 2].mean(axis=0)
        u, valid = decode(images, 16)
        self.assertTrue(valid.all())
        self.assertTrue(np.isfinite(u).all())

    def test_previous_native_coordinate_sequences_require_regeneration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sequence = root / 'sequence.json'
            sequence.write_text(json.dumps({'schema': 'dmd-sim.vertical-gray.v1'}))
            with self.assertRaisesRegex(ValueError, 'Regenerate rectangular patterns'):
                reconstruct(root / 'old-render', sequence, root / 'new-reconstruction')

    def fixture(self, diamond=False, coincident=False):
        mapping = build_carrier_mapping_contract(logical_size=(64, 48)) if diamond else {
            'carrier_to_native_homography': np.eye(3), 'logical_dmd_size_wh': [64, 48],
            'carrier_texture_size_wh': [64, 48]}
        pw, ph = mapping['carrier_texture_size_wh']
        kc = np.array([[32., 0, 15.5], [0, 32., 11.5], [0, 0, 1]])
        kp = np.array([[40., 0, (pw-1)/2], [0, 40., (ph-1)/2], [0, 0, 1]])
        center = np.array([.03, -.02, -.1])
        pc = center if coincident else np.array([.25, -.02, -.1])
        angle = np.pi / 4 if diamond else .17
        rotation = np.array([[np.cos(angle), -np.sin(angle), 0],
                             [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        rc = np.column_stack((np.eye(3), -center))
        rp = np.column_stack((rotation, -rotation @ pc))
        calibration = {'reference_camera': 'camera', 'camera_projectors': {'camera': 'projector'},
                       'devices': {'camera': {'K': kc, 'world_to_device': rc},
                                   'projector': {'K': kp, 'world_to_device': rp}}}
        yy, xx = np.indices((24, 32))
        truth = center + 1.1 * np.stack(((xx-15.5)/32, (yy-11.5)/32, np.ones_like(xx)), axis=-1)
        points = np.concatenate((truth, np.ones((24, 32, 1))), axis=-1)
        projected = points @ (np.asarray(mapping['carrier_to_native_homography']) @ kp @ rp).T
        native_u = projected[..., 0] / projected[..., 2]
        logical_u = native_u / 2 if diamond else native_u
        return logical_u, truth, calibration, mapping

    def test_standard_fpp_triangulation_has_no_row_offset(self):
        for diamond in (False, True):
            u, truth, calibration, mapping = self.fixture(diamond)
            xyz, valid = triangulate(u, np.ones(u.shape, bool), calibration, mapping)
            self.assertTrue(valid.all())
            np.testing.assert_allclose(xyz[valid], truth[valid], atol=1e-12)

    def test_out_of_bounds_and_negative_depth_results_are_retained(self):
        for diamond in (False, True):
            u, _, calibration, mapping = self.fixture(diamond)
            u += 100
            xyz, valid = triangulate(u, np.ones(u.shape, bool), calibration, mapping)
            self.assertTrue(valid.all())
            self.assertTrue((u >= mapping['logical_dmd_size_wh'][0]).all())
            rc = calibration['devices']['camera']['world_to_device']
            self.assertTrue(((xyz @ rc[:, :3].T + rc[:, 3])[..., 2] < 0).all())

    def test_distant_finite_intersections_are_retained(self):
        for diamond in (False, True):
            _, truth, calibration, mapping = self.fixture(diamond)
            rc = calibration['devices']['camera']['world_to_device']
            center = -rc[:, :3].T @ rc[:, 3]
            truth = center + (truth - center) * 1e7
            projector = calibration['devices']['projector']
            pp = np.asarray(mapping['carrier_to_native_homography']) @ projector['K'] @ projector['world_to_device']
            projected = np.concatenate((truth, np.ones((*truth.shape[:2], 1))), axis=-1) @ pp.T
            u = projected[..., 0] / projected[..., 2] / (2 if diamond else 1)
            xyz, valid = triangulate(u, np.ones(u.shape, bool), calibration, mapping)
            self.assertTrue(valid.all())
            np.testing.assert_allclose(xyz, truth, rtol=1e-6, atol=1e-6)

    def test_coincident_devices_cannot_triangulate(self):
        u, _, calibration, mapping = self.fixture(coincident=True)
        xyz, valid = triangulate(u, np.ones(u.shape, bool), calibration, mapping)
        self.assertFalse(valid.any())
        self.assertTrue(np.isnan(xyz).all())


if __name__ == '__main__':
    unittest.main()
