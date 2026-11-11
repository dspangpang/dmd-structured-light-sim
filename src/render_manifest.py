"""Transactional provenance manifest for every Blender render entry."""

from pixel_coordinates import PIXEL_COORDINATE_CONTRACT
from calibration import CALIBRATION_SCHEMA

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


MANIFEST_SCHEMA = "core-sim.render_run.v1"
MANIFEST_NAME = "render_manifest.json"


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_record(path):
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Render input not found: {resolved}")
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _resolve_repo_path(path, repo_root):
    text = str(path or "")
    if text.startswith("//"):
        return Path(repo_root) / text[2:]
    candidate = Path(text).expanduser()
    return candidate if candidate.is_absolute() else Path(repo_root) / candidate


def _render_config_snapshot(cfg):
    excluded = {
        "OUTPUT_DIR",
        "ACTUAL_CYCLES_DEVICE",
        "ACTUAL_COMPOSITOR_DEVICE",
        "NO_RENDER",
        "METADATA_ONLY",
        "APP_SCENE_CONFIG_PATH",
        "APP_SCENE_CONFIG_AUTHORITY",
        "RENDER_REQUEST_PATH",
        "RENDER_REQUEST_SCHEMA",
        "RENDER_REQUEST_SHA256",
    }
    snapshot = {}
    for key, value in vars(cfg).items():
        if key in excluded or key.startswith("APP_"):
            continue
        try:
            json.dumps(value)
        except TypeError:
            snapshot[key] = repr(value)
        else:
            snapshot[key] = value
    from projector_psf import parse_settings, implementation_sha256
    if getattr(cfg, "PROJECTOR_PSF", None) is not None:
        snapshot["PROJECTOR_PSF"] = parse_settings(cfg.PROJECTOR_PSF)
        snapshot["PROJECTOR_PSF_IMPLEMENTATION_SHA256"] = implementation_sha256()
    return snapshot


def _git_provenance(repo_root):
    root = Path(repo_root).resolve()

    def run(*args):
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {
        "commit": commit,
        "dirty": bool(status),
    }


