import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from exr_io import EXRReadError, read_scalar_float_exr


class FakeOpenEXRFile:
    channels_by_path = {}
    calls = []

    def __init__(self, path, *, separate_channels):
        self.path = str(path)
        self.separate_channels = separate_channels
        self.channels_data = self.channels_by_path[self.path]
        self.calls.append((self.path, separate_channels))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def channels(self):
        return self.channels_data


class OpenEXRReaderTests(unittest.TestCase):
    def setUp(self):
        FakeOpenEXRFile.channels_by_path = {}
        FakeOpenEXRFile.calls = []
        self.openexr_module = SimpleNamespace(File=FakeOpenEXRFile)

    def _install_fake_file(self, channels):
        path = "/tmp/core-sim-test-image.exr"
        FakeOpenEXRFile.channels_by_path[path] = {
            name: SimpleNamespace(pixels=np.asarray(pixels))
            for name, pixels in channels.items()
        }
        return Path(path)

    def test_v_channel_is_preferred_and_returned_as_float32(self):
        path = self._install_fake_file(
            {
                "Y": np.full((2, 3), 2.0),
                "V": np.arange(6, dtype=np.float64).reshape(2, 3),
            }
        )

        with patch.dict(sys.modules, {"OpenEXR": self.openexr_module}):
            image = read_scalar_float_exr(path)

        np.testing.assert_array_equal(
            image,
            np.arange(6, dtype=np.float32).reshape(2, 3),
        )
        self.assertEqual(image.dtype, np.float32)
        self.assertTrue(image.flags.c_contiguous)
        self.assertEqual(FakeOpenEXRFile.calls, [(str(path), True)])

    def test_explicit_channel_overrides_default_preference(self):
        path = self._install_fake_file(
            {
                "Y": np.full((2, 3), 2.0),
                "V": np.full((2, 3), 3.0),
            }
        )

        with patch.dict(sys.modules, {"OpenEXR": self.openexr_module}):
            image = read_scalar_float_exr(path, channel="Y")

        np.testing.assert_array_equal(image, np.full((2, 3), 2.0))

    def test_equal_rgb_is_accepted_when_scalar_channels_are_absent(self):
        pixels = np.arange(6, dtype=np.float32).reshape(2, 3)
        path = self._install_fake_file(
            {"R": pixels, "G": pixels.copy(), "B": pixels.copy()}
        )

        with patch.dict(sys.modules, {"OpenEXR": self.openexr_module}):
            image = read_scalar_float_exr(path)

        np.testing.assert_array_equal(image, pixels)

    def test_non_equal_rgb_is_rejected_instead_of_silently_using_red(self):
        pixels = np.arange(6, dtype=np.float32).reshape(2, 3)
        path = self._install_fake_file(
            {"R": pixels, "G": pixels + 1.0, "B": pixels.copy()}
        )

        with patch.dict(sys.modules, {"OpenEXR": self.openexr_module}):
            with self.assertRaisesRegex(ValueError, "non-identical RGB"):
                read_scalar_float_exr(path)

    def test_read_fails_when_openexr_is_unavailable(self):
        path = Path("/tmp/missing-openexr.exr")

        with patch.dict(sys.modules, {"OpenEXR": None}):
            with self.assertRaisesRegex(EXRReadError, "OpenEXR"):
                read_scalar_float_exr(path)


if __name__ == "__main__":
    unittest.main()
