#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import os
from pathlib import Path
import sys

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import cv2
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from exr_io import read_scalar_float_exr


DEFAULT_NAME = "rect_dmd_blender"
DEFAULT_INPUT = REPO_ROOT / "output" / "calibration_circle_grid" / DEFAULT_NAME
DEFAULT_GLOB = "**/*_float.exr"
DEFAULT_SIGMA_PX = 0.8
DEFAULT_SIGMA_TAG = f"{DEFAULT_SIGMA_PX:g}".replace("-", "m").replace(".", "p")
DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT
    / "output"
    / "calibration_circle_grid"
    / f"{DEFAULT_NAME}_blur_s{DEFAULT_SIGMA_TAG}_bmp"
)
DEFAULT_SUFFIX = None
DEFAULT_IMAGE_FORMAT = "bmp"
DEFAULT_GAUSSIAN_BLUR = True
DEFAULT_WRITE_BMP = False
DEFAULT_BMP_ONLY = True
DEFAULT_BMP_NAME_MODE = "camera"
DEFAULT_CMOS_NOISE = True
DEFAULT_NOISE_K = 1e-4
DEFAULT_NOISE_C = 5e-8
DEFAULT_NOISE_SEED = None
DEFAULT_OVERWRITE = False
DEFAULT_DRY_RUN = False
DEFAULT_JOBS = 8
DEFAULT_OPENCV_THREADS = 1

