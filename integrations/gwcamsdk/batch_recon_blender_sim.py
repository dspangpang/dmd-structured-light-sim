from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from gwcam_calibration import load_reconstruction_calibrations

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover - runtime guard
    raise SystemExit(
        "This script needs OpenCV and numpy. Activate an environment such as "
        "`pip install -r requirements-postprocess.txt` before running it."
    ) from exc


# ---------------------------------------------------------------------------
# Default parameters. Edit these for the common no-argument workflow.
# Command-line arguments still override these values when provided.
SOURCE_ROOT_DIR = Path("output")
SOURCE_ROOT = SOURCE_ROOT_DIR
GWCAM_ROOT = None
# Optional comma-separated batch names. Leave unset to auto-discover all batch_* dirs.
# Example: "batch_diffuse,batch_metal"
SOURCE_BATCHES = None
OUTPUT_ROOT = None
# Required. Set to a fringe/data directory name, a directory path, or code-config.json path.
FRINGE_ROOT = Path("assets/fringes")
# Example: "fringe-config_ps4_pc2_a0-90_mrect-rect_bd8x16_img16_t45-46_hb127.5_n0_1920#1080"
FRINGE_CONFIG = None
CONFIG_TEMPLATE = "reconsutruction/config/reconstruction_config.json"
RECON_BIN = "bin/reconsutruction/cam_proj_recon_test"
THIRDPARTY_PREFIX = "3rdparty/install"
DEBUG = False
USE_CUDA = False
MODULATION_THRESHOLD = 0.0
SINUSOIDAL_FIT_THRESHOLD = 0.0
DRY_RUN = False
# ---------------------------------------------------------------------------


def log(message: str) -> None:
    print(message, flush=True)


@dataclass(frozen=True)
class Dataset:
    source_dir: Path
    batch_name: Optional[str]
    object_name: str
    dataset_name: str
    frame_count: int


