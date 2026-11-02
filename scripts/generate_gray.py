#!/usr/bin/env python3
"""Generate vertical four-step sine + complementary Gray patterns and a scene JSON."""
import argparse
import copy
import json
import math
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dmd_setup import sha256, validate_config, write_json


def make_patterns(width, height, period, diamond=False):
    """Gray encodes fringe order; sine encodes position within each period."""
    if width < 2 or height < 2 or not math.isfinite(period) or period < 8:
        raise ValueError("Width/height must be >= 2 and period must be finite and >= 8")
    yy, xx = np.indices((height, width))
    # Vertical means native physical U: odd Diamond rows have a one-unit offset.
    u = 2 * xx + (yy & 1) if diamond else xx
    span = 2 * width if diamond else width
    bits = max(1, (math.ceil(span / period) - 1).bit_length())
    phase = 2 * np.pi * u / period
    images = [np.rint(127.5 + 100 * np.sin(phase + k * np.pi / 2)).astype(np.uint8)
              for k in range(4)]
    order = np.floor(u / period).astype(np.int64)
    gray = order ^ (order >> 1)
    images += [(((gray >> bit) & 1) * 255).astype(np.uint8)
               for bit in range(bits - 1, -1, -1)]
    half_order = np.floor(2 * u / period).astype(np.int64)
    images.append((((half_order ^ (half_order >> 1)) & 1) * 255).astype(np.uint8))
    return images, bits


def generate(config_path, output, period):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = copy.deepcopy(validate_config(json.loads(config_path.read_text())))
    width, height = config['projector']['resolution']
    kind = config['projector']['type']
    images, bits = make_patterns(width, height, period, kind == 'diamond')
    # Keep assets valid when the generated scene JSON moves to another directory.
    if 'asset' in config['target']:
        config['target']['asset'] = str((config_path.parent / config['target']['asset']).resolve())
    for model in config.get('visualization', {}).values():
        if 'path' in model:
            model['path'] = str((config_path.parent / model['path']).resolve())
    names = [f'proj_{i:05d}.png' for i in range(len(images))]
    config['patterns'] = {'kind': 'files', 'files': names}
    config.setdefault('capture', {})['projector_image_format'] = 'PNG'
    output.mkdir(parents=True, exist_ok=False)
    for name, image in zip(names, images):
        Image.fromarray(image).save(output / name)
    write_json(output / 'scene.json', config)
    write_json(output / 'sequence.json', {
        'schema': 'dmd-sim.vertical-gray.v1', 'dmd_type': kind,
        'logical_size': [width, height], 'period': period, 'gray_bits': bits,
        'files': [{'name': name, 'sha256': sha256(output / name)} for name in names],
    })
    print(f'{len(images)} frames: 4 sine + {bits} Gray + 1 complementary; {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory')
    parser.add_argument('--period', type=float, default=48, help='Period in native U units (>= 8)')
    args = parser.parse_args()
    generate(args.config, args.output, args.period)
