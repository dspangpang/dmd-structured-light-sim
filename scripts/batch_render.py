#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import difflib
import os
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from render_manifest import completed_render_manifest


MODELS_DIR = REPO_ROOT / "assets" / "models"
FRINGES_DIR = REPO_ROOT / "assets" / "fringes"
RUNNER = REPO_ROOT / "scripts" / "run_blender.sh"
STATUS_FILE_NAME = "batch_status.txt"


@dataclass(frozen=True)
class RenderTask:
    model_name: str
    model_path: str
    fringe_name: str
    fringe_path: str
    save_depth: bool
    model_output: Path
    task_output: Path
    cmd: list[str]


def available_model_names() -> list[str]:
    if not MODELS_DIR.is_dir():
        return []
    return sorted(
        path.name for path in MODELS_DIR.iterdir() if path.is_dir()
    )


def available_fringe_names() -> list[str]:
    if not FRINGES_DIR.is_dir():
        return []
    return sorted(
        path.name for path in FRINGES_DIR.iterdir() if path.exists()
    )


def build_not_found_error(kind: str, value: str, candidates: list[str], hint: str) -> FileNotFoundError:
    matches = difflib.get_close_matches(value, candidates, n=3, cutoff=0.5)
    extra = f" Did you mean: {', '.join(matches)}?" if matches else ""
    return FileNotFoundError(f"{kind} not found: {value}. {hint}{extra}")


def parse_list(raw: list[str]) -> list[str]:
    items: list[str] = []
    for chunk in raw:
        for part in chunk.split(","):
            item = part.strip()
            if item:
                items.append(item)
    return items


def parse_bool(raw: str) -> bool:
    norm = raw.strip().lower()
    if norm in {"1", "true", "yes", "y", "on"}:
        return True
    if norm in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {raw}")


def resolve_model(model: str) -> tuple[str, str]:
    candidate = Path(model)
    if candidate.exists():
        path = candidate.resolve()
        return path.stem, str(path)

    model_dir = MODELS_DIR / model
    if model_dir.is_dir():
        obj_files = sorted(model_dir.glob("*.obj"))
        if not obj_files:
            raise FileNotFoundError(f"No .obj file found under model directory: {model_dir}")
        return model_dir.name, f"//assets/models/{model_dir.name}/{obj_files[0].name}"

    if not model_dir.suffix:
        nested_obj = MODELS_DIR / model / f"{model}.obj"
        if nested_obj.exists():
            return Path(model).name, f"//assets/models/{model}/{nested_obj.name}"

    raise build_not_found_error(
        "Model",
        model,
        available_model_names(),
        "Expected an existing path or assets/models/<name>/<name>.obj.",
    )


def resolve_fringe(fringe: str) -> tuple[str, str]:
    candidate = Path(fringe)
    if candidate.exists():
        path = candidate.resolve()
        return path.stem if path.is_file() else path.name, str(path)

    fringe_dir = FRINGES_DIR / fringe
    if fringe_dir.exists():
        return fringe_dir.name, f"//assets/fringes/{fringe_dir.name}"

    raise build_not_found_error(
        "Fringe",
        fringe,
        available_fringe_names(),
        "Expected an existing path or assets/fringes/<name>.",
    )


def resolve_fringe_spec(spec: str) -> tuple[str, str, bool]:
    fringe_raw = spec
    save_depth = False

    if ":" in spec:
        fringe_raw, depth_raw = spec.rsplit(":", 1)
        fringe_raw = fringe_raw.strip()
        save_depth = parse_bool(depth_raw)

    fringe_name, fringe_path = resolve_fringe(fringe_raw)
    return fringe_name, fringe_path, save_depth


