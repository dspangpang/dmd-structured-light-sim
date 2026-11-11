#!/usr/bin/env python3
"""Check shared files and the expected simulator branch lineage.

The simulator repository has two layers below ``core-sim``: single- and
dual-camera bases, followed by project-specific branches.  This check keeps
the truly shared renderer files identical and verifies that each worktree is
based on the correct parent branch.  Files that implement a project's
capture extension are deliberately excluded from ``SHARED_PATHS``.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess


SHARED_PATHS = (
    "3rdparty/Projectors/__init__.py",
    "3rdparty/Projectors/helper.py",
    "3rdparty/Projectors/projector.py",
    "blender/stereo_with_projector_parented.py",
    "scripts/batch_render.py",
    "scripts/batch_render.example.txt",
    "scripts/blender_env.sh",
    "scripts/check_core_sync.py",
    "scripts/open_blender_gui.sh",
    "scripts/run_blender.sh",
    "scripts/run_calibration_checkerboard.sh",
    "scripts/run_calibration_circle_grid.sh",
    "src/app_scene_config.py",
    "src/calc_params.py",
    "src/cleanup.py",
    "src/cli.py",
    "src/exr_io.py",
    "src/config.py",
    "src/cycles_device.py",
    "src/projector.py",
    "src/projector_psf.py",
    "tests/test_projector_psf.py",
    "tests/blender_projector_psf.py",
    "src/gwcam_calibration.py",
    "src/gwcam_calibration_io.py",
    "tests/test_calibration_io.py",
    "tests/calibration_test_support.py",
    "src/blender_parameters.py",
    "src/pixel_coordinates.py",
    "tests/blender_pixel_coordinates.py",
    "src/render.py",
    "src/render_manifest.py",
    "src/render_presets.py",
    "src/rig_geometry.py",
    "src/rig_topology.py",
    "src/scene.py",
    "src/variant_api.py",
    "tests/test_gwcam_calibration.py",
    "tests/test_cycles_device.py",
    "tests/test_blender_env.py",
    "tests/test_exr_io.py",
)
DEFAULT_DERIVED = (
    "single-camera",
    "dual-camera",
    "diamond-dmd-sim",
    "FoSSL",
    "fossl-ps-capture",
    "FoSSL-led",
)

# A project branch inherits the camera-topology policy before adding its own
# renderer or capture extension.  ``main`` is intentionally not listed here:
# it is a stable release branch and may lag ``core-sim`` between releases.
BRANCH_PARENTS = {
    "single-camera": "core-sim",
    "dual-camera": "core-sim",
    "diamond-dmd-sim": "single-camera",
    "FoSSL": "single-camera",
    # FoSSL-led currently has a direct core baseline; its directory is retained.
    "fossl-ps-capture": "core-sim",
    "FoSSL-led": "core-sim",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _head(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return None
    return result.stdout.strip()


def _is_ancestor(parent: Path, child: Path) -> bool:
    parent_head = _head(parent)
    child_head = _head(child)
    if not parent_head or not child_head:
        return False
    result = subprocess.run(
        ["git", "-C", str(child), "merge-base", "--is-ancestor", parent_head, child_head],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main() -> int:
    script_root = Path(__file__).resolve().parents[1]
    container = script_root.parent
    default_reference = container / "core-sim"
    parser = argparse.ArgumentParser(
        description="Compare core-owned files across sibling Git worktrees."
    )
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Derived worktree roots; defaults to known siblings.",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=default_reference if default_reference.is_dir() else script_root,
        help="Authoritative core-sim worktree.",
    )
    parser.add_argument(
        "--skip-lineage",
        action="store_true",
        help="Only compare shared files; do not check parent branch ancestry.",
    )
    parser.add_argument("--psf-only", action="store_true", help="Compare shared projector optics and conformance tests only.")
    args = parser.parse_args()
    paths = ("src/projector_psf.py", "src/projector.py", "tests/test_projector_psf.py", "tests/blender_projector_psf.py", "PROJECTOR_PSF.md") if args.psf_only else SHARED_PATHS

    reference = args.reference.expanduser().resolve()
    roots = [root.expanduser().resolve() for root in args.roots]
    if not roots:
        roots = [container / name for name in DEFAULT_DERIVED if (container / name).is_dir()]
    if not roots:
        parser.error("No derived worktrees found; pass their roots explicitly")

    failures = []
    for relative in paths:
        source = reference / relative
        if not source.is_file():
            failures.append(f"reference missing: {source}")
            continue
        source_digest = digest(source)
        for root in roots:
            candidate = root / relative
            if not candidate.is_file():
                failures.append(f"derived missing: {candidate}")
            elif digest(candidate) != source_digest:
                failures.append(f"drift: {candidate} != {source}")

    if not args.skip_lineage and not args.psf_only:
        for root in roots:
            parent_name = BRANCH_PARENTS.get(root.name)
            if not parent_name:
                continue
            parent = container / parent_name
            if not parent.is_dir():
                continue
            if not _is_ancestor(parent, root):
                failures.append(
                    f"lineage: {root.name} is not based on {parent_name} "
                    f"({root} <- {parent})"
                )

    if failures:
        print("[CoreSync] FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"[CoreSync] OK: {len(paths)} files match across "
        f"{len(roots)} derived worktree(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
