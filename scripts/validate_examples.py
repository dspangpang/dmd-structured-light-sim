#!/usr/bin/env python3
"""Render the bundled sphere examples and check images, calibration and 3-D output."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dmd_setup import write_json
from render_manifest import completed_render_manifest
from calibration_io import read_calibration
from run_simulation import resolve_blender
from generate_gray import generate
from reconstruct_gray import reconstruct
from exr_io import read_scalar_float_exr


def inspect_gray(run, reconstruction):
    """Depth truth is used only here for evaluation, never by the decoder."""
    data = np.load(reconstruction / 'reconstruction.npz')
    xyz, valid = data['xyz_world_m'], data['valid']
    receipt = json.loads((run / 'run.json').read_text())
    directory = (run / receipt['render_manifest']).parent
    calibration = read_calibration(directory / 'calibration.json')
    camera = calibration['reference_camera']
    depth = read_scalar_float_exr(directory / 'proj_00000' / f'{camera}_depth.exr')
    foreground = np.isfinite(depth) & (depth > 0) & (depth < 1e8)
    matched = valid & foreground
    rt = np.asarray(calibration['devices'][camera]['world_to_device'])
    camera_z = (xyz @ rt[:, :3].T + rt[:, 3])[..., 2]
    coverage = float(matched.sum() / max(foreground.sum(), 1))
    if valid.sum() < 1000 or coverage < .8:
        raise RuntimeError('Too few reconstructed sphere points')
    sphere_error = float(np.quantile(np.abs(np.linalg.norm(xyz[valid], axis=-1) - .1), .99))
    depth_error = float(np.quantile(np.abs(camera_z[matched] - depth[matched]), .99))
    # Shared 2 mm example tolerance for basic FPP with raster-sampling error.
    tolerance = .002
    if not np.isfinite([sphere_error, depth_error]).all() or max(sphere_error, depth_error) > tolerance:
        raise RuntimeError(f'Gray reconstruction failed: sphere={sphere_error}, depth={depth_error} m')
    return {'status': 'PASS', 'tolerance_m': tolerance,
            'point_count': int(valid.sum()), 'foreground_coverage': coverage,
            'sphere_error_p99_m': sphere_error, 'depth_error_p99_m': depth_error}


def inspect_example(output):
    receipt = json.loads((output / "run.json").read_text())
    if receipt["status"] != "COMPLETE":
        raise RuntimeError("Example did not render")
    manifest_path = output / receipt["render_manifest"]
    complete, reason = completed_render_manifest(manifest_path.parent)
    if not complete:
        raise RuntimeError(reason)
    manifest = json.loads(manifest_path.read_text())
    calibration = read_calibration(manifest_path.parent / "calibration.json")
    if calibration["raw"].get("calibration_result_source") != "blender_ground_truth":
        raise RuntimeError("Expected calibration ground truth from scene geometry")
    if calibration["camera_resolution"] != (512, 512) or not calibration["camera_projectors"]:
        raise RuntimeError("Example calibration does not describe its camera/projector pair")
    mapping = json.loads((output / "inputs/dmd_mapping.json").read_text())
    for device in calibration["devices"].values():
        if device["kind"] == "projector" and device["image_size"] != mapping["carrier_texture_size_wh"]:
            raise RuntimeError("Projector truth resolution does not match its carrier texture")
    frames = manifest["artifacts"]["camera_files"]
    if len(frames) != 4:
        raise RuntimeError(f"Expected four example frames, found {len(frames)}")
    images, statistics = [], []
    for frame in frames:
        path = manifest_path.parent / frame["path"]
        with Image.open(path) as image:
            array = np.asarray(image.convert("L"))
            if image.size != (512, 512):
                raise RuntimeError(f"Wrong image dimensions: {image.size}")
        if array.std() < 5 or (array == 255).mean() > 0.01:
            raise RuntimeError(f"Blank or saturated example: {path}")
        images.append(array)
        statistics.append({"min": int(array.min()), "max": int(array.max()), "std": float(array.std())})
        for suffix in ("_float.exr", "_depth.exr", "_pointcloud.ply"):
            artifact = path.with_name(path.stem + suffix)
            if not artifact.is_file() or artifact.stat().st_size == 0:
                raise RuntimeError(f"Missing example artifact: {artifact}")
    if any(np.array_equal(images[0], image) for image in images[1:]):
        raise RuntimeError("Phase-shifted frames unexpectedly match")
    cloud = manifest_path.parent / frames[0]["path"]
    cloud = cloud.with_name(cloud.stem + "_pointcloud.ply")
    with cloud.open("rb") as stream:
        while True:
            line = stream.readline()
            if not line:
                raise RuntimeError("Invalid PLY header")
            if line.strip() == b"end_header":
                break
        points = np.frombuffer(stream.read(), dtype="<f4").reshape(-1, 3)
    if len(points) < 1000 or not np.isfinite(points).all():
        raise RuntimeError("Incomplete sphere point cloud")
    error = float(np.quantile(np.abs(np.linalg.norm(points, axis=1) - .1), 0.99))
    # The bundled mesh approximates an analytic sphere with planar triangles.
    if error > 1e-4:
        raise RuntimeError(f"Sphere radius residual exceeds 0.1 mm: {error} m")
    return {"status": "PASS", "frame_count": len(frames), "image_size": [512, 512],
            "logical_dmd_size_wh": mapping["logical_dmd_size_wh"],
            "carrier_texture_size_wh": mapping["carrier_texture_size_wh"],
            "image_statistics": statistics, "point_count": len(points), "sphere_radius_error_p99_m": error,
            "cycles_device": manifest.get("actual_cycles_device")}


def validate(output, blender):
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema": "dmd-sim.example-validation.v1", "examples": {}}
    cases = {family: ROOT / "examples" / family / "scene.json" for family in ("orthogonal", "diamond")}
    # Exercise both odd row/column counts with the same optical geometry.
    odd = json.loads(cases["diamond"].read_text())
    odd["projector"]["resolution"] = [641, 481]
    odd["target"]["asset"] = str((cases["diamond"].parent / odd["target"]["asset"]).resolve())
    for model in odd.get("visualization", {}).values():
        model["path"] = str((cases["diamond"].parent / model["path"]).resolve())
    cases["diamond_odd"] = output / "diamond_odd_scene.json"
    write_json(cases["diamond_odd"], odd)
    for family, config in cases.items():
        command = [sys.executable, str(ROOT / "scripts/run_simulation.py"), "--config",
                   str(config), "--output", str(output / family)]
        if blender:
            command += ["--blender-bin", blender]
        with (output / f"{family}.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        report["examples"][family] = inspect_example(output / family)
        print(f"PASS: {family}", flush=True)
    for family in ('orthogonal', 'diamond'):
        command = [resolve_blender(blender), "-b", "--factory-startup", "--python-exit-code", "1",
                   "--python", str(ROOT / "tests/blender_scene_options.py"), "--", "--render-request",
                   str(output / family / "inputs/render_request.json")]
        with (output / f"{family}_scene_options.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    report["scene_options"] = "PASS"
    report['gray_reconstruction'] = {}
    for family in ('orthogonal', 'diamond'):
        base = output / f'gray_{family}'
        generate(cases[family], base / 'patterns', 48)
        command = [sys.executable, str(ROOT / 'scripts/run_simulation.py'), '--config',
                   str(base / 'patterns/scene.json'), '--output', str(base / 'render'),
                   '--blender-bin', resolve_blender(blender)]
        with (base / 'render.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        reconstruct(base / 'render', base / 'patterns/sequence.json', base / 'reconstruction')
        report['gray_reconstruction'][family] = inspect_gray(base / 'render', base / 'reconstruction')
        print(f'PASS: {family} Gray reconstruction', flush=True)
    write_json(output / "validation.json", report)
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender-bin")
    parser.add_argument("--output", type=Path, help="Keep renders and logs in a new directory")
    args = parser.parse_args()
    if args.output:
        if args.output.exists():
            parser.error("--output must not already exist")
        validate(args.output.resolve(), args.blender_bin)
    else:
        with tempfile.TemporaryDirectory(prefix="dmd-examples-") as temp:
            validate(Path(temp), args.blender_bin)


if __name__ == "__main__":
    main()