def load_txt_config(config_path: Path) -> tuple[list[str], list[str], Path, list[str], int | None]:
    section = None
    models: list[str] = []
    fringes: list[str] = []
    extra_args: list[str] = []
    output_root = Path("output/batch")
    jobs: int | None = None

    with config_path.open("r", encoding="utf-8") as fh:
        for lineno, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                continue

            if section == "models":
                models.append(line)
                continue

            if section == "fringes":
                parts = line.rsplit(maxsplit=1)
                if len(parts) == 2 and parts[1].lower() in {
                    "1",
                    "0",
                    "true",
                    "false",
                    "yes",
                    "no",
                    "on",
                    "off",
                    "y",
                    "n",
                }:
                    fringes.append(f"{parts[0]}:{parts[1]}")
                else:
                    fringes.append(f"{line}:false")
                continue

            if section == "extra_args":
                extra_args.extend(shlex.split(line))
                continue

            if section == "settings":
                if "=" not in line:
                    raise ValueError(
                        f"Invalid settings entry at {config_path}:{lineno}: {line}"
                    )
                key, value = [item.strip() for item in line.split("=", 1)]
                if key == "output_root":
                    output_root = Path(value)
                    continue
                if key == "jobs":
                    jobs = int(value)
                    continue
                raise ValueError(
                    f"Unsupported settings key at {config_path}:{lineno}: {key}"
                )

            raise ValueError(
                f"Line outside supported section at {config_path}:{lineno}: {line}"
            )

    if not models:
        raise ValueError(f"No models defined in config: {config_path}")
    if not fringes:
        raise ValueError(f"No fringes defined in config: {config_path}")

    return models, fringes, output_root, extra_args, jobs


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_status(task: RenderTask, status: str, note: str = "") -> None:
    task.task_output.mkdir(parents=True, exist_ok=True)
    lines = [
        f"status={status}",
        f"model={task.model_name}",
        f"fringe={task.fringe_name}",
        f"save_depth={'true' if task.save_depth else 'false'}",
        f"updated_at={now_utc()}",
        f"command={shlex.join(task.cmd)}",
    ]
    if note:
        lines.append(f"note={note}")
    (task.task_output / STATUS_FILE_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_status(task_output: Path) -> dict[str, str]:
    status_path = task_output / STATUS_FILE_NAME
    if not status_path.exists():
        return {}

    result: dict[str, str] = {}
    for raw_line in status_path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def is_completed(task: RenderTask) -> bool:
    status = read_status(task.task_output)
    if status.get("status") != "SUCCESS":
        return False
    complete, _reason = completed_render_manifest(task.task_output)
    return complete


def run_task(task: RenderTask, dry_run: bool, lock: threading.Lock) -> tuple[str, RenderTask, str]:
    header = (
        f"model={task.model_name} fringe={task.fringe_name} "
        f"save_depth={task.save_depth}"
    )
    with lock:
        print(header)
        print(shlex.join(task.cmd))

    if dry_run:
        return "DRY_RUN", task, ""

    write_status(task, "RUNNING")
    try:
        subprocess.run(task.cmd, cwd=REPO_ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        write_status(task, "FAILED", note=f"returncode={exc.returncode}")
        return "FAILED", task, f"returncode={exc.returncode}"

    complete, reason = completed_render_manifest(task.task_output)
    if not complete:
        write_status(task, "FAILED", note=reason)
        return "FAILED", task, reason

    write_status(task, "SUCCESS")
    return "SUCCESS", task, ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch render all model x fringe combinations."
    )
    parser.add_argument(
        "--config",
        help="Path to a txt config file. When provided, models/fringes are read from it.",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        help="Model names or paths. Comma-separated values are supported.",
    )
    parser.add_argument(
        "--fringes",
        nargs="+",
        help=(
            "Fringe specs. Format: <name>[:true|false]. "
            "Examples: 80px_7rot_1920x1080:false 80px_27rot_1920x1080:true"
        ),
    )
    parser.add_argument(
        "--output-root",
        default="output/batch",
        help="Base output directory for batch results.",
    )
    parser.add_argument(
        "--scene-config",
        type=Path,
        default=(
            Path(os.environ["GWCAM_STRUCTURED_LIGHT_SCENE_CONFIG"])
            if os.environ.get("GWCAM_STRUCTURED_LIGHT_SCENE_CONFIG")
            else None
        ),
        help="Current StructuredLightViewer JSON shared by every task in this batch.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing Blender.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Number of concurrent Blender tasks to run.",
    )
    parser.add_argument(
        "--rerun-failed",
        action="store_true",
        help="Rerun tasks whose status file says FAILED.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rerun all tasks even if their status file says SUCCESS.",
    )
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Extra args passed through to Blender after '--'.",
    )
    args = parser.parse_args()

    cli_extra_args = list(args.extra_args)
    if cli_extra_args and cli_extra_args[0] == "--":
        cli_extra_args = cli_extra_args[1:]

    raw_models: list[str]
    raw_fringes: list[str]
    output_root = Path(args.output_root)
    extra_args: list[str] = []
    jobs = args.jobs

    if args.config:
        config_path = Path(args.config)
        raw_models, raw_fringes, cfg_output_root, cfg_extra_args, cfg_jobs = load_txt_config(config_path)
        output_root = cfg_output_root
        extra_args.extend(cfg_extra_args)
        if jobs is None:
            jobs = cfg_jobs
    else:
        if not args.models or not args.fringes:
            parser.error("Either provide --config, or provide both --models and --fringes.")
        raw_models = parse_list(args.models)
        raw_fringes = parse_list(args.fringes)

    extra_args.extend(cli_extra_args)
    if args.scene_config is None:
        parser.error(
            "--scene-config (or GWCAM_STRUCTURED_LIGHT_SCENE_CONFIG) is required"
        )
    scene_config = args.scene_config.expanduser().resolve()
    if not scene_config.is_file():
        parser.error(f"scene config does not exist: {scene_config}")
    models = [resolve_model(item) for item in raw_models]
    fringes = [resolve_fringe_spec(item) for item in raw_fringes]

    if jobs is None:
        jobs = 1

    if jobs < 1:
        parser.error("--jobs must be >= 1")

    tasks: list[RenderTask] = []
    for model_name, model_path in models:
        model_output = output_root / model_name
        for fringe_name, fringe_path, save_depth in fringes:
            task_output = model_output / f"{fringe_name}_{model_name}"
            cmd = [
                str(RUNNER),
                "--",
                "--scene-config",
                str(scene_config),
                "--target",
                model_path,
                "--img",
                fringe_path,
                "--out",
                str(model_output),
                "--save-depth",
                "true" if save_depth else "false",
                *extra_args,
            ]
            tasks.append(
                RenderTask(
                    model_name=model_name,
                    model_path=model_path,
                    fringe_name=fringe_name,
                    fringe_path=fringe_path,
                    save_depth=save_depth,
                    model_output=model_output,
                    task_output=task_output,
                    cmd=cmd,
                )
            )

    seen_outputs: dict[Path, tuple[str, str, bool]] = {}
    runnable: list[RenderTask] = []
    skipped = 0
    for task in tasks:
        request_identity = (task.model_path, task.fringe_path, task.save_depth)
        previous_request = seen_outputs.get(task.task_output)
        if previous_request is not None:
            if previous_request != request_identity:
                parser.error(
                    "Different batch requests map to the same output directory: "
                    f"{task.task_output}. Previous={previous_request}, "
                    f"current={request_identity}. Use unique model/fringe names and "
                    "choose only one depth mode per output."
                )
            continue
        seen_outputs[task.task_output] = request_identity

        status = read_status(task.task_output)
        if not args.force and is_completed(task):
            skipped += 1
            print(
                f"[SKIP] model={task.model_name} fringe={task.fringe_name} "
                f"save_depth={task.save_depth} reason=already_success"
            )
            continue
        if not args.force and status.get("status") == "SUCCESS":
            print(
                f"[STALE] model={task.model_name} fringe={task.fringe_name} "
                "reason=SUCCESS status has no complete render manifest"
            )
        if not args.force and not args.rerun_failed and status.get("status") == "FAILED":
            skipped += 1
            print(
                f"[SKIP] model={task.model_name} fringe={task.fringe_name} "
                f"save_depth={task.save_depth} reason=previous_failed"
            )
            continue
        runnable.append(task)

    total = len(runnable)
    print(
        f"[Batch] total={len(tasks)} runnable={total} skipped={skipped} jobs={jobs}"
    )
    if total == 0:
        return 0

    lock = threading.Lock()
    failures = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        future_map = {
            executor.submit(run_task, task, args.dry_run, lock): task for task in runnable
        }
        for future in as_completed(future_map):
            status, task, detail = future.result()
            completed += 1
            prefix = f"[{completed}/{total}]"
            if status == "FAILED":
                failures += 1
                print(
                    f"{prefix} FAILED model={task.model_name} fringe={task.fringe_name} {detail}"
                )
            else:
                print(
                    f"{prefix} {status} model={task.model_name} fringe={task.fringe_name}"
                )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