@dataclass(frozen=True)
class FringeConfig:
    code_config_path: Path
    config_dir: Path
    total_image_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Flatten Blender projection captures, generate per-camera calibration, "
            "and run reconstruction for Blender simulation batches."
        )
    )
    parser.add_argument(
        "--source-root",
        default=SOURCE_ROOT,
        help=(
            "Root containing batch folders, object folders, or one Blender dataset with proj_* folders. "
            "The default points at the Blender simulation output root."
        ),
    )
    parser.add_argument(
        "--gwcam-root",
        required=True,
        help="Path to a separately built gwCamSDK checkout (required).",
    )
    parser.add_argument(
        "--source-batches",
        "--source-batch",
        default=SOURCE_BATCHES,
        help=(
            "Optional comma-separated batch directory names under --source-root. "
            "Default auto-discovers all batch_* directories when --source-root contains batches."
        ),
    )
    parser.add_argument(
        "--output-root",
        default=OUTPUT_ROOT,
        help=(
            "Optional output root. Default writes under the source dataset's reconstruction directory. "
            "When set, batch names are included as <output-root>/<batch>/<object>/<dataset>."
        ),
    )
    parser.add_argument(
        "--fringe-config",
        required=True,
        help=(
            "Fringe config directory, code-config.json path, or directory name under --fringe-root. "
            "Required; no experiment-specific pattern configuration is assumed."
        ),
    )
    parser.add_argument(
        "--fringe-root",
        default=FRINGE_ROOT,
        help="Directory containing local fringe assets. Defaults to assets/fringes.",
    )
    parser.add_argument(
        "--config-template",
        default=CONFIG_TEMPLATE,
        help="Base reconstruction config used for reconstruction defaults.",
    )
    parser.add_argument(
        "--recon-bin",
        default=RECON_BIN,
        help="Reconstruction executable.",
    )
    parser.add_argument(
        "--thirdparty-prefix",
        default=THIRDPARTY_PREFIX,
        help="gwCamSDK repo-local dependency prefix used for subprocess runtime libraries.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=DEBUG,
        help="Enable debug in generated reconstruction configs.",
    )
    parser.add_argument(
        "--use-cuda",
        action="store_true",
        default=USE_CUDA,
        help="Request CUDA reconstruction in generated configs.",
    )
    parser.add_argument(
        "--modulation-threshold",
        type=float,
        default=MODULATION_THRESHOLD,
        help="Modulation threshold for point filtering. Values <= 0 disable this filter.",
    )
    parser.add_argument(
        "--sinusoidal-fit-threshold",
        type=float,
        default=SINUSOIDAL_FIT_THRESHOLD,
        help="Sinusoidal-fit threshold for point filtering. Values <= 0 disable this filter.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=DRY_RUN,
        help="Print planned work without writing or running.",
    )
    return parser.parse_args()


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_project_path(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else root / path


def resolve_gwcam_path(path: Path, gwcam_root: Path) -> Path:
    return path if path.is_absolute() else gwcam_root / path


def to_project_relative(path: Path, root: Path) -> str:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    try:
        return str(resolved_path.relative_to(resolved_root))
    except ValueError:
        return str(resolved_path)


def prepend_env_path(env: dict[str, str], name: str, path: Path) -> None:
    if not path.exists():
        return
    value = str(path)
    current = env.get(name, "")
    parts = [part for part in current.split(os.pathsep) if part]
    if value in parts:
        parts.remove(value)
    env[name] = os.pathsep.join([value, *parts])


def build_subprocess_env(gwcam_root: Path, thirdparty_prefix: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CODE_ROOT"] = str(gwcam_root)
    env["GWCAMSDK_THIRDPARTY_PREFIX"] = str(thirdparty_prefix)

    prepend_env_path(env, "PATH", thirdparty_prefix / "bin")
    prepend_env_path(env, "PATH", thirdparty_prefix / "tools" / "cmake-3.27.9-linux-x86_64" / "bin")
    prepend_env_path(env, "LD_LIBRARY_PATH", thirdparty_prefix / "lib")
    prepend_env_path(env, "LD_LIBRARY_PATH", thirdparty_prefix / "lib64")
    prepend_env_path(env, "LIBRARY_PATH", thirdparty_prefix / "lib")
    prepend_env_path(env, "LIBRARY_PATH", thirdparty_prefix / "lib64")
    prepend_env_path(env, "PKG_CONFIG_PATH", thirdparty_prefix / "lib" / "pkgconfig")
    prepend_env_path(env, "PKG_CONFIG_PATH", thirdparty_prefix / "lib64" / "pkgconfig")
    prepend_env_path(env, "CMAKE_PREFIX_PATH", thirdparty_prefix)

    opencv_dir = thirdparty_prefix / "lib" / "cmake" / "opencv4"
    if (opencv_dir / "OpenCVConfig.cmake").exists():
        env["OpenCV_DIR"] = str(opencv_dir)
    return env


def is_dataset_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    for child in path.iterdir():
        if child.is_dir() and child.name.startswith("proj_"):
            return True
    return False


def parse_source_batches(value: Optional[str]) -> list[str]:
    if not value:
        return []
    batches = []
    seen = set()
    for item in value.split(","):
        batch = item.strip()
        if not batch or batch in seen:
            continue
        batches.append(batch)
        seen.add(batch)
    return batches


def find_batch_roots(source_root: Path, source_batches: list[str]) -> list[Path]:
    if source_batches:
        batch_roots = []
        for batch_name in source_batches:
            batch_path = Path(batch_name)
            batch_root = batch_path if batch_path.is_absolute() else source_root / batch_name
            if not batch_root.is_dir():
                raise FileNotFoundError(f"Batch root does not exist: {batch_root}")
            batch_roots.append(batch_root)
        return batch_roots

    if is_dataset_dir(source_root):
        return [source_root]

    batch_roots = sorted(
        path
        for path in source_root.iterdir()
        if path.is_dir() and path.name.startswith("batch_")
    )
    return batch_roots or [source_root]


def find_dataset_dirs(source_root: Path) -> list[Path]:
    if is_dataset_dir(source_root):
        return [source_root]
    return sorted(path for path in source_root.rglob("*") if is_dataset_dir(path))


def collect_frames(source_root: Path) -> dict[str, list[Path]]:
    proj_dirs = sorted(path for path in source_root.iterdir() if path.is_dir() and path.name.startswith("proj_"))
    proj_pattern = re.compile(r"proj_(\d+)$")
    cam_pattern = re.compile(r"^(camera_\d+)\.[a-zA-Z0-9]+$")
    camera_frames: dict[str, list[tuple[int, Path]]] = {}

    for proj_dir in proj_dirs:
        proj_match = proj_pattern.match(proj_dir.name)
        if not proj_match:
            continue
        proj_idx = int(proj_match.group(1))
        for img_path in sorted(proj_dir.iterdir()):
            if not img_path.is_file():
                continue
            cam_match = cam_pattern.match(img_path.name)
            if not cam_match:
                continue
            camera_frames.setdefault(cam_match.group(1), []).append((proj_idx, img_path))

    result: dict[str, list[Path]] = {}
    for camera_name, frames in camera_frames.items():
        frames.sort(key=lambda item: item[0])
        result[camera_name] = [path for _, path in frames]
    return result


def build_dataset(source_dir: Path, batch_root: Optional[Path] = None) -> Dataset:
    frames = collect_frames(source_dir)
    if not frames:
        raise RuntimeError(f"No camera frames found under {source_dir}")

    frame_counts = {camera: len(paths) for camera, paths in frames.items()}
    unique_counts = set(frame_counts.values())
    if len(unique_counts) != 1:
        raise RuntimeError(f"Inconsistent frame counts under {source_dir}: {frame_counts}")

    batch_name = None
    if batch_root is not None and batch_root != source_dir and batch_root.name.startswith("batch_"):
        batch_name = batch_root.name
    else:
        for parent in source_dir.parents:
            if parent.name.startswith("batch_"):
                batch_name = parent.name
                break

    object_name = source_dir.parent.name
    return Dataset(
        source_dir=source_dir,
        batch_name=batch_name,
        object_name=object_name,
        dataset_name=source_dir.name,
        frame_count=unique_counts.pop(),
    )


def load_code_config(code_config_path: Path) -> dict[str, object]:
    with code_config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_total_image_count(code_config_path: Path) -> int:
    config = load_code_config(code_config_path)

    formal_image_count = int(config.get("formal_image_count") or 0)
    if formal_image_count > 0:
        return formal_image_count

    pattern_sequence = config.get("pattern_sequence") or []
    if pattern_sequence:
        max_index = max(int(item.get("image_index", -1)) for item in pattern_sequence)
        if max_index >= 0:
            return max_index + 1

    refs = config.get("angle_phase_refs") or []
    total = sum(int(ref.get("image_count", 0)) for ref in refs)
    if total > 0:
        return total
    return len(list(code_config_path.parent.glob("proj_*.bmp")))



def list_fringe_configs(fringe_root: Path) -> list[FringeConfig]:
    configs = []
    for code_config_path in sorted(fringe_root.glob("*/code-config.json")):
        configs.append(
            FringeConfig(
                code_config_path=code_config_path,
                config_dir=code_config_path.parent,
                total_image_count=load_total_image_count(code_config_path),
            )
        )
    return configs


def fringe_candidates(value: str, root: Path, fringe_root: Path) -> list[Path]:
    candidate = Path(value)
    if candidate.is_absolute():
        return [candidate]

    candidates = [root / candidate, fringe_root / candidate]
    parts = candidate.parts
    if len(parts) >= 3 and parts[0] == "fringe" and parts[1] == "data":
        candidates.append(fringe_root.joinpath(*parts[2:]))
    return candidates


def resolve_named_fringe_config(value: str, root: Path, fringe_root: Path) -> FringeConfig:
    for candidate in fringe_candidates(value, root, fringe_root):
        if candidate.is_dir():
            candidate = candidate / "code-config.json"
        if candidate.is_file():
            return FringeConfig(candidate, candidate.parent, load_total_image_count(candidate))

    matches = [
        config
        for config in list_fringe_configs(fringe_root)
        if config.config_dir.name == value or config.config_dir.name.endswith(value)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"Cannot resolve fringe config: {value}")
    choices = "\n".join(f"  - {config.config_dir.name}" for config in matches)
    raise RuntimeError(f"Ambiguous fringe config '{value}'. Matches:\n{choices}")


def opencv_matrix(mat: np.ndarray) -> dict[str, object]:
    mat = np.asarray(mat, dtype=float)
    return {
        "type_id": "opencv-matrix",
        "rows": int(mat.shape[0]),
        "cols": int(mat.shape[1]),
        "dt": "d",
        "data": mat.reshape(-1).tolist(),
    }


def save_calibration_json(calibration: dict[str, np.ndarray], out_path: Path) -> None:
    ordered_keys = [
        "camera_intrinsic_matrix",
        "camera_distortion",
        "camera_image_size",
        "dlp_intrinsic_matrix",
        "dlp_distortion",
        "dlp_image_size",
        "dlp_rotation_matrix",
        "dlp_tanslation_matrix",
        "T_cam_2_proj",
    ]
    payload = {key: opencv_matrix(calibration[key]) for key in ordered_keys}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4)
        f.write("\n")


PLY_NORMAL_COLOR_PROPERTIES = (
    "property float x",
    "property float y",
    "property float z",
    "property float nx",
    "property float ny",
    "property float nz",
    "property uchar red",
    "property uchar green",
    "property uchar blue",
)
PLY_NORMAL_COLOR_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("nx", "<f4"),
        ("ny", "<f4"),
        ("nz", "<f4"),
        ("red", "u1"),
        ("green", "u1"),
        ("blue", "u1"),
    ]
)


