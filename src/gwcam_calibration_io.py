"""Strict reader for core-sim's OpenCV calibration artifact; no text fallback."""
import json
import math
from pathlib import Path

SCHEMA = 'core-sim.gwcam-calib.v1'
FILENAME = 'gwcam-calib.json'
PIXEL_COORDINATE_CONTRACT = 'opencv_integer_centers_v1'


def matrix(value, rows, cols):
    if not isinstance(value, dict) or value.get('type_id') != 'opencv-matrix' or (value.get('rows'), value.get('cols')) != (rows, cols):
        raise ValueError(f'Expected an OpenCV {rows}x{cols} matrix')
    data = value.get('data', [])
    if not isinstance(data, list) or len(data) != rows * cols or any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in data):
        raise ValueError('Calibration matrix contains invalid values')
    return [[float(x) for x in data[r*cols:(r+1)*cols]] for r in range(rows)]


def read_gwcam_calibration(path):
    root = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(root, dict) or root.get('schema') != SCHEMA:
        raise ValueError(f'Expected {SCHEMA}: {path}; regenerate with current core-sim')
    if root.get('pixel_coordinate_contract') != PIXEL_COORDINATE_CONTRACT or root.get('translation_unit') != 'meter':
        raise ValueError(f'Unsupported calibration coordinates or units: {path}')
    devices = {}
    raw_devices = root.get('devices')
    if not isinstance(raw_devices, dict) or not raw_devices:
        raise ValueError('Calibration requires a nonempty devices object')
    for name, device in raw_devices.items():
        if not isinstance(device, dict) or device.get('kind') not in ('camera', 'projector'):
            raise ValueError(f'Invalid device kind: {name}')
        if not isinstance(name, str) or not name:
            raise ValueError('Invalid device name')
        size = device.get('image_size')
        if not isinstance(size, list) or len(size) != 2 or any(isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in size):
            raise ValueError(f'Invalid image size: {name}')
        devices[name] = {'K': matrix(device['K'], 3, 3),
                         'world_to_device': matrix(device['world_to_device'], 3, 4),
                         'kind': device['kind'], 'image_size': size}
        k = devices[name]['K']
        if k[0][0] <= 0 or k[1][1] <= 0 or k[2] != [0., 0., 1.]:
            raise ValueError(f'Invalid pinhole intrinsic: {name}')
        rt = devices[name]['world_to_device']
        if any(abs(sum(rt[i][v] * rt[j][v] for v in range(3)) - float(i == j)) > 1e-7
               for i in range(3) for j in range(3)):
            raise ValueError(f'Device basis is not orthonormal: {name}')
        if 'distortion' in device:
            devices[name]['distortion'] = matrix(device['distortion'], 1, 5)
        if 'P' in device:
            projection = matrix(device['P'], 3, 4)
            if any(not math.isclose(projection[i][j], sum(k[i][v] * rt[v][j] for v in range(3)),
                                    rel_tol=1e-9, abs_tol=1e-8)
                   for i in range(3) for j in range(4)):
                raise ValueError(f'Projection matrix disagrees with K and pose: {name}')
    reference = root.get('reference_camera')
    if not isinstance(reference, str) or reference not in devices or devices[reference]['kind'] != 'camera':
        raise ValueError('Invalid reference camera')
    cameras = {name for name, d in devices.items() if d['kind'] == 'camera'}
    projectors = {name for name, d in devices.items() if d['kind'] == 'projector'}
    pairing = root.get('camera_projectors')
    if pairing is None:
        if len(projectors) > 1:
            raise ValueError('Multiple projectors require camera_projectors')
        pairing = {name: next(iter(projectors)) for name in cameras} if projectors else {}
    if not isinstance(pairing, dict) or (projectors and set(pairing) != cameras) or any(
            name not in cameras or target not in projectors for name, target in pairing.items()):
        raise ValueError('Invalid camera/projector pairing')
    transforms = root.get('device_transforms', {})
    if not isinstance(transforms, dict):
        raise ValueError('Invalid device_transforms')
    for source, targets in transforms.items():
        if source not in devices or not isinstance(targets, dict):
            raise ValueError('Invalid transform source')
        for target, values in targets.items():
            if target not in devices:
                raise ValueError('Invalid transform target')
            transform = matrix(values, 4, 4)
            a, b = devices[source]['world_to_device'], devices[target]['world_to_device']
            rotation = [[sum(b[i][v] * a[j][v] for v in range(3)) for j in range(3)] for i in range(3)]
            expected = [rotation[i] + [b[i][3] - sum(rotation[i][v] * a[v][3] for v in range(3))] for i in range(3)] + [[0.,0.,0.,1.]]
            if any(not math.isclose(transform[i][j], expected[i][j], abs_tol=1e-8, rel_tol=1e-9)
                   for i in range(4) for j in range(4)):
                raise ValueError('Relative transform disagrees with device poses')
    return {'devices': devices, 'camera_projectors': pairing, 'reference_camera': reference,
            'camera_resolution': tuple(devices[reference]['image_size']),
            'projector_resolution': tuple(devices['Projector']['image_size']) if 'Projector' in devices else None,
            'raw': root}
