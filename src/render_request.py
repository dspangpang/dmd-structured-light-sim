"""Load and snapshot the non-structural Blender render request contract."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path


RENDER_REQUEST_SCHEMA = "core-sim.render_request.v1"
_TOP_LEVEL_KEYS = {
    "schema",
    "scene_config",
    "projector_images",
    "output_dir",
    "render",
    "capture",
    "target",
    "visualization",
    "variant",
}


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _check_keys(value, allowed, label):
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"Unknown {label} field(s): {', '.join(unknown)}")


def _string(value, label, *, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _integer(value, label, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return value


def _number(value, label, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return result


def _vector(value, length, label):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{label} must be a {length}-element JSON array")
    return tuple(_number(item, f"{label}[{index}]") for index, item in enumerate(value))


def _resolve_input_path(
    value,
    request_dir,
    *,
    repo_root=None,
    preserve_blender_relative=True,
):
    text = _string(value, "input path")
    if text.startswith("//"):
        if preserve_blender_relative:
            return text
        if repo_root is None:
            raise ValueError("repo_root is required to resolve a // input path")
        return str((Path(repo_root) / text[2:]).resolve())
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = Path(request_dir) / candidate
    return str(candidate.resolve())


def render_request_from_argv(argv, variant, repo_root):
    path = os.environ.get("SIM_RENDER_REQUEST", "").strip()
    for index, key in enumerate(argv):
        if key == "--render-request":
            if index + 1 >= len(argv):
                raise ValueError("--render-request requires a JSON path")
            path = argv[index + 1]
    if path:
        return path
    default_path = variant.default_render_request_path(repo_root)
    return str(default_path) if default_path else ""


def load_render_request(path, *, repo_root):
    if not path:
        return None
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(repo_root) / candidate
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"Render request not found: {candidate}")
    try:
        root = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid render request JSON {candidate}: {exc}") from exc
    root = _mapping(root, "render request root")
    _check_keys(root, _TOP_LEVEL_KEYS, "render request")
    if root.get("schema") != RENDER_REQUEST_SCHEMA:
        raise ValueError(
            f"Unsupported render request schema {root.get('schema')!r}; "
            f"expected {RENDER_REQUEST_SCHEMA!r}"
        )
    return {
        "path": str(candidate),
        "directory": str(candidate.parent),
        "repo_root": str(Path(repo_root).resolve()),
        "sha256": _sha256(candidate),
        "raw": root,
    }


def render_request_preset(loaded):
    if not loaded:
        return "default"
    render = _mapping(loaded["raw"].get("render", {}), "render")
    preset = render.get("preset", "default")
    return _string(preset, "render.preset").strip().lower().replace("_", "-")


def _apply_render(cfg, render):
    allowed = {
        "preset",
        "samples",
        "engine",
        "use_gpu",
        "cycles_device",
        "cycles_device_name",
        "persistent_data",
        "compositor_device",
        "threads",
        "denoising",
        "pixel_filter",
        "filter_width",
        "resolution",
        "file_format",
        "color_mode",
        "film_transparent",
        "save_float_exr",
        "projector_energy",
        "projector_texture_interpolation",
        "bounce_level",
        "environment_light",
    }
    _check_keys(render, allowed, "render")
    scalar_fields = {
        "samples": ("SAMPLES", lambda value: _integer(value, "render.samples", minimum=1)),
        "engine": ("ENGINE", lambda value: _string(value, "render.engine").upper()),
        "use_gpu": ("USE_GPU", lambda value: _boolean(value, "render.use_gpu")),
        "cycles_device": ("CYCLES_DEVICE", lambda value: _string(value, "render.cycles_device", allow_empty=True).upper()),
        "cycles_device_name": ("CYCLES_DEVICE_NAME", lambda value: _string(value, "render.cycles_device_name", allow_empty=True)),
        "persistent_data": ("USE_PERSISTENT_DATA", lambda value: _boolean(value, "render.persistent_data")),
        "compositor_device": ("COMPOSITOR_DEVICE", lambda value: _string(value, "render.compositor_device").upper()),
        "threads": ("RENDER_THREADS", lambda value: _integer(value, "render.threads", minimum=0)),
        "denoising": ("CYCLES_USE_DENOISING", lambda value: _boolean(value, "render.denoising")),
        "pixel_filter": ("CYCLES_PIXEL_FILTER_TYPE", lambda value: _string(value, "render.pixel_filter").upper()),
        "filter_width": ("CYCLES_FILTER_WIDTH", lambda value: _number(value, "render.filter_width", minimum=0.0)),
        "file_format": ("RENDER_FILE_FORMAT", lambda value: _string(value, "render.file_format").upper()),
        "color_mode": ("RENDER_COLOR_MODE", lambda value: _string(value, "render.color_mode").upper()),
        "film_transparent": ("RENDER_FILM_TRANSPARENT", lambda value: _boolean(value, "render.film_transparent")),
        "save_float_exr": ("SAVE_RENDER_FLOAT_EXR", lambda value: _boolean(value, "render.save_float_exr")),
        "projector_energy": ("PROJECTOR_ENERGY", lambda value: _number(value, "render.projector_energy", minimum=0.0)),
        "projector_texture_interpolation": ("PROJECTOR_TEXTURE_INTERPOLATION", lambda value: _string(value, "render.projector_texture_interpolation")),
        "bounce_level": ("BOUNCE_LEVEL", lambda value: _string(value, "render.bounce_level").lower()),
    }
    for request_key, (runtime_key, convert) in scalar_fields.items():
        if request_key in render:
            setattr(cfg, runtime_key, convert(render[request_key]))
    if "resolution" in render:
        resolution = render["resolution"]
        if not isinstance(resolution, list) or len(resolution) != 2:
            raise ValueError("render.resolution must be a 2-element JSON array")
        cfg.RENDER_RES_X = _integer(
            resolution[0], "render.resolution[0]", minimum=1
        )
        cfg.RENDER_RES_Y = _integer(
            resolution[1], "render.resolution[1]", minimum=1
        )
    if "environment_light" in render:
        environment = _mapping(render["environment_light"], "render.environment_light")
        _check_keys(environment, {"enabled", "strength"}, "render.environment_light")
        if "enabled" in environment:
            cfg.USE_ENV_LIGHT = _boolean(environment["enabled"], "render.environment_light.enabled")
        if "strength" in environment:
            cfg.ENV_LIGHT_STRENGTH = _number(environment["strength"], "render.environment_light.strength", minimum=0.0)


def _apply_capture(cfg, capture, variant, resolve_input_path):
    common_allowed = {
        "mode",
        "projector_output_dir",
        "projector_image_format",
        "save_depth",
        "save_pointcloud",
    }
    mappings = {
        "mode": ("CAPTURE_MODE", lambda value: _string(value, "capture.mode")),
        "projector_output_dir": ("PROJECTOR_CAPTURE_OUTPUT_DIR", lambda value: _string(value, "capture.projector_output_dir")),
        "projector_image_format": ("PROJECTOR_CAPTURE_IMAGE_FORMAT", lambda value: _string(value, "capture.projector_image_format").upper()),
        "save_depth": ("SAVE_DEPTH", lambda value: _boolean(value, "capture.save_depth")),
        "save_pointcloud": ("SAVE_POINTCLOUD", lambda value: _boolean(value, "capture.save_pointcloud")),
    }
    for request_key, (runtime_key, convert) in mappings.items():
        if request_key in capture:
            setattr(cfg, runtime_key, convert(capture[request_key]))

    consumed = set(
        variant.apply_capture_request(cfg, capture, resolve_input_path) or ()
    )
    if consumed & common_allowed:
        names = ", ".join(sorted(consumed & common_allowed))
        raise ValueError(
            f"Variant {variant.name!r} cannot consume common capture field(s): {names}"
        )
    missing = consumed - set(capture)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(
            f"Variant {variant.name!r} reported unprovided capture field(s): {names}"
        )
    unknown = set(capture) - common_allowed - consumed
    if unknown:
        _check_keys(capture, common_allowed | consumed, "capture")


def _apply_target(cfg, target, request_dir):
    allowed = {"asset", "primitive", "center_world_m", "rotation_deg", "scale", "material"}
    _check_keys(target, allowed, "target")
    if "asset" in target and "primitive" in target:
        raise ValueError("target.asset and target.primitive are mutually exclusive")
    if "asset" in target:
        cfg.CUSTOM_TARGET_PATH = _resolve_input_path(target["asset"], request_dir)
        cfg.USE_CUSTOM_TARGET = True
    if "primitive" in target:
        primitive = _mapping(target["primitive"], "target.primitive")
        _check_keys(primitive, {"shape", "size_m", "radius_m", "height_m", "axis"}, "target.primitive")
        shape = _string(primitive.get("shape"), "target.primitive.shape").lower()
        if shape in {"box", "plane"}:
            size = _vector(primitive.get("size_m"), 3, "target.primitive.size_m")
            if any(value <= 0.0 for value in size):
                raise ValueError("target.primitive.size_m values must be > 0")
            cfg.target_size_x, cfg.target_size_y, cfg.target_size_z = size
        elif shape == "cylinder":
            cfg.target_cyl_radius = _number(primitive.get("radius_m"), "target.primitive.radius_m", minimum=0.0)
            cfg.target_cyl_height = _number(primitive.get("height_m"), "target.primitive.height_m", minimum=0.0)
            if cfg.target_cyl_radius <= 0.0 or cfg.target_cyl_height <= 0.0:
                raise ValueError("Cylinder radius_m and height_m must be > 0")
            cfg.target_cyl_axis = _string(primitive.get("axis", "y"), "target.primitive.axis").lower()
        else:
            raise ValueError(f"Unsupported target primitive shape: {shape!r}")
        cfg.TARGET_SHAPE = shape
        cfg.USE_CUSTOM_TARGET = False
        cfg.CUSTOM_TARGET_PATH = ""
    if "center_world_m" in target:
        cfg.TARGET_CENTER_X, cfg.TARGET_CENTER_Y, cfg.TARGET_CENTER_Z = _vector(
            target["center_world_m"], 3, "target.center_world_m"
        )
    if "rotation_deg" in target:
        (
            cfg.CUSTOM_TARGET_ROT_X_DEG,
            cfg.CUSTOM_TARGET_ROT_Y_DEG,
            cfg.CUSTOM_TARGET_ROT_Z_DEG,
        ) = _vector(target["rotation_deg"], 3, "target.rotation_deg")
    if "scale" in target:
        cfg.TARGET_SCALE = _number(target["scale"], "target.scale", minimum=0.0)
        if cfg.TARGET_SCALE <= 0.0:
            raise ValueError("target.scale must be > 0")
    if "material" in target:
        material = _mapping(target["material"], "target.material")
        _check_keys(material, {"albedo", "roughness", "metallic", "subsurface", "use_ply_brdf"}, "target.material")
        mappings = {
            "albedo": ("MAT_ALBEDO", lambda value: _number(value, "target.material.albedo", minimum=0.0)),
            "roughness": ("MAT_ROUGHNESS", lambda value: _number(value, "target.material.roughness", minimum=0.0)),
            "metallic": ("MAT_METALLIC", lambda value: _number(value, "target.material.metallic", minimum=0.0)),
            "subsurface": ("MAT_SUBSURFACE", lambda value: _number(value, "target.material.subsurface", minimum=0.0)),
            "use_ply_brdf": ("USE_PLY_BRDF", lambda value: _boolean(value, "target.material.use_ply_brdf")),
        }
        for request_key, (runtime_key, convert) in mappings.items():
            if request_key in material:
                setattr(cfg, runtime_key, convert(material[request_key]))


def _apply_model(cfg, value, label, prefix, request_dir):
    model = _mapping(value, label)
    _check_keys(model, {"enabled", "path", "scale", "rotation_deg", "disable_render", "disable_shadows"}, label)
    if "enabled" in model:
        setattr(cfg, f"USE_{prefix}_MODEL", _boolean(model["enabled"], f"{label}.enabled"))
    if "path" in model:
        setattr(cfg, f"{prefix}_MODEL_PATH", _resolve_input_path(model["path"], request_dir))
    if "scale" in model:
        setattr(cfg, f"{prefix}_MODEL_SCALE", _number(model["scale"], f"{label}.scale", minimum=0.0))
    if "rotation_deg" in model:
        rotation = _vector(model["rotation_deg"], 3, f"{label}.rotation_deg")
        for axis, angle in zip("XYZ", rotation):
            setattr(cfg, f"{prefix}_MODEL_ROT_{axis}_DEG", angle)
    if prefix == "PROJECTOR":
        if "disable_render" in model:
            cfg.PROJECTOR_MODEL_DISABLE_RENDER = _boolean(model["disable_render"], f"{label}.disable_render")
        if "disable_shadows" in model:
            cfg.PROJECTOR_MODEL_DISABLE_SHADOWS = _boolean(model["disable_shadows"], f"{label}.disable_shadows")
    elif "disable_render" in model or "disable_shadows" in model:
        raise ValueError(f"{label} does not support disable_render/disable_shadows")


def _apply_visualization(cfg, visualization, request_dir):
    allowed = {"camera_model", "projector_model", "overview_camera"}
    _check_keys(visualization, allowed, "visualization")
    if "camera_model" in visualization:
        _apply_model(cfg, visualization["camera_model"], "visualization.camera_model", "CAMERA", request_dir)
    if "projector_model" in visualization:
        _apply_model(cfg, visualization["projector_model"], "visualization.projector_model", "PROJECTOR", request_dir)
    if "overview_camera" in visualization:
        overview = _mapping(visualization["overview_camera"], "visualization.overview_camera")
        allowed_overview = {"enabled", "render_only", "name", "location_m", "look_at_m", "lens_mm", "sensor_width_mm"}
        _check_keys(overview, allowed_overview, "visualization.overview_camera")
        mappings = {
            "enabled": ("USE_OVERVIEW_CAMERA", lambda value: _boolean(value, "visualization.overview_camera.enabled")),
            "render_only": ("RENDER_OVERVIEW_ONLY", lambda value: _boolean(value, "visualization.overview_camera.render_only")),
            "name": ("OVERVIEW_CAMERA_NAME", lambda value: _string(value, "visualization.overview_camera.name")),
            "location_m": ("OVERVIEW_CAMERA_LOCATION", lambda value: _vector(value, 3, "visualization.overview_camera.location_m")),
            "look_at_m": ("OVERVIEW_CAMERA_LOOK_AT", lambda value: _vector(value, 3, "visualization.overview_camera.look_at_m")),
            "lens_mm": ("OVERVIEW_CAMERA_LENS_MM", lambda value: _number(value, "visualization.overview_camera.lens_mm", minimum=0.0)),
            "sensor_width_mm": ("OVERVIEW_CAMERA_SENSOR_WIDTH", lambda value: _number(value, "visualization.overview_camera.sensor_width_mm", minimum=0.0)),
        }
        for request_key, (runtime_key, convert) in mappings.items():
            if request_key in overview:
                setattr(cfg, runtime_key, convert(overview[request_key]))


def apply_render_request(cfg, loaded, variant):
    if not loaded:
        return ""
    root = loaded["raw"]
    request_dir = loaded["directory"]
    repo_root = loaded["repo_root"]
    if "render" in root:
        _apply_render(cfg, _mapping(root["render"], "render"))
    if "capture" in root:
        _apply_capture(
            cfg,
            _mapping(root["capture"], "capture"),
            variant,
            lambda value: _resolve_input_path(value, request_dir),
        )
    if "target" in root:
        _apply_target(cfg, _mapping(root["target"], "target"), request_dir)
    if "visualization" in root:
        _apply_visualization(cfg, _mapping(root["visualization"], "visualization"), request_dir)
    if "projector_images" in root:
        cfg.PROJECTOR_IMAGE = _resolve_input_path(root["projector_images"], request_dir)
    if "output_dir" in root:
        cfg.OUTPUT_DIR = _string(root["output_dir"], "output_dir")
    scene_config = ""
    if "scene_config" in root:
        scene_config = _resolve_input_path(
            root["scene_config"],
            request_dir,
            repo_root=repo_root,
            preserve_blender_relative=False,
        )
    variant_block = root.get("variant")
    if variant_block is not None:
        variant_block = _mapping(variant_block, "variant")
        _check_keys(variant_block, {"name", "settings"}, "variant")
        request_variant = _string(variant_block.get("name"), "variant.name")
        if request_variant != variant.name:
            raise ValueError(
                f"Render request targets variant {request_variant!r}, "
                f"but current worktree is {variant.name!r}"
            )
        settings = _mapping(variant_block.get("settings", {}), "variant.settings")
        variant.apply_render_request(
            cfg,
            settings,
            lambda value: _resolve_input_path(value, request_dir),
        )
    cfg.RENDER_REQUEST_PATH = loaded["path"]
    cfg.RENDER_REQUEST_SCHEMA = str(root["schema"])
    cfg.RENDER_REQUEST_SHA256 = loaded["sha256"]
    print(f"[RenderRequest] Applied render request: {loaded['path']}")
    return scene_config


def snapshot_render_request(cfg, output_dir):
    source_text = str(getattr(cfg, "RENDER_REQUEST_PATH", "") or "")
    if not source_text:
        return None
    source = Path(source_text).resolve()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "render_request.json"
    manifest_path = output / "render_request.manifest.json"
    digest = str(cfg.RENDER_REQUEST_SHA256)
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if str(existing.get("sha256", "")) != digest:
            raise ValueError(
                "Output directory already belongs to a different render request: "
                f"{output}"
            )
    elif snapshot.is_file() and _sha256(snapshot) != digest:
        raise ValueError(
            "Output directory contains a different unmanifested render request: "
            f"{output}"
        )
    shutil.copy2(source, snapshot)
    manifest = {
        "schema": str(cfg.RENDER_REQUEST_SCHEMA),
        "sha256": digest,
        "source": str(source),
        "snapshot": snapshot.name,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[RenderRequest] Snapshotted render request: {snapshot}")
    return snapshot
