import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'src'))
from calibration_test_support import write_calibration
from gwcam_calibration_io import read_gwcam_calibration

class CalibrationReaderTests(unittest.TestCase):
    def test_rejects_old_text_and_wrong_coordinate_units(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'gwcam-calib.json'
            p.write_text('Resolution: 4x3\nIntrinsic Matrix K:\n')
            with self.assertRaises(ValueError):read_gwcam_calibration(p)
            write_calibration(p,(4,3),{'camera_000':([[1,0,1],[0,1,1],[0,0,1]],[[1,0,0,0],[0,1,0,0],[0,0,1,0]])})
            self.assertEqual(read_gwcam_calibration(p)['camera_resolution'],(4,3))
            data=json.loads(p.read_text());data['translation_unit']='millimeter';p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'units'):read_gwcam_calibration(p)

    def test_multiple_projectors_require_explicit_pairing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'gwcam-calib.json'
            k = [[1,0,1],[0,1,1],[0,0,1]]
            rt = [[1,0,0,0],[0,1,0,0],[0,0,1,0]]
            write_calibration(p,(4,3),{'camera_000':(k,rt)},((4,3),k,rt))
            data=json.loads(p.read_text()); data['devices']['Projector_2']=data['devices']['Projector']
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'Multiple projectors'):read_gwcam_calibration(p)
            data['camera_projectors']={'camera_000':'Projector_2'};p.write_text(json.dumps(data))
            self.assertEqual(read_gwcam_calibration(p)['camera_projectors'],data['camera_projectors'])
            data['camera_projectors']['camera_000']='missing';p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'pairing'):read_gwcam_calibration(p)

    def test_reader_import_uses_only_standard_library(self):
        import subprocess
        result = subprocess.run([sys.executable, '-I', '-S', '-c',
            "import sys; sys.path.insert(0, sys.argv[1]); import gwcam_calibration_io; "
            "assert not ({'bpy', 'numpy', 'config', 'variant'} & set(sys.modules))",
            str(Path(__file__).resolve().parents[1] / 'src')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