def camera_to_world_points(points: np.ndarray, world_to_camera_rt: np.ndarray) -> np.ndarray:
    rotation = world_to_camera_rt[:, :3]
    translation = world_to_camera_rt[:, 3]
    return (points - translation) @ rotation


def camera_to_world_normals(normals: np.ndarray, world_to_camera_rt: np.ndarray) -> np.ndarray:
    rotation = world_to_camera_rt[:, :3]
    normals_world = normals @ rotation
    lengths = np.linalg.norm(normals_world, axis=1)
    valid = lengths > 1e-12
    normals_world[valid] = normals_world[valid] / lengths[valid, None]
    return normals_world


def read_normal_color_ply(path: Path) -> np.ndarray:
    with path.open("rb") as f:
        header_lines: list[str] = []
        vertex_count: int | None = None
        while True:
            raw_line = f.readline()
            if not raw_line:
                raise ValueError(f"PLY header missing end_header: {path}")
            line = raw_line.decode("ascii", errors="strict").strip()
            header_lines.append(line)
            if line.startswith("element vertex "):
                vertex_count = int(line.split()[-1])
            if line == "end_header":
                break

        if not header_lines or header_lines[0] != "ply":
            raise ValueError(f"Invalid PLY file: {path}")
        if "format binary_little_endian 1.0" not in header_lines:
            raise ValueError(f"Only binary_little_endian PLY is supported: {path}")
        if vertex_count is None:
            raise ValueError(f"PLY vertex count missing: {path}")

        properties = [line for line in header_lines if line.startswith("property ")]
        if tuple(properties[: len(PLY_NORMAL_COLOR_PROPERTIES)]) != PLY_NORMAL_COLOR_PROPERTIES:
            raise ValueError(
                f"PLY must contain x/y/z + nx/ny/nz + red/green/blue properties: {path}"
            )

        vertices = np.fromfile(f, dtype=PLY_NORMAL_COLOR_DTYPE, count=vertex_count)
        if vertices.shape[0] != vertex_count:
            raise ValueError(f"PLY payload truncated: {path}")
        return vertices.copy()


