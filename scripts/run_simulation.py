#!/usr/bin/env python3
"""Prepare and run either supported DMD family from one standalone JSON file."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dmd_setup import prepare, write_json
from render_manifest import completed_render_manifest


def resolve_blender(value):
    candidate = value or os.environ.get("BLENDER_BIN") or os.environ.get("BLENDER") or "blender"
    resolved = shutil.which(candidate)
    if not resolved:
        raise FileNotFoundError(f"Blender not found: {candidate}. Set --blender-bin or BLENDER_BIN.")
    return str(Path(resolved).resolve())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Standalone dmd-sim.scene.v1 JSON")
    parser.add_argument("--output", type=Path, required=True, help="New output directory; existing directories are preserved")
    parser.add_argument("--blender-bin", help="Blender executable; otherwise BLENDER_BIN, BLENDER, then PATH")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true", help="Generate inputs without starting Blender")
    mode.add_argument("--validate-only", action="store_true", help="Build and validate the actual scene without rendering")
    mode.add_argument("--interactive", action="store_true", help="Open Blender with an editable four-step scene and save scene.blend")
    args = parser.parse_args(argv)
    blender = None if args.prepare_only else resolve_blender(args.blender_bin)
    request = prepare(args.config, args.output)
    print(f"Prepared inputs: {request.parent}", flush=True)
    if args.prepare_only:
        return 0
    if args.interactive:
        scene_file = args.output.resolve() / "scene.blend"
        subprocess.run([blender, "--factory-startup", "--python-exit-code", "1",
                        "--python", str(ROOT / "blender/open_scene.py"), "--",
                        "--render-request", str(request), "--save", str(scene_file)], cwd=ROOT, check=True)
        if not scene_file.is_file():
            raise RuntimeError("Interactive scene was not saved")
        print(f"Saved editable scene: {scene_file}", flush=True)
        return 0
    command = [blender, "--background", "--factory-startup", "--python-exit-code", "1",
               "--python", str(ROOT / "blender/stereo_with_projector_parented.py"),
               "--", "--render-request", str(request)]
    if args.validate_only:
        command.append("--validate-only")
    subprocess.run(command, cwd=ROOT, check=True)
    manifests = list((args.output.resolve() / "render").glob("*/render_manifest.json"))
    if len(manifests) != 1:
        raise RuntimeError(f"Expected one render manifest, found {len(manifests)}")
    manifest = manifests[0]
    if args.validate_only:
        if json.loads(manifest.read_text())["status"] != "VALIDATED":
            raise RuntimeError("Scene validation did not finish")
    else:
        valid, reason = completed_render_manifest(manifest.parent)
        if not valid:
            raise RuntimeError(f"Render verification failed: {reason}")
    write_json(args.output.resolve() / "run.json", {
        "schema": "dmd-sim.run.v1", "status": "VALIDATED" if args.validate_only else "COMPLETE",
        "render_manifest": manifest.relative_to(args.output.resolve()).as_posix(),
        "inputs": "inputs/preparation_manifest.json",
    })
    print(f"{'VALIDATED' if args.validate_only else 'COMPLETE'}: {manifest}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Simulation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