def _atomic_write_json(path, payload):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class RenderRun:
    """Mark a run RUNNING first and COMPLETE only after artifact validation."""

    def __init__(
        self,
        output_dir,
        cfg,
        *,
        repo_root,
        variant_name,
        run_type,
        projector_images=(),
        target_path=None,
        blender_version="",
        extra_request=None,
    ):
        self.cfg = cfg
        self.output_dir = Path(output_dir).resolve()
        self.path = self.output_dir / MANIFEST_NAME
        scene_record = {
            "path": str(Path(cfg.APP_SCENE_CONFIG_PATH).resolve()),
            "sha256": str(cfg.APP_SCENE_CONFIG_SHA256),
            "schema": str(cfg.APP_SCENE_CONFIG_SCHEMA),
        }
        auxiliary_inputs = {}
        for enabled_field, path_field, label in (
            ("USE_CAMERA_MODEL", "CAMERA_MODEL_PATH", "camera_model"),
            ("USE_PROJECTOR_MODEL", "PROJECTOR_MODEL_PATH", "projector_model"),
        ):
            if bool(getattr(cfg, enabled_field, False)):
                path = _resolve_repo_path(getattr(cfg, path_field, ""), repo_root)
                auxiliary_inputs[label] = _file_record(path)

        request = {
            "pixel_coordinate_contract": PIXEL_COORDINATE_CONTRACT,
            "calibration_schema": CALIBRATION_SCHEMA,
            "variant": str(variant_name),
            "run_type": str(run_type),
            "execution_mode": (
                "metadata_only"
                if bool(getattr(cfg, "METADATA_ONLY", False))
                else "validate_only"
                if bool(getattr(cfg, "NO_RENDER", False))
                else "render"
            ),
            "scene": scene_record,
            "render_request": (
                {
                    "path": str(Path(cfg.RENDER_REQUEST_PATH).resolve()),
                    "sha256": str(cfg.RENDER_REQUEST_SHA256),
                    "schema": str(cfg.RENDER_REQUEST_SCHEMA),
                }
                if str(getattr(cfg, "RENDER_REQUEST_PATH", "") or "")
                else None
            ),
            "projector_images": [_file_record(path) for path in projector_images],
            "target": _file_record(target_path) if target_path else None,
            "auxiliary_inputs": auxiliary_inputs,
            "render_config": _render_config_snapshot(cfg),
            "render_options": {
                "preset": str(cfg.RENDER_PRESET),
                "engine": str(cfg.ENGINE),
                "samples": int(cfg.SAMPLES),
                "capture_mode": str(cfg.CAPTURE_MODE),
                "image_format": str(cfg.PROJECTOR_CAPTURE_IMAGE_FORMAT),
                "save_depth": bool(cfg.SAVE_DEPTH),
                "save_pointcloud": bool(cfg.SAVE_POINTCLOUD),
            },
        }
        if extra_request:
            request["entry"] = dict(extra_request)
        self.payload = {
            "schema": MANIFEST_SCHEMA,
            "status": "PENDING",
            "request_sha256": _canonical_sha256(request),
            "request": request,
            "runtime": {
                "blender_version": str(blender_version),
                "git": _git_provenance(repo_root),
            },
        }
        self.finalized = False

    def __enter__(self):
        if self.output_dir.exists() and not self.path.is_file():
            ignored = {".DS_Store", "batch_status.txt"}
            entries = [
                entry for entry in self.output_dir.iterdir() if entry.name not in ignored
            ]
            if entries:
                raise ValueError(
                    "Output directory contains legacy or partial artifacts without "
                    f"{MANIFEST_NAME}: {self.output_dir}. Choose a new --out or archive "
                    "the old result before rerunning."
                )
        if self.path.is_file():
            existing = json.loads(self.path.read_text(encoding="utf-8"))
            if existing.get("request", {}).get("calibration_schema") != CALIBRATION_SCHEMA:
                raise ValueError("Legacy calibration output cannot be resumed; regenerate calibration.json")
            if existing.get("request", {}).get("pixel_coordinate_contract") != PIXEL_COORDINATE_CONTRACT:
                raise ValueError(
                    "Output directory uses a legacy or incompatible pixel coordinate "
                    "contract; choose a new output directory and rerender: "
                    f"{self.output_dir}"
                )
            if existing.get("request_sha256") != self.payload["request_sha256"]:
                raise ValueError(
                    "Output directory belongs to a different render request: "
                    f"{self.output_dir}"
                )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.payload["status"] = "RUNNING"
        self.payload["started_at"] = _utc_now()
        _atomic_write_json(self.path, self.payload)
        return self

    def _camera_artifacts(self, camera_names):
        names = {str(name) for name in camera_names}
        records = []
        for path in sorted(self.output_dir.rglob("*")):
            if path.is_file() and path.stem in names:
                records.append(
                    {
                        "path": path.relative_to(self.output_dir).as_posix(),
                        "size_bytes": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                )
        return records

    def _extra_artifacts(self, groups):
        if groups is None:
            return {}
        if not isinstance(groups, dict):
            raise ValueError("Manifest artifact groups must be an object")
        reserved = {"camera_files", "camera_file_count", "expected_minimum"}
        normalized = {}
        for group_name, records in groups.items():
            group_name = str(group_name)
            if group_name in reserved:
                raise ValueError(
                    f"Manifest artifact group uses reserved name: {group_name}"
                )
            if not isinstance(records, (list, tuple)):
                raise ValueError(
                    f"Manifest artifact group {group_name!r} must be a list"
                )
            normalized_records = []
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError(
                        f"Manifest artifact record in {group_name!r} must be an object"
                    )
                relative = str(record.get("path", "") or "")
                candidate = Path(relative)
                if not relative or candidate.is_absolute() or ".." in candidate.parts:
                    raise ValueError(
                        f"Manifest artifact path must be output-relative: {relative!r}"
                    )
                artifact = self.output_dir / candidate
                if not artifact.is_file():
                    raise RuntimeError(
                        f"Missing declared {group_name} artifact: {relative}"
                    )
                if artifact.stat().st_size <= 0:
                    raise RuntimeError(
                        f"Declared {group_name} artifact is empty: {relative}"
                    )
                normalized_record = {
                    "path": candidate.as_posix(),
                    "size_bytes": artifact.stat().st_size,
                    "sha256": _sha256(artifact),
                }
                if record.get("kind"):
                    normalized_record["kind"] = str(record["kind"])
                normalized_records.append(normalized_record)
            normalized[group_name] = normalized_records
        return normalized

    def complete(
        self,
        *,
        camera_names=(),
        expected_minimum=0,
        status="COMPLETE",
        extra_artifacts=None,
    ):
        artifacts = self._camera_artifacts(camera_names)
        if len(artifacts) < int(expected_minimum):
            raise RuntimeError(
                "Render finished without the expected camera artifacts: "
                f"actual={len(artifacts)}, expected_at_least={int(expected_minimum)}"
            )
        empty = [record["path"] for record in artifacts if record["size_bytes"] <= 0]
        if empty:
            raise RuntimeError(f"Render produced empty camera artifacts: {empty}")
        self.payload["status"] = str(status)
        self.payload["finished_at"] = _utc_now()
        self.payload["actual_cycles_device"] = getattr(self.cfg, "ACTUAL_CYCLES_DEVICE", None)
        self.payload["actual_compositor_device"] = getattr(
            self.cfg, "ACTUAL_COMPOSITOR_DEVICE", None
        )
        artifact_groups = {
            "camera_files": artifacts,
            "camera_file_count": len(artifacts),
            "expected_minimum": int(expected_minimum),
        }
        extra_artifacts = dict(extra_artifacts or {})
        if getattr(self.cfg, "PROJECTOR_PSF", None) is not None and status in {"COMPLETE", "VALIDATED"}:
            receipt = self.output_dir / "projector_psf_updates.jsonl"
            if not receipt.is_file() or not receipt.stat().st_size:
                raise RuntimeError("Projector PSF acquisition has no effective-state update receipt")
            records = list(extra_artifacts.get("projector_psf", []))
            if not any(record.get("path") == receipt.name for record in records):
                records.append({"path": receipt.name, "kind": "effective_projector_emission_psf_updates"})
            extra_artifacts["projector_psf"] = records
        artifact_groups.update(self._extra_artifacts(extra_artifacts))
        parameter_records = []
        for name in ("calibration.json", "params.txt"):
            path = self.output_dir / name
            if path.is_file():
                parameter_records.append({"path": name, "size_bytes": path.stat().st_size,
                                          "sha256": _sha256(path)})
        if parameter_records:
            artifact_groups["parameter_files"] = parameter_records
        self.payload["artifacts"] = artifact_groups
        _atomic_write_json(self.path, self.payload)
        self.finalized = True

    def fail(self, error):
        self.payload["status"] = "FAILED"
        self.payload["finished_at"] = _utc_now()
        self.payload["actual_cycles_device"] = getattr(self.cfg, "ACTUAL_CYCLES_DEVICE", None)
        self.payload["actual_compositor_device"] = getattr(
            self.cfg, "ACTUAL_COMPOSITOR_DEVICE", None
        )
        self.payload["error"] = f"{type(error).__name__}: {error}"
        _atomic_write_json(self.path, self.payload)
        self.finalized = True

    def __exit__(self, exc_type, exc_value, _traceback):
        if exc_value is not None:
            self.fail(exc_value)
            return False
        if not self.finalized:
            error = RuntimeError("Render entry exited without finalizing its manifest")
            self.fail(error)
            raise error
        return False


def completed_render_manifest(output_dir):
    path = Path(output_dir) / MANIFEST_NAME
    if not path.is_file():
        return False, f"missing {MANIFEST_NAME}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid {MANIFEST_NAME}: {exc}"
    if payload.get("schema") != MANIFEST_SCHEMA:
        return False, f"unsupported manifest schema: {payload.get('schema')!r}"
    if payload.get("request", {}).get("calibration_schema") != CALIBRATION_SCHEMA:
        return False, "unsupported calibration output schema"
    if payload.get("request", {}).get("pixel_coordinate_contract") != PIXEL_COORDINATE_CONTRACT:
        return False, "legacy or incompatible pixel coordinate contract; rerender required"
    if payload.get("status") != "COMPLETE":
        return False, f"render status is {payload.get('status')!r}"
    artifacts = payload.get("artifacts", {})
    if not isinstance(artifacts, dict):
        return False, "manifest artifacts must be an object"
    expected = int(artifacts.get("expected_minimum", 0))
    files = artifacts.get("camera_files", [])
    if not isinstance(files, list) or len(files) < expected:
        return False, "manifest camera artifact count is incomplete"
    for record in files:
        relative = record.get("path") if isinstance(record, dict) else None
        artifact = Path(output_dir) / str(relative or "")
        if not relative or not artifact.is_file():
            return False, f"missing declared camera artifact: {relative}"
        expected_size = int(record.get("size_bytes", -1))
        if expected_size <= 0 or artifact.stat().st_size != expected_size:
            return False, f"camera artifact size mismatch: {relative}"
        if record.get("sha256") and _sha256(artifact) != record["sha256"]:
            return False, f"camera artifact hash mismatch: {relative}"
    for group_name, records in artifacts.items():
        if group_name in {
            "camera_files",
            "camera_file_count",
            "expected_minimum",
        }:
            continue
        if not isinstance(records, list):
            return False, f"manifest artifact group is not a list: {group_name}"
        for record in records:
            if not isinstance(record, dict):
                return False, f"invalid {group_name} artifact record"
            relative = record.get("path")
            candidate = Path(str(relative or ""))
            if (
                not relative
                or candidate.is_absolute()
                or ".." in candidate.parts
            ):
                return False, f"invalid {group_name} artifact path: {relative}"
            artifact = Path(output_dir) / candidate
            if not artifact.is_file():
                return False, f"missing declared {group_name} artifact: {relative}"
            try:
                expected_size = int(record.get("size_bytes", -1))
            except (TypeError, ValueError):
                return False, f"invalid {group_name} artifact size: {relative}"
            if expected_size <= 0 or artifact.stat().st_size != expected_size:
                return False, f"{group_name} artifact size mismatch: {relative}"
            expected_sha256 = str(record.get("sha256", "") or "")
            if not expected_sha256 or _sha256(artifact) != expected_sha256:
                return False, f"{group_name} artifact hash mismatch: {relative}"
    return True, ""