def transform_normal_color_ply_to_world(path: Path, world_to_camera_rt: np.ndarray) -> np.ndarray:
    vertices = read_normal_color_ply(path)
    points = np.column_stack((vertices["x"], vertices["y"], vertices["z"])).astype(np.float64)
    normals = np.column_stack((vertices["nx"], vertices["ny"], vertices["nz"])).astype(np.float64)
    points_world = camera_to_world_points(points, world_to_camera_rt)
    normals_world = camera_to_world_normals(normals, world_to_camera_rt)

    transformed = vertices.copy()
    transformed["x"] = points_world[:, 0].astype(np.float32)
    transformed["y"] = points_world[:, 1].astype(np.float32)
    transformed["z"] = points_world[:, 2].astype(np.float32)
    transformed["nx"] = normals_world[:, 0].astype(np.float32)
    transformed["ny"] = normals_world[:, 1].astype(np.float32)
    transformed["nz"] = normals_world[:, 2].astype(np.float32)
    return transformed


def write_normal_color_ply(path: Path, vertices: np.ndarray) -> None:
    vertices = np.asarray(vertices, dtype=PLY_NORMAL_COLOR_DTYPE)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "comment Generated by gwCamSDK batch_recon_blender_sim with world-space normal-color PLY contract\n"
        f"element vertex {vertices.shape[0]}\n"
        + "\n".join(PLY_NORMAL_COLOR_PROPERTIES)
        + "\nend_header\n"
    ).encode("ascii")
    with path.open("wb") as f:
        f.write(header)
        np.ascontiguousarray(vertices).tofile(f)


def write_merged_world_pointcloud(
    camera_pointclouds: list[tuple[str, Path, np.ndarray]],
    output_path: Path,
    dry_run: bool,
) -> None:
    if dry_run:
        inputs = ", ".join(camera_name for camera_name, _, _ in camera_pointclouds)
        log(f"[dry-run] merge world PLY ({inputs}) -> {output_path}")
        return
    if not camera_pointclouds:
        raise RuntimeError("No camera point clouds available for merged_world.ply")

    transformed_clouds = [
        transform_normal_color_ply_to_world(ply_path, world_to_camera_rt)
        for _, ply_path, world_to_camera_rt in camera_pointclouds
    ]
    merged = np.concatenate(transformed_clouds) if len(transformed_clouds) > 1 else transformed_clouds[0]
    write_normal_color_ply(output_path, merged)
    log(f"[merge] world pointcloud: {output_path} ({merged.shape[0]} points)")



