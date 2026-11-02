#!/usr/bin/env python3
"""Decode vertical complementary Gray + four-step sine and triangulate with scene K/R/t."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from calibration_io import read_calibration
from render_manifest import completed_render_manifest


def decode(images, period, min_modulation=.02):
    """Decode rectangular-image columns from ordered intensities in [0, 1]."""
    images = np.asarray(images, dtype=float)
    if images.ndim != 3 or len(images) < 6 or not np.isfinite(period) or period < 8:
        raise ValueError('Expected four sine frames, Gray bits, one complementary frame and period >= 8')
    sine, cosine = images[0] - images[2], images[1] - images[3]
    phase = np.mod(np.arctan2(sine, cosine), 2 * np.pi)
    amplitude = np.hypot(sine, cosine) / 2
    threshold = images[:4].mean(axis=0)
    gray = images[4:] > threshold
    # Gray-to-binary: cumulative XOR from most significant to least significant.
    binary = np.logical_xor.accumulate(gray, axis=0)
    order = np.zeros(phase.shape, dtype=np.int64)
    for bit in binary[:-1]:
        order = 2 * order + bit
    half_order = 2 * order + binary[-1]
    shifted_order = (half_order + 1) // 2
    order = np.where(phase <= np.pi / 2, shifted_order,
                     np.where(phase >= 3 * np.pi / 2, shifted_order - 1, order))
    u = period * (order + phase / (2 * np.pi))
    valid = np.isfinite(images).all(axis=0) & (amplitude > min_modulation)
    valid &= (np.abs(images[4:] - threshold).min(axis=0) > .1 * amplitude)
    return u, valid


def triangulate(u, valid, calibration, mapping):
    """Ray/plane triangulation in metres; Diamond uses a linear row-average approximation."""
    camera_name = calibration['reference_camera']
    camera = calibration['devices'][camera_name]
    projector = calibration['devices'][calibration['camera_projectors'][camera_name]]
    kc, rc = np.asarray(camera['K']), np.asarray(camera['world_to_device'])
    rp = np.asarray(projector['world_to_device'])
    pp = np.asarray(projector['K']) @ rp
    # Exported K uses carrier coordinates; decoding returns rectangular columns.
    pn = np.asarray(mapping['carrier_to_native_homography']) @ pp
    # True Diamond centres are U = 2*column + row parity. A single vertical
    # sequence has no row code: this minimal linear baseline averages parity.
    native_u = 2 * u + .5 if 'diamond_native_size_wh' in mapping else u
    yy, xx = np.indices(u.shape)
    rays = np.stack((xx, yy, np.ones_like(xx)), axis=-1) @ np.linalg.inv(kc).T @ rc[:, :3]
    center = -rc[:, :3].T @ rc[:, 3]
    planes = pn[0] - native_u[..., None] * pn[2]
    denominator = np.sum(planes[..., :3] * rays, axis=-1)
    scale = np.linalg.norm(planes[..., :3], axis=-1) * np.linalg.norm(rays, axis=-1)
    with np.errstate(divide='ignore', invalid='ignore'):
        distance = -(planes[..., :3] @ center + planes[..., 3]) / denominator
        xyz = center + distance[..., None] * rays
        homogeneous = np.concatenate((xyz, np.ones((*u.shape, 1))), axis=-1)
        native = homogeneous @ pn.T
        native_uv = native[..., :2] / native[..., 2:]
    width, height = mapping['logical_dmd_size_wh']
    valid = valid & (np.abs(denominator) > 1e-6 * scale) & (distance > 0)
    valid &= (native[..., 2] > 0) & np.isfinite(xyz).all(axis=-1)
    valid &= (u >= 0) & (u < width) & (native_uv[..., 1] >= 0) & (native_uv[..., 1] < height)
    xyz[~valid] = np.nan
    return xyz, valid


def reconstruct(run, sequence_path, output):
    run, output = Path(run).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(f'Choose a new output directory: {output}')
    sequence = json.loads(Path(sequence_path).read_text())
    if sequence['schema'] != 'dmd-sim.vertical-gray.v2':
        raise ValueError('Regenerate rectangular patterns and sequence.json with generate_gray.py')
    receipt = json.loads((run / 'run.json').read_text())
    directory = (run / receipt['render_manifest']).parent
    complete, reason = completed_render_manifest(directory)
    if receipt['status'] != 'COMPLETE' or not complete:
        raise ValueError(f'Render is incomplete: {reason}')
    mapping = json.loads((run / 'inputs/dmd_mapping.json').read_text())
    if 'diamond_native_size_wh' in mapping:
        print('Diamond reconstruction: row-average linear approximation U = 2*logical_u + 0.5')
    preparation = json.loads((run / 'inputs/preparation_manifest.json').read_text())
    if (mapping['logical_dmd_size_wh'] != sequence['logical_size'] or
            [f['sha256'] for f in preparation['source_images']] != [f['sha256'] for f in sequence['files']]):
        raise ValueError('Sequence does not match this simulation')
    manifest = json.loads((directory / 'render_manifest.json').read_text())
    calibration = read_calibration(directory / 'calibration.json')
    camera = calibration['reference_camera']
    paths = {Path(f['path']).parts[0]: directory / f['path'] for f in manifest['artifacts']['camera_files']
             if Path(f['path']).stem == camera}
    if len(paths) != sequence['gray_bits'] + 5 or len(paths) != len(sequence['files']):
        raise ValueError('Unexpected frame count')
    images = []
    for i in range(len(paths)):
        with Image.open(paths[f'proj_{i:05d}']) as image:
            if image.size != calibration['camera_resolution']:
                raise ValueError('Camera image size disagrees with calibration')
            images.append(np.asarray(image.convert('L'), dtype=float) / 255)
    u, valid = decode(images, sequence['period'])
    xyz, valid = triangulate(u, valid, calibration, mapping)
    if not valid.any():
        raise ValueError('No valid intersections; check illumination and camera/projector placement')
    u[~valid] = np.nan
    output.mkdir(parents=True)
    np.savez_compressed(output / 'reconstruction.npz', logical_u=u, xyz_world_m=xyz, valid=valid)
    points = xyz[valid]
    header = (f'ply\nformat ascii 1.0\nelement vertex {len(points)}\n'
              'property float x\nproperty float y\nproperty float z\nend_header')
    np.savetxt(output / 'points.ply', points, fmt='%.8f', header=header, comments='')
    print(f'{len(points)} reconstructed points in world metres: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='Completed simulation directory')
    parser.add_argument('--sequence', type=Path, required=True, help='Generated sequence.json')
    parser.add_argument('--output', type=Path, required=True, help='New reconstruction directory')
    args = parser.parse_args()
    reconstruct(args.run, args.sequence, args.output)