@dataclass(frozen=True)
class BlurTask:
    input_path: Path
    output_path: Path
    image_output_path: Path
    gaussian_blur: bool
    sigma_px: float
    bmp_only: bool
    write_bmp: bool
    image_format: str
    bmp_name_mode: str
    cmos_noise: bool
    noise_k: float
    noise_c: float
    noise_seed: int | None
    overwrite: bool
    opencv_threads: int


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply a Gaussian PSF blur to float EXR render outputs. "
            "Install dependencies from requirements.txt."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        default=[str(DEFAULT_INPUT)],
        help=(
            "Input EXR file(s) or directory/directories. Project-relative paths "
            "starting with // are resolved from the repository root. "
            f"Default: //output/calibration_circle_grid/{DEFAULT_NAME}"
        ),
    )
    parser.add_argument(
        "--gaussian-blur",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_GAUSSIAN_BLUR,
        help=(
            "Apply Gaussian PSF blur before quantization. Disabling blur still "
            "allows 8-bit output and sensor noise, default: "
            f"{DEFAULT_GAUSSIAN_BLUR}."
        ),
    )
    parser.add_argument(
        "--sigma-px",
        type=float,
        default=DEFAULT_SIGMA_PX,
        help=f"Gaussian PSF sigma in image pixels, default: {DEFAULT_SIGMA_PX:g}.",
    )
    parser.add_argument(
        "--glob",
        default=DEFAULT_GLOB,
        help=f"Directory input glob, default: {DEFAULT_GLOB}",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help=(
            "Optional output root. When set, directory structure is mirrored "
            "under this root. Default: //output/calibration_circle_grid/"
            "<input-name>_blur_s<sigma>_<image-format>."
        ),
    )
    parser.add_argument(
        "--suffix",
        default=DEFAULT_SUFFIX,
        help=(
            "Output suffix before .exr. Default: _blur_s<sigma>, for example "
            "_blur_s1p5."
        ),
    )
    parser.add_argument(
        "--write-bmp",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_WRITE_BMP,
        help=(
            "Also write a clamped 8-bit capture image next to each blurred "
            f"EXR output, default: {DEFAULT_WRITE_BMP}."
        ),
    )
    parser.add_argument(
        "--bmp-only",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_BMP_ONLY,
        help=(
            "Write only clamped 8-bit capture-image outputs and skip blurred "
            f"EXR outputs, default: {DEFAULT_BMP_ONLY}."
        ),
    )
    parser.add_argument(
        "--image-format",
        choices=("bmp", "png"),
        default=DEFAULT_IMAGE_FORMAT,
        help=(
            "8-bit capture image format written by --write-bmp/--bmp-only. "
            "The legacy BMP flag names are kept for compatibility. "
            f"Default: {DEFAULT_IMAGE_FORMAT}."
        ),
    )
    parser.add_argument(
        "--bmp-name-mode",
        choices=("blurred", "camera"),
        default=DEFAULT_BMP_NAME_MODE,
        help=(
            "8-bit capture-image output naming. 'blurred' writes "
            "<input-stem><suffix>.<image-format>; 'camera' writes "
            "camera-style names such as camera_000.<image-format> for "
            f"camera_000_float.exr inputs. Default: {DEFAULT_BMP_NAME_MODE}."
        ),
    )
    parser.add_argument(
        "--cmos-noise",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_CMOS_NOISE,
        help=(
            "Add simplified CMOS noise to 8-bit capture-image outputs after "
            f"Gaussian blur and quantization, default: {DEFAULT_CMOS_NOISE}."
        ),
    )
    parser.add_argument(
        "--noise-k",
        type=float,
        default=DEFAULT_NOISE_K,
        help=(
            "Signal-dependent CMOS noise coefficient used by --cmos-noise, "
            f"default: {DEFAULT_NOISE_K:g}."
        ),
    )
    parser.add_argument(
        "--noise-c",
        type=float,
        default=DEFAULT_NOISE_C,
        help=(
            "Signal-independent CMOS noise floor coefficient used by "
            f"--cmos-noise, default: {DEFAULT_NOISE_C:g}."
        ),
    )
    parser.add_argument(
        "--noise-seed",
        type=int,
        default=DEFAULT_NOISE_SEED,
        help=(
            "Optional base random seed for reproducible CMOS noise. Each input "
            "image derives a different seed from this base seed."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_OVERWRITE,
        help=f"Overwrite existing output files, default: {DEFAULT_OVERWRITE}.",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_DRY_RUN,
        help=f"Print planned outputs without reading or writing images, default: {DEFAULT_DRY_RUN}.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"Number of worker processes, default: {DEFAULT_JOBS}.",
    )
    parser.add_argument(
        "--opencv-threads",
        type=int,
        default=DEFAULT_OPENCV_THREADS,
        help=(
            "OpenCV threads per worker process. Keep this low when --jobs > 1, "
            f"default: {DEFAULT_OPENCV_THREADS}."
        ),
    )
    return parser


def resolve_project_path(raw_path: str | Path) -> Path:
    raw = str(raw_path)
    if raw.startswith("//"):
        return (REPO_ROOT / raw[2:]).resolve()
    return Path(raw).expanduser().resolve()


def sigma_tag(sigma_px: float) -> str:
    return f"{sigma_px:g}".replace("-", "m").replace(".", "p")


def default_output_root_for(
    sigma_px: float,
    image_format: str,
    gaussian_blur: bool = True,
) -> Path:
    processing_tag = f"blur_s{sigma_tag(sigma_px)}" if gaussian_blur else "no_blur"
    return (
        REPO_ROOT
        / "output"
        / "calibration_circle_grid"
        / f"{DEFAULT_NAME}_{processing_tag}_{image_format}"
    )


def collect_inputs(raw_inputs: list[str], glob_pattern: str) -> list[tuple[Path, Path]]:
    result: list[tuple[Path, Path]] = []
    for raw_input in raw_inputs:
        input_path = resolve_project_path(raw_input)
        if input_path.is_file():
            result.append((input_path, input_path.parent))
            continue
        if input_path.is_dir():
            matches = sorted(path for path in input_path.glob(glob_pattern) if path.is_file())
            result.extend((path, input_path) for path in matches)
            continue
        raise FileNotFoundError(f"Input path does not exist: {input_path}")
    return result


def output_path_for(
    input_path: Path,
    input_root: Path,
    output_root: Path | None,
    suffix: str,
) -> Path:
    output_name = f"{input_path.stem}{suffix}.exr"
    if output_root is None:
        return input_path.with_name(output_name)

    try:
        rel_parent = input_path.parent.relative_to(input_root)
    except ValueError:
        rel_parent = Path()
    return output_root / rel_parent / output_name


def image_output_path_for(
    input_path: Path,
    input_root: Path,
    output_root: Path | None,
    suffix: str,
    name_mode: str,
    image_format: str,
) -> Path:
    stem = input_path.stem
    extension = f".{image_format}"
    if name_mode == "camera" and stem.endswith("_float"):
        output_name = f"{stem[:-len('_float')]}{extension}"
    elif name_mode == "camera":
        output_name = f"{stem}{extension}"
    else:
        output_name = f"{stem}{suffix}{extension}"

    if output_root is None:
        return input_path.with_name(output_name)

    try:
        rel_parent = input_path.parent.relative_to(input_root)
    except ValueError:
        rel_parent = Path()
    return output_root / rel_parent / output_name


def apply_gaussian_psf(img: np.ndarray, sigma_px: float) -> np.ndarray:
    return cv2.GaussianBlur(
        img,
        ksize=(0, 0),
        sigmaX=sigma_px,
        sigmaY=sigma_px,
        borderType=cv2.BORDER_REFLECT,
    )


def write_exr(path: Path, img: np.ndarray, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output exists, pass --overwrite to replace: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), img.astype(np.float32, copy=False)):
        raise RuntimeError(f"Cannot write EXR: {path}")