def write_gray_sequence(frames: Iterable[Path], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for idx, src_path in enumerate(frames):
        img = cv2.imread(str(src_path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise RuntimeError(f"Cannot read image: {src_path}")
        if img.ndim == 3:
            if img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        out_path = out_dir / f"gray_{idx:05d}.bmp"
        if not cv2.imwrite(str(out_path), img):
            raise RuntimeError(f"Cannot write image: {out_path}")
        count += 1
    return count


def backup_path_for(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.unwritable-{timestamp}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.unwritable-{timestamp}-{suffix}")
        suffix += 1
    return candidate


def ensure_writable_directory(path: Path, dry_run: bool) -> None:
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"Output path exists but is not a directory: {path}")
        if os.access(path, os.W_OK | os.X_OK):
            return
        backup = backup_path_for(path)
        if dry_run:
            log(f"[dry-run] move unwritable directory {path} -> {backup}")
            return
        path.rename(backup)
        log(f"[warn] moved unwritable directory {path} -> {backup}")
    if not dry_run:
        path.mkdir(parents=True, exist_ok=True)


def has_unwritable_entries(path: Path) -> bool:
    if not os.access(path, os.W_OK | os.X_OK):
        return True
    for entry in path.rglob("*"):
        if entry.is_dir():
            if not os.access(entry, os.W_OK | os.X_OK):
                return True
        elif not os.access(entry, os.W_OK):
            return True
    return False


def backup_unwritable_tree(path: Path, dry_run: bool) -> None:
    if not path.exists() or not has_unwritable_entries(path):
        return
    backup = backup_path_for(path)
    if dry_run:
        log(f"[dry-run] move unwritable tree {path} -> {backup}")
        return
    path.rename(backup)
    log(f"[warn] moved unwritable tree {path} -> {backup}")


def read_cv_mat(fs: cv2.FileStorage, key: str) -> np.ndarray | None:
    node = fs.getNode(key)
    if node.empty():
        return None
    mat = node.mat()
    return None if mat is None or mat.size == 0 else mat


def scalar_coord_map_for_ref(rows: int, cols: int, ref: dict[str, object]) -> np.ndarray:
    axis_x = float(ref.get("axis_x", 1.0) or 0.0)
    axis_y = float(ref.get("axis_y", 0.0) or 0.0)
    min_projection = float(ref.get("min_projection", 0.0) or 0.0)
    xs = np.arange(cols, dtype=np.float64)[None, :]
    ys = np.arange(rows, dtype=np.float64)[:, None]
    coords = axis_x * xs + axis_y * ys - min_projection
    return np.ascontiguousarray(coords, dtype=np.float64)


def write_runtime_phase_ref(
    source_path: Path,
    output_path: Path,
    ref: dict[str, object],
) -> tuple[int, int]:
    fs = cv2.FileStorage(str(source_path), cv2.FILE_STORAGE_READ)
    if not fs.isOpened():
        raise RuntimeError(f"Cannot open phase reference file: {source_path}")
    try:
        phase_map = read_cv_mat(fs, "phase_map")
        phase_valid_mask = read_cv_mat(fs, "phase_valid_mask")
        scalar_coord_map = read_cv_mat(fs, "scalar_coord_map")
    finally:
        fs.release()

    if phase_map is None:
        raise RuntimeError(f"Missing phase_map in phase reference: {source_path}")
    if phase_map.dtype != np.float64:
        phase_map = phase_map.astype(np.float64)
    rows, cols = phase_map.shape[:2]

    if scalar_coord_map is None:
        scalar_coord_map = scalar_coord_map_for_ref(rows, cols, ref)
    elif scalar_coord_map.dtype != np.float64:
        scalar_coord_map = scalar_coord_map.astype(np.float64)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out = cv2.FileStorage(str(output_path), cv2.FILE_STORAGE_WRITE)
    if not out.isOpened():
        raise RuntimeError(f"Cannot write phase reference file: {output_path}")
    try:
        for key in (
            "angle_deg",
            "axis_x",
            "axis_y",
            "min_projection",
            "max_projection",
            "mapping_layout",
            "generation_layout",
            "phase_ref_layout",
        ):
            value = ref.get(key)
            if value is not None:
                out.write(key, value)
        out.write("phase_map", phase_map)
        if (
            phase_valid_mask is not None
            and phase_valid_mask.shape[:2] == phase_map.shape[:2]
            and phase_valid_mask.dtype == np.uint8
        ):
            out.write("phase_valid_mask", phase_valid_mask)
        out.write("scalar_coord_map", scalar_coord_map)
    finally:
        out.release()
    return cols, rows


def prepare_runtime_fringe_config(
    fringe_config: FringeConfig,
    runtime_dir: Path,
    root: Path,
    dry_run: bool,
) -> FringeConfig:
    runtime_code_config_path = runtime_dir / "code-config.json"
    if dry_run:
        log(
            f"[dry-run] prepare runtime fringe config "
            f"{to_project_relative(fringe_config.config_dir, root)} -> "
            f"{to_project_relative(runtime_dir, root)}"
        )
        return FringeConfig(runtime_code_config_path, runtime_dir, fringe_config.total_image_count)

    code_config = load_code_config(fringe_config.code_config_path)
    refs = code_config.get("angle_phase_refs")
    if not isinstance(refs, list) or not refs:
        raise RuntimeError(f"{fringe_config.code_config_path} has no angle_phase_refs")

    runtime_dir.mkdir(parents=True, exist_ok=True)
    projector_size: tuple[int, int] | None = None
    for idx, ref_obj in enumerate(refs):
        if not isinstance(ref_obj, dict):
            raise RuntimeError(f"Invalid angle_phase_refs[{idx}] in {fringe_config.code_config_path}")
        ref = ref_obj
        mapping_layout = str(ref.get("mapping_layout") or "rect")
        ref.setdefault("generation_layout", mapping_layout)
        ref.setdefault("phase_ref_layout", mapping_layout)
        ref["phase_ref_written"] = 1

        phase_ref_value = ref.get("phase_ref_path") or f"phase_ref_{idx:02d}.yml"
        phase_ref_path = Path(str(phase_ref_value))
        if not phase_ref_path.is_absolute():
            phase_ref_path = fringe_config.config_dir / phase_ref_path
        output_phase_ref = runtime_dir / f"phase_ref_{idx:02d}.yml"
        cols, rows = write_runtime_phase_ref(phase_ref_path, output_phase_ref, ref)
        projector_size = projector_size or (cols, rows)
        ref["phase_ref_path"] = output_phase_ref.name

    code_config.setdefault("projector_coord_domain", "rect_pixel")
    if not code_config.get("projector_coord_size"):
        if projector_size is None:
            raise RuntimeError(f"Cannot infer projector_coord_size from {fringe_config.config_dir}")
        code_config["projector_coord_size"] = [projector_size[0], projector_size[1]]

    with runtime_code_config_path.open("w", encoding="utf-8") as f:
        json.dump(code_config, f, indent=4, ensure_ascii=False)
        f.write("\n")

    return FringeConfig(runtime_code_config_path, runtime_dir, fringe_config.total_image_count)


def make_output_dir(args: argparse.Namespace, dataset: Dataset, fringe_config: FringeConfig, camera_name: str, root: Path) -> Path:
    if args.output_root:
        base = Path(args.output_root)
        if not base.is_absolute():
            base = root / base
        if dataset.batch_name:
            base = base / dataset.batch_name
        return base / dataset.object_name / dataset.dataset_name / fringe_config.config_dir.name / camera_name
    return (
        dataset.source_dir
        / "reconstruction"
        / fringe_config.config_dir.name
        / camera_name
    )


def load_config_template(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_reconstruction_config(
    config_template: dict[str, object],
    config_path: Path,
    root: Path,
    fringe_config: FringeConfig,
    calibration_path: Path,
    reconstruction_data_path: Path,
    args: argparse.Namespace,
) -> None:
    config = dict(config_template)
    config["debug"] = bool(args.debug)
    config["use_cuda"] = bool(args.use_cuda)
    config["modulation_threshold"] = float(args.modulation_threshold)
    config["sinusoidal_fit_threshold"] = float(args.sinusoidal_fit_threshold)
    config["fringe_code_config_path"] = to_project_relative(fringe_config.config_dir, root)
    config["calibration_res_path"] = to_project_relative(calibration_path, root)
    config["reconstruction_data_path"] = to_project_relative(reconstruction_data_path, root)

    with config_path.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
        f.write("\n")


def run_reconstruction(
    recon_bin: Path,
    config_path: Path,
    root: Path,
    env: dict[str, str],
    dry_run: bool,
) -> None:
    if dry_run:
        log(f"[dry-run] {to_project_relative(recon_bin, root)} {to_project_relative(config_path, root)}")
        return
    subprocess.run([str(recon_bin), str(config_path)], cwd=root, env=env, check=True)


def rename_reconstruction_outputs(camera_out_dir: Path, camera_name: str, dry_run: bool) -> None:
    output_map = {
        "depth_cpu.tiff": f"{camera_name}.tiff",
        "depth_cpu_vis.png": f"{camera_name}_vis.png",
        "disparity_cpu.tiff": f"{camera_name}_disparity.tiff",
        "disparity_cpu_vis.png": f"{camera_name}_disparity_vis.png",
        "modulation_cpu.tiff": f"{camera_name}_modulation.tiff",
        "modulation_cpu_vis.png": f"{camera_name}_modulation_vis.png",
        "sinusoidal_fit_cpu.tiff": f"{camera_name}_sinusoidal_fit.tiff",
        "sinusoidal_fit_cpu_vis.png": f"{camera_name}_sinusoidal_fit_vis.png",
        "pointcloud_cpu.ply": f"{camera_name}.ply",
    }
    for old_name, new_name in output_map.items():
        old_path = camera_out_dir / old_name
        new_path = camera_out_dir / new_name
        if dry_run:
            log(f"[dry-run] rename {old_path.name} -> {new_path.name}")
            continue
        if not old_path.exists():
            raise FileNotFoundError(f"Missing reconstruction output: {old_path}")
        old_path.replace(new_path)


def copy_reconstruction_outputs(camera_out_dir: Path, camera_name: str, dry_run: bool) -> None:
    recon_root = camera_out_dir.parent.parent
    depth_dir = recon_root / "fpp_depth"
    disparity_dir = recon_root / "fpp_disparity"
    modulation_dir = recon_root / "fpp_modulation"
    sinusoidal_fit_dir = recon_root / "fpp_sinusoidal_fit"
    pointcloud_dir = recon_root / "fpp_pointcloud"
    for output_dir in (
        depth_dir,
        disparity_dir,
        modulation_dir,
        sinusoidal_fit_dir,
        pointcloud_dir,
    ):
        ensure_writable_directory(output_dir, dry_run)
    copy_map = {
        camera_out_dir / f"{camera_name}.tiff": depth_dir / f"{camera_name}.tiff",
        camera_out_dir / f"{camera_name}_vis.png": depth_dir / f"{camera_name}_vis.png",
        camera_out_dir / f"{camera_name}_disparity.tiff": disparity_dir / f"{camera_name}.tiff",
        camera_out_dir / f"{camera_name}_disparity_vis.png": disparity_dir / f"{camera_name}_vis.png",
        camera_out_dir / f"{camera_name}_modulation.tiff": modulation_dir / f"{camera_name}.tiff",
        camera_out_dir / f"{camera_name}_modulation_vis.png": modulation_dir / f"{camera_name}_vis.png",
        camera_out_dir / f"{camera_name}_sinusoidal_fit.tiff": sinusoidal_fit_dir / f"{camera_name}.tiff",
        camera_out_dir / f"{camera_name}_sinusoidal_fit_vis.png": sinusoidal_fit_dir / f"{camera_name}_vis.png",
        camera_out_dir / f"{camera_name}.ply": pointcloud_dir / f"{camera_name}.ply",
    }
    for src_path, dst_path in copy_map.items():
        if dry_run:
            log(f"[dry-run] copy {src_path.name} -> {dst_path}")
            continue
        if not src_path.exists():
            raise FileNotFoundError(f"Missing reconstruction output: {src_path}")
        shutil.copy2(src_path, dst_path)


def process_dataset(
    dataset: Dataset,
    fringe_config: FringeConfig,
    config_template: dict[str, object],
    recon_bin: Path,
    root: Path,
    subprocess_env: dict[str, str],
    args: argparse.Namespace,
) -> None:
    params_path = dataset.source_dir / "gwcam-calib.json"
    if not params_path.exists():
        raise FileNotFoundError(f"Missing Blender params file: {params_path}")

    frames_by_camera = collect_frames(dataset.source_dir)
    calibrations = load_reconstruction_calibrations(params_path)

    log(
        f"[dataset] {to_project_relative(dataset.source_dir, root)} "
        f"-> {fringe_config.config_dir.name}"
    )

    merged_inputs: list[tuple[str, Path, np.ndarray]] = []
    merged_output_path: Path | None = None
    runtime_fringe_config: FringeConfig | None = None

    for camera_name in sorted(frames_by_camera):
        if camera_name not in calibrations:
            raise RuntimeError(f"{camera_name} has images but no calibration in {params_path}")

        camera_out_dir = make_output_dir(args, dataset, fringe_config, camera_name, root)
        if runtime_fringe_config is None:
            runtime_fringe_config = prepare_runtime_fringe_config(
                fringe_config,
                camera_out_dir.parent / "_fringe_config_runtime",
                root,
                args.dry_run,
            )
        calibration_path = camera_out_dir / "calibration.json"
        config_path = camera_out_dir / "reconstruction_config.json"

        log(f"[camera] {camera_name}: {len(frames_by_camera[camera_name])} frames -> {to_project_relative(camera_out_dir, root)}")
        if not args.dry_run:
            backup_unwritable_tree(camera_out_dir / "reconstruction_debug", args.dry_run)
            written = write_gray_sequence(frames_by_camera[camera_name], camera_out_dir)
            if written != fringe_config.total_image_count:
                raise RuntimeError(
                    f"{camera_name} wrote {written} images, but {fringe_config.config_dir.name} "
                    f"requires {fringe_config.total_image_count}"
                )
            save_calibration_json(calibrations[camera_name], calibration_path)
            save_reconstruction_config(
                config_template,
                config_path,
                root,
                runtime_fringe_config,
                calibration_path,
                camera_out_dir,
                args,
            )
        run_reconstruction(recon_bin, config_path, root, subprocess_env, args.dry_run)
        rename_reconstruction_outputs(camera_out_dir, camera_name, args.dry_run)
        copy_reconstruction_outputs(camera_out_dir, camera_name, args.dry_run)

        recon_root = camera_out_dir.parent.parent
        pointcloud_dir = recon_root / "fpp_pointcloud"
        merged_output_path = pointcloud_dir / "merged_world.ply"
        merged_inputs.append((
            camera_name,
            pointcloud_dir / f"{camera_name}.ply",
            calibrations[camera_name]["_camera_world_to_camera_rt"],
        ))

        if not args.dry_run:
            expected = camera_out_dir / f"{camera_name}.tiff"
            if not expected.exists():
                raise FileNotFoundError(f"Missing reconstruction output: {expected}")

    if merged_output_path is not None:
        write_merged_world_pointcloud(merged_inputs, merged_output_path, args.dry_run)


def main() -> int:
    args = parse_args()
    root = project_root()
    source_root = resolve_project_path(Path(args.source_root), root)
    if not source_root.is_dir():
        print(f"Source root does not exist: {source_root}", file=sys.stderr)
        return 1
    gwcam_root = resolve_project_path(Path(args.gwcam_root), root)
    if not gwcam_root.is_dir():
        print(f"gwCamSDK root does not exist: {gwcam_root}", file=sys.stderr)
        return 1
    fringe_root = resolve_project_path(Path(args.fringe_root), root)
    if not fringe_root.is_dir():
        print(f"Fringe root does not exist: {fringe_root}", file=sys.stderr)
        return 1
    source_batches = parse_source_batches(args.source_batches)

    recon_bin = resolve_gwcam_path(Path(args.recon_bin), gwcam_root)
    if not recon_bin.exists():
        print(f"Missing executable: {recon_bin}", file=sys.stderr)
        return 1

    config_template_path = resolve_gwcam_path(Path(args.config_template), gwcam_root)
    config_template = load_config_template(config_template_path)
    thirdparty_prefix = resolve_gwcam_path(Path(args.thirdparty_prefix), gwcam_root)
    subprocess_env = build_subprocess_env(gwcam_root, thirdparty_prefix)

    try:
        source_roots = find_batch_roots(source_root, source_batches)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    dataset_dirs: list[tuple[Path, Optional[Path]]] = []
    for batch_root in source_roots:
        batch_dataset_dirs = find_dataset_dirs(batch_root)
        if batch_dataset_dirs:
            dataset_dirs.extend((path, batch_root) for path in batch_dataset_dirs)
    if not dataset_dirs:
        print(f"No Blender projection datasets found under {source_root}", file=sys.stderr)
        return 1

    datasets = [build_dataset(path, batch_root) for path, batch_root in dataset_dirs]
    for dataset in datasets:
        if not args.fringe_config:
            choices = "\n".join(
                f"  - {config.config_dir.name} ({config.total_image_count} images)"
                for config in list_fringe_configs(fringe_root)
            )
            print(
                "Fringe config must be set manually. Edit FRINGE_CONFIG at the top of "
                "scripts/batch_recon_blender_sim.py or pass --fringe-config.\n"
                f"Available configs:\n{choices}",
                file=sys.stderr,
            )
            return 1
        fringe_config = resolve_named_fringe_config(args.fringe_config, root, fringe_root)
        if fringe_config.total_image_count != dataset.frame_count:
            raise RuntimeError(
                f"{fringe_config.config_dir.name} requires {fringe_config.total_image_count} images, "
                f"but {dataset.source_dir} has {dataset.frame_count}"
            )
        process_dataset(dataset, fringe_config, config_template, recon_bin, root, subprocess_env, args)

    log(f"[done] reconstructed {len(datasets)} dataset(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
