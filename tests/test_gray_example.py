"""Check Gray unwrapping and ray/plane triangulation independently of Blender."""
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from generate_gray import make_patterns
from reconstruct_gray import decode, triangulate
from dmd_diamond import build_carrier_mapping_contract


class GrayExampleTests(unittest.TestCase):
    def test_generated_codes_recover_every_column_including_wraps(self):
        for diamond in (False, True):
            for size, period in (((641, 31), 48), ((33, 21), 12.5), ((7, 5), 48)):
                with self.subTest(diamond=diamond, size=size, period=period):
                    frames, bits = make_patterns(*size, period, diamond)
                    images = np.asarray(frames) / 255
                    # Reflectance gain and ambient offset vary over the image.
                    yy, xx = np.indices(size[::-1])
                    images = images * (.2 + .5 * xx / size[0]) + .03
                    u, valid = decode(images, period)
                    truth = 2 * xx + (yy & 1) if diamond else xx
                    self.assertEqual(len(frames), bits + 5)
                    self.assertTrue(valid.all())
                    np.testing.assert_allclose(u, truth, atol=.05, rtol=0)

    def test_dark_constant_and_ambiguous_gray_are_rejected(self):
        frames, _ = make_patterns(64, 8, 16)
        images = np.asarray(frames) / 255
        images[:, :, 0] = 0
        images[:, :, 1] = .8
        images[4, :, 2] = images[:4, :, 2].mean(axis=0)
        _, valid = decode(images, 16)
        self.assertFalse(valid[:, :3].any())
        self.assertTrue(valid[:, 3:].all())

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
        return projected[..., 0] / projected[..., 2], truth, calibration, mapping

    def test_plane_reconstruction_in_world_metres_for_both_coordinate_domains(self):
        for diamond in (False, True):
            u, truth, calibration, mapping = self.fixture(diamond)
            xyz, valid = triangulate(u, np.ones(u.shape, bool), calibration, mapping)
            self.assertGreater(valid.sum(), 600)
            np.testing.assert_allclose(xyz[valid], truth[valid], atol=1e-12)

    def test_coincident_devices_cannot_triangulate(self):
        u, _, calibration, mapping = self.fixture(coincident=True)
        xyz, valid = triangulate(u, np.ones(u.shape, bool), calibration, mapping)
        self.assertFalse(valid.any())
        self.assertTrue(np.isnan(xyz).all())


if __name__ == '__main__':
    unittest.main()