def float_to_bmp_uint8(img: np.ndarray) -> np.ndarray:
    bmp = np.clip(img, 0.0, 1.0)
    return np.rint(bmp * 255.0).astype(np.uint8)


def add_cmos_noise_8bit(
    img8: np.ndarray,
    k: float,
    c: float,
    seed: int | None,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = img8.astype(np.float32)
    img_norm = np.clip(img / 255.0, 0.0, 1.0)
    noise_var = np.maximum(k * img_norm + c, 0.0)
    noise_std_dn = 255.0 * np.sqrt(noise_var)
    noise = noise_std_dn * rng.normal(0.0, 1.0, img.shape)
    noisy = np.clip(img + noise, 0.0, 255.0)
    return np.rint(noisy).astype(np.uint8)


def derive_noise_seed(base_seed: int | None, task_index: int) -> int | None:
    if base_seed is None:
        return None
    seed_seq = np.random.SeedSequence([int(base_seed), int(task_index)])
    return int(seed_seq.generate_state(1, dtype=np.uint32)[0])


def write_8bit_image(
    path: Path,
    img: np.ndarray,
    overwrite: bool,
    cmos_noise: bool,
    noise_k: float,
    noise_c: float,
    noise_seed: int | None,
    image_format: str,
) -> None:
    image_path = path
    if image_path.exists() and not overwrite:
        raise FileExistsError(f"Output exists, pass --overwrite to replace: {image_path}")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    capture_image = float_to_bmp_uint8(img)
    if cmos_noise:
        capture_image = add_cmos_noise_8bit(capture_image, noise_k, noise_c, noise_seed)
    if not cv2.imwrite(str(image_path), capture_image):
        raise RuntimeError(f"Cannot write {image_format.upper()}: {image_path}")


def process_task(task: BlurTask) -> str:
    if task.opencv_threads > 0:
        cv2.setNumThreads(task.opencv_threads)

    img = read_scalar_float_exr(task.input_path)
    processed = apply_gaussian_psf(img, task.sigma_px) if task.gaussian_blur else img

    if not task.bmp_only:
        write_exr(task.output_path, processed, task.overwrite)
    if task.write_bmp or task.bmp_only:
        write_8bit_image(
            task.image_output_path,
            processed,
            task.overwrite,
            task.cmos_noise,
            task.noise_k,
            task.noise_c,
            task.noise_seed,
            task.image_format,
        )

    if task.bmp_only:
        return f"{task.input_path} -> {task.image_output_path}"
    if task.write_bmp:
        return f"{task.input_path} -> {task.output_path} + {task.image_output_path}"
    return f"{task.input_path} -> {task.output_path}"


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.gaussian_blur and args.sigma_px <= 0.0:
        parser.error("--sigma-px must be > 0 when --gaussian-blur is enabled")
    if args.sigma_px < 0.0:
        parser.error("--sigma-px must be >= 0")
    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
    if args.opencv_threads < 0:
        parser.error("--opencv-threads must be >= 0")
    if args.noise_k < 0.0:
        parser.error("--noise-k must be >= 0")
    if args.noise_c < 0.0:
        parser.error("--noise-c must be >= 0")

    suffix = args.suffix or (
        f"_blur_s{sigma_tag(args.sigma_px)}" if args.gaussian_blur else "_no_blur"
    )
    output_root = (
        resolve_project_path(args.output_root)
        if args.output_root
        else default_output_root_for(
            args.sigma_px,
            args.image_format,
            args.gaussian_blur,
        )
    )
    inputs = collect_inputs(args.inputs, args.glob)

    if not inputs:
        print(f"[Blur] No inputs found. glob={args.glob}")
        return 0

    print(
        f"[Blur] gaussian_blur={str(args.gaussian_blur).lower()} "
        f"sigma_px={args.sigma_px:g} inputs={len(inputs)} "
        f"image_format={args.image_format} jobs={args.jobs} "
        f"opencv_threads={args.opencv_threads}"
    )
    if args.cmos_noise:
        print(
            f"[Blur] cmos_noise=true noise_k={args.noise_k:g} "
            f"noise_c={args.noise_c:g} noise_seed={args.noise_seed}"
        )
    tasks: list[BlurTask] = []
    for task_index, (input_path, input_root) in enumerate(inputs):
        output_path = output_path_for(input_path, input_root, output_root, suffix)
        image_output_path = image_output_path_for(
            input_path,
            input_root,
            output_root,
            suffix,
            args.bmp_name_mode,
            args.image_format,
        )
        if not args.bmp_only and output_path == input_path:
            raise RuntimeError(f"Refusing to overwrite input without a distinct output path: {input_path}")
        if args.dry_run:
            if args.bmp_only:
                print(f"[DRY] {input_path} -> {image_output_path}")
            elif args.write_bmp:
                print(f"[DRY] {input_path} -> {output_path} + {image_output_path}")
            else:
                print(f"[DRY] {input_path} -> {output_path}")
            continue

        tasks.append(
            BlurTask(
                input_path=input_path,
                output_path=output_path,
                image_output_path=image_output_path,
                gaussian_blur=args.gaussian_blur,
                sigma_px=args.sigma_px,
                bmp_only=args.bmp_only,
                write_bmp=args.write_bmp,
                image_format=args.image_format,
                bmp_name_mode=args.bmp_name_mode,
                cmos_noise=args.cmos_noise,
                noise_k=args.noise_k,
                noise_c=args.noise_c,
                noise_seed=derive_noise_seed(args.noise_seed, task_index),
                overwrite=args.overwrite,
                opencv_threads=args.opencv_threads,
            )
        )

    if args.dry_run:
        return 0

    failures = 0
    completed = 0
    if args.jobs == 1:
        for task in tasks:
            completed += 1
            try:
                detail = process_task(task)
            except Exception as exc:
                failures += 1
                print(f"[{completed}/{len(tasks)}] FAILED {task.input_path}: {exc}")
            else:
                print(f"[{completed}/{len(tasks)}] {detail}")
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as executor:
            future_map = {executor.submit(process_task, task): task for task in tasks}
            for future in as_completed(future_map):
                task = future_map[future]
                completed += 1
                try:
                    detail = future.result()
                except Exception as exc:
                    failures += 1
                    print(f"[{completed}/{len(tasks)}] FAILED {task.input_path}: {exc}")
                else:
                    print(f"[{completed}/{len(tasks)}] {detail}")

    if failures:
        print(f"[Blur] failed={failures} succeeded={len(tasks) - failures}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
