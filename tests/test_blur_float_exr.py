import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from exr_io import read_scalar_float_exr


SCRIPT_PATH = REPO_ROOT / "scripts" / "blur_float_exr_gaussian.py"
SPEC = importlib.util.spec_from_file_location("blur_float_exr_gaussian", SCRIPT_PATH)
blur = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = blur
SPEC.loader.exec_module(blur)


class BlurScriptSharedReaderTests(unittest.TestCase):
    def test_blur_script_uses_the_core_scalar_exr_reader(self):
        self.assertIs(blur.read_scalar_float_exr, read_scalar_float_exr)


if __name__ == "__main__":
    unittest.main()
