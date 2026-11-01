#!/usr/bin/env python3
"""Render the bundled plane examples and check images, provenance and 3-D output."""
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
    if calibration["camera_resolution"] != (320, 240) or not calibration["camera_projectors"]:
        raise RuntimeError("Example calibration does not describe its camera/projector pair")
    frames = manifest["artifacts"]["camera_files"]
    if len(frames) != 4:
        raise RuntimeError(f"Expected four example frames, found {len(frames)}")
    images, statistics = [], []
    for frame in frames:
        path = manifest_path.parent / frame["path"]
        with Image.open(path) as image:
            array = np.asarray(image.convert("L"))
            if image.size != (320, 240):
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
        raise RuntimeError("Incomplete plane point cloud")
    error = float(np.quantile(np.abs(points[:, 2]), 0.99))
    if error > 1e-5:
        raise RuntimeError(f"Plane ground-truth z error exceeds 10 micrometres: {error} m")
    return {"status": "PASS", "frame_count": len(frames), "image_size": [320, 240],
            "image_statistics": statistics, "point_count": len(points), "plane_abs_z_p99_m": error,
            "cycles_device": manifest.get("actual_cycles_device")}


def validate(output, blender):
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema": "dmd-sim.example-validation.v1", "examples": {}}
    for family in ("orthogonal", "diamond"):
        command = [sys.executable, str(ROOT / "scripts/run_simulation.py"), "--config",
                   str(ROOT / "examples" / family / "scene.json"), "--output", str(output / family)]
        if blender:
            command += ["--blender-bin", blender]
        with (output / f"{family}.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        report["examples"][family] = inspect_example(output / family)
        print(f"PASS: {family}", flush=True)
    command = [resolve_blender(blender), "-b", "--factory-startup", "--python-exit-code", "1",
               "--python", str(ROOT / "tests/blender_scene_options.py"), "--", "--render-request",
               str(output / "orthogonal/inputs/render_request.json")]
    with (output / "scene_options.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    report["scene_options"] = "PASS"
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
