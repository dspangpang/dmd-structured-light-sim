import bpy
import os
from config import SimConfig
from calc_params import get_camera_parameters
import numpy as np
from pixel_coordinates import ndc_to_opencv


def validate_blender_camera_intrinsics(cameras, cfg, tolerance_px=2.0e-2):
    """Compare Blender's effective projection against each imported App K."""
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    devices = {
        str(device["name"]): device
        for device in (getattr(cfg, "APP_CAMERA_DEVICES", None) or ())
    }
    scene = bpy.context.scene
    width = int(scene.render.resolution_x)
    height = int(scene.render.resolution_y)
    bpy.context.view_layer.update()

    def project_world(camera, world_point):
        ndc = world_to_camera_view(scene, camera, world_point)
        return ndc_to_opencv(ndc.x, ndc.y, width, height)

    def project_local(camera, local_point):
        return project_world(camera, camera.matrix_world @ Vector(local_point))

    for camera in cameras:
        device = devices.get(str(camera.name))
        if device is None:
            continue
        expected = np.asarray(device["intrinsic_matrix_px"], dtype=float)
        # A larger finite-difference step keeps Blender's float projection
        # noise well below the sub-pixel conformance tolerance.
        step = 0.1
        principal = project_local(camera, (0.0, 0.0, -1.0))
        sample_x = project_local(camera, (step, 0.0, -1.0))
        sample_y = project_local(camera, (0.0, -step, -1.0))
        actual = np.array(
            [
                (sample_x[0] - principal[0]) / step,
                (sample_y[1] - principal[1]) / step,
                principal[0],
                principal[1],
            ],
            dtype=float,
        )
        wanted = np.array(
            [expected[0, 0], expected[1, 1], expected[0, 2], expected[1, 2]],
            dtype=float,
        )
        error = float(np.max(np.abs(actual - wanted)))
        if error > float(tolerance_px):
            raise ValueError(
                f"Blender camera {camera.name} projection disagrees with App K "
                f"(max abs error={error:.6g} px, actual={actual.tolist()}, "
                f"expected={wanted.tolist()})"
            )

        device_to_world = np.asarray(device["device_to_world_3x3"], dtype=float)
        center_world = np.asarray(device["center_world_m"], dtype=float)
        projection = np.asarray(device["projection_matrix_px_3x4"], dtype=float)
        device_samples = np.asarray(
            [
                (0.0, 0.0, 1.0),
                (step, 0.0, 1.0),
                (0.0, step, 1.0),
            ],
            dtype=float,
        )
        world_samples = center_world + device_samples @ device_to_world.T
        actual_world = np.asarray(
            [project_world(camera, Vector(point)) for point in world_samples],
            dtype=float,
        )
        homogeneous_world_mm = np.column_stack(
            (world_samples * 1.0e3, np.ones(len(world_samples), dtype=float))
        )
        projected = homogeneous_world_mm @ projection.T
        expected_world = projected[:, :2] / projected[:, 2:3]
        world_error = float(np.max(np.abs(actual_world - expected_world)))
        if world_error > float(tolerance_px):
            raise ValueError(
                f"Blender camera {camera.name} world projection disagrees with "
                f"the App projection matrix (max abs error={world_error:.6g} px, "
                f"actual={actual_world.tolist()}, expected={expected_world.tolist()}); "
                "check for an axis flip or mirrored camera basis"
            )
        print(
            f"[RenderContract] {camera.name} matches App K/P "
            f"(intrinsic error={error:.3g} px, world error={world_error:.3g} px)"
        )


def _normalize_render_file_format(raw_format):
    fmt = str(raw_format or "BMP").strip().upper()
    aliases = {
        "JPG": "JPEG",
        "TIF": "TIFF",
        "EXR": "OPEN_EXR",
    }
    return aliases.get(fmt, fmt)


def _render_file_extension(raw_format):
    fmt = _normalize_render_file_format(raw_format)
    return {
        "BMP": ".bmp",
        "PNG": ".png",
        "JPEG": ".jpg",
        "TIFF": ".tif",
        "OPEN_EXR": ".exr",
    }.get(fmt, f".{fmt.lower()}")


def setup_render(cfg: SimConfig):
    scn = bpy.context.scene
    view_layer = scn.view_layers[0] if scn.view_layers else None
    if view_layer:
        view_layer.use_pass_z = cfg.SAVE_DEPTH or cfg.SAVE_POINTCLOUD

    scn.render.engine = cfg.ENGINE
    render_res_x = int(getattr(cfg, "RENDER_RES_X", 0) or cfg.RES_X)
    render_res_y = int(getattr(cfg, "RENDER_RES_Y", 0) or cfg.RES_Y)
    if (render_res_x, render_res_y) != (cfg.RES_X, cfg.RES_Y) and not bool(
        getattr(cfg, "RENDER_OVERVIEW_ONLY", False)
    ):
        raise ValueError(
            "Render resolution may differ from the App camera only for an "
            "overview-only rendering preset."
        )
    scn.render.resolution_x = render_res_x
    scn.render.resolution_y = render_res_y
    scn.render.resolution_percentage = 100
    pitch_x = float(getattr(cfg, "CAMERA_PIXEL_PITCH_X_UM", 1.0))
    pitch_y = float(getattr(cfg, "CAMERA_PIXEL_PITCH_Y_UM", 1.0))
    pitch_scale = min(pitch_x, pitch_y)
    scn.render.pixel_aspect_x = pitch_x / pitch_scale
    scn.render.pixel_aspect_y = pitch_y / pitch_scale
    _configure_render_performance(scn, cfg)

    if cfg.ENGINE == "CYCLES":
        _configure_cycles_device(cfg)
        scn.cycles.samples = cfg.SAMPLES
        if hasattr(scn.cycles, "use_denoising"):
            scn.cycles.use_denoising = bool(
                getattr(cfg, "CYCLES_USE_DENOISING", False)
            )
            print("[Render] Cycles denoising:", scn.cycles.use_denoising)
        if hasattr(scn.cycles, "pixel_filter_type"):
            scn.cycles.pixel_filter_type = getattr(
                cfg,
                "CYCLES_PIXEL_FILTER_TYPE",
                "BOX",
            )
            print("[Render] Cycles pixel filter:", scn.cycles.pixel_filter_type)
        if hasattr(scn.cycles, "filter_width"):
            scn.cycles.filter_width = float(getattr(cfg, "CYCLES_FILTER_WIDTH", 0.01))
            print("[Render] Cycles filter width:", scn.cycles.filter_width)
        # Clamp multi-bounce strength with a simple preset knob
        bounce_presets = {
            "off": dict(max_bounces=0, diffuse=0, glossy=0, transmission=0, volume=0, transparent=0),
            "low": dict(max_bounces=1, diffuse=1, glossy=1, transmission=0, volume=0, transparent=1),
            "medium": dict(max_bounces=4, diffuse=2, glossy=2, transmission=2, volume=0, transparent=4),
            "high": dict(max_bounces=12, diffuse=4, glossy=4, transmission=4, volume=2, transparent=8),
        }
        preset = bounce_presets.get(cfg.BOUNCE_LEVEL.lower(), bounce_presets["medium"])
        scn.cycles.max_bounces = preset["max_bounces"]
        scn.cycles.diffuse_bounces = preset["diffuse"]
        scn.cycles.glossy_bounces = preset["glossy"]
        scn.cycles.transmission_bounces = preset["transmission"]
        scn.cycles.volume_bounces = preset["volume"]
        scn.cycles.transparent_max_bounces = preset["transparent"]

    # Environment light control
    if scn.world and scn.world.node_tree:
        bg_node = scn.world.node_tree.nodes.get("Background")
        if bg_node:
            strength = cfg.ENV_LIGHT_STRENGTH if cfg.USE_ENV_LIGHT else 0.0
            bg_node.inputs["Strength"].default_value = strength
    
    # Remove default light if any (though we usually clean scene, but just in case)
    # And do NOT add any fill light here.
    
    # Color Management
    # Force scene-linear output (no Filmic/sRGB/tone mapping)
    scn.view_settings.view_transform = "Raw"
    scn.view_settings.look = "None"
    scn.view_settings.exposure = 0.0
    scn.view_settings.gamma = 1.0

    # Output: scene-linear image via compositor (clamped, no gamma)
    render_file_format = _normalize_render_file_format(
        getattr(cfg, "RENDER_FILE_FORMAT", "BMP")
    )
    scn.render.image_settings.file_format = render_file_format
    color_mode = (cfg.RENDER_COLOR_MODE or "BW").upper()
    if color_mode in ("COLOR", "COLOUR"):
        color_mode = "RGB"
    if color_mode not in ("BW", "RGB", "RGBA"):
        raise ValueError(f"[Render] Unsupported RENDER_COLOR_MODE: {cfg.RENDER_COLOR_MODE}")
    scn.render.image_settings.color_mode = color_mode
    scn.render.image_settings.color_depth = "8"
    if hasattr(scn.render, "film_transparent"):
        scn.render.film_transparent = bool(
            getattr(cfg, "RENDER_FILM_TRANSPARENT", False)
        )
    scn.render.use_compositing = True
    scn.use_nodes = True
    save_depth = cfg.SAVE_DEPTH or cfg.SAVE_POINTCLOUD
    save_float_exr = bool(getattr(cfg, "SAVE_RENDER_FLOAT_EXR", False))
    _setup_linear_compositor(scn, view_layer, save_depth, color_mode, save_float_exr)
    
    print("[Render] Engine:", cfg.ENGINE)
    print("[Render] Resolution:", render_res_x, render_res_y)
    print("[Render] Pixel aspect X/Y:", scn.render.pixel_aspect_x, scn.render.pixel_aspect_y)
    print("[Render] File format:", render_file_format, color_mode)
    print("[Render] Transparent film:", getattr(scn.render, "film_transparent", False))
    print("[Render] Float EXR output:", save_float_exr)


def prepare_camera_preview(cameras):
    """Make the interactive viewport use the same camera as the render path."""
    if not cameras:
        raise ValueError("Cannot prepare camera preview without a render camera")

    camera = cameras[0]
    scene = bpy.context.scene
    scene.camera = camera

    viewport_count = 0
    window_manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(window_manager, "windows", ()):
        screen = getattr(window, "screen", None)
        for area in getattr(screen, "areas", ()):
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            region_3d = getattr(space, "region_3d", None)
            if region_3d is None:
                continue
            region_3d.view_perspective = "CAMERA"
            if hasattr(region_3d, "view_camera_offset"):
                region_3d.view_camera_offset = (0.0, 0.0)
            if hasattr(region_3d, "view_camera_zoom"):
                region_3d.view_camera_zoom = 0.0
            viewport_count += 1

    print(
        f"[Render] Interactive preview camera: {camera.name} "
        f"({viewport_count} 3D viewport(s))"
    )
    return camera


def _configure_cycles_device(cfg: SimConfig):
    from cycles_device import configure_cycles_device
    prefs = getattr(bpy.context, "preferences", None)
    addons = getattr(prefs, "addons", None)
    addon = addons.get("cycles") if addons is not None else None
    return configure_cycles_device(bpy.context.scene, getattr(addon, "preferences", None), cfg)


def _configure_render_performance(scn, cfg: SimConfig):
    if hasattr(scn.render, "use_persistent_data"):
        scn.render.use_persistent_data = bool(getattr(cfg, "USE_PERSISTENT_DATA", True))
        print("[Render] Persistent data:", scn.render.use_persistent_data)

    if hasattr(scn.render, "compositor_device"):
        from cycles_device import resolve_compositor_device

        requested = getattr(cfg, "COMPOSITOR_DEVICE", "GPU") or "CPU"
        actual, fallback_reason = resolve_compositor_device(
            requested,
            background=bool(getattr(getattr(bpy, "app", None), "background", False)),
            display_available=bool(
                os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
            ),
            gpu_enabled=bool(getattr(cfg, "USE_GPU", True)),
        )
        record = {
            "requested": str(requested).strip().upper(),
            "device": actual,
            "fallback_reason": fallback_reason,
        }
        cfg.ACTUAL_COMPOSITOR_DEVICE = record
        try:
            scn.render.compositor_device = actual
        except Exception as exc:
            # Assignment of GPU can fail on a graphical host whose context was
            # lost between the preflight and this call.  Retrying CPU is safe;
            # the background/no-display path above avoids Blender's known
            # native abort before Python can catch it.
            if actual != "GPU":
                raise
            actual = "CPU"
            fallback_reason = f"GPU compositor assignment failed: {exc}"
            cfg.ACTUAL_COMPOSITOR_DEVICE = {
                "requested": str(requested).strip().upper(),
                "device": actual,
                "fallback_reason": fallback_reason,
            }
            scn.render.compositor_device = actual
        if fallback_reason:
            print(f"[Render] GPU compositor unavailable; using CPU: {fallback_reason}")
        print("[Render] Compositor device:", scn.render.compositor_device)

    render_threads = int(getattr(cfg, "RENDER_THREADS", 0) or 0)
    if render_threads > 0 and hasattr(scn.render, "threads_mode") and hasattr(scn.render, "threads"):
        scn.render.threads_mode = "FIXED"
        scn.render.threads = render_threads
        print("[Render] Render threads:", render_threads)



def _setup_linear_compositor(scn, view_layer, save_depth, color_mode, save_float_exr):
    tree = scn.node_tree
    tree.nodes.clear()

    rlayers = tree.nodes.new("CompositorNodeRLayers")
    if view_layer:
        rlayers.layer = view_layer.name
    composite = tree.nodes.new("CompositorNodeComposite")
    float_out = None
    depth_out = None
    if save_float_exr:
        float_out = tree.nodes.new("CompositorNodeOutputFile")
    if save_depth:
        depth_out = tree.nodes.new("CompositorNodeOutputFile")

    if save_float_exr:
        float_out.name = "ImageFloatOutput"
        float_out.label = "ImageFloatOutput"
        float_out.base_path = ""
        float_out.format.file_format = "OPEN_EXR"
        float_out.format.color_mode = "BW"
        float_out.format.color_depth = "32"
        float_out.format.color_management = "OVERRIDE"
        float_out.format.view_settings.view_transform = "Raw"

    if save_depth:
        depth_out.name = "DepthOutput"
        depth_out.label = "DepthOutput"
        depth_out.base_path = ""
        depth_out.format.file_format = "OPEN_EXR"
        depth_out.format.color_mode = "BW"
        depth_out.format.color_depth = "32"
        depth_out.format.color_management = "OVERRIDE"
        depth_out.format.view_settings.view_transform = "Raw"

    if color_mode == "BW":
        rgb_to_bw = tree.nodes.new("CompositorNodeRGBToBW")
        max0 = tree.nodes.new("CompositorNodeMath")
        min1 = tree.nodes.new("CompositorNodeMath")
        combine = _create_combine_color_node(tree)

        max0.operation = "MAXIMUM"
        min1.operation = "MINIMUM"
        max0.inputs[1].default_value = 0.0
        min1.inputs[1].default_value = 1.0

        tree.links.new(rlayers.outputs["Image"], rgb_to_bw.inputs["Image"])
        tree.links.new(rgb_to_bw.outputs["Val"], max0.inputs[0])
        tree.links.new(max0.outputs["Value"], min1.inputs[0])
        _link_value_to_rgb(combine, min1.outputs["Value"])
        _set_alpha_if_present(combine, 1.0)
        image_socket = combine.outputs.get("Image") or combine.outputs.get("Color") or combine.outputs[0]
    else:
        image_socket = rlayers.outputs["Image"]

    tree.links.new(image_socket, composite.inputs["Image"])
    if save_float_exr:
        tree.links.new(image_socket, float_out.inputs[0])

    if save_depth:
        depth_socket = rlayers.outputs.get("Depth") or rlayers.outputs.get("Z")
        if depth_socket is None:
            raise RuntimeError("[Render] Depth pass not available; enable Z pass on the view layer.")
        tree.links.new(depth_socket, depth_out.inputs[0])


def _create_combine_color_node(tree):
    for node_type in (
        "CompositorNodeCombineColor",
        "CompositorNodeCombineRGB",
        "CompositorNodeCombineRGBA",
    ):
        if hasattr(bpy.types, node_type):
            node = tree.nodes.new(node_type)
            if hasattr(node, "mode"):
                node.mode = "RGB"
            return node
    raise RuntimeError("[Render] No Combine Color node type found in this Blender build.")


def _link_value_to_rgb(node, value_socket):
    r = node.inputs.get("R") or node.inputs.get("Red")
    g = node.inputs.get("G") or node.inputs.get("Green")
    b = node.inputs.get("B") or node.inputs.get("Blue")
    if r is None or g is None or b is None:
        raise RuntimeError("[Render] Combine node missing RGB inputs.")
    nt = node.id_data
    nt.links.new(value_socket, r)
    nt.links.new(value_socket, g)
    nt.links.new(value_socket, b)


def _set_alpha_if_present(node, value):
    a = node.inputs.get("A") or node.inputs.get("Alpha")
    if a is not None:
        a.default_value = value


def render_cameras(
    cameras,
    cfg: SimConfig,
    output_dir="output",
    image_format=None,
    file_extension=None,
    color_mode=None,
    color_depth=None,
    use_compositor=None,
    save_depth=None,
):
    scn = bpy.context.scene
    base_dir = bpy.path.abspath("//") or os.getcwd()
    out_dir = os.path.join(base_dir, output_dir)
    os.makedirs(out_dir, exist_ok=True)
    float_exr_node = scn.node_tree.nodes.get("ImageFloatOutput")
    depth_node = scn.node_tree.nodes.get("DepthOutput")
    frame_str = f"{scn.frame_current:04d}"
    save_depth_effective = cfg.SAVE_DEPTH or cfg.SAVE_POINTCLOUD
    if save_depth is not None:
        save_depth_effective = bool(save_depth)
    params = (
        get_camera_parameters(cfg, include_projector=False)
        if cfg.SAVE_POINTCLOUD and save_depth_effective
        else {}
    )

    image_settings = scn.render.image_settings
    old_file_format = image_settings.file_format
    old_color_mode = image_settings.color_mode
    old_color_depth = image_settings.color_depth
    old_use_compositing = scn.render.use_compositing
    try:
        if image_format is not None:
            image_settings.file_format = _normalize_render_file_format(image_format)
        if color_mode is not None:
            image_settings.color_mode = color_mode
        if color_depth is not None:
            image_settings.color_depth = color_depth
        if use_compositor is not None:
            scn.render.use_compositing = bool(use_compositor)

        render_file_format = getattr(image_settings, "file_format", "BMP")
        render_ext = file_extension or _render_file_extension(render_file_format)
        if render_ext and not str(render_ext).startswith("."):
            render_ext = f".{render_ext}"

        for cam in cameras:
            scn.camera = cam
            scn.render.filepath = os.path.join(out_dir, f"{cam.name}{render_ext}")
            if float_exr_node:
                float_exr_node.base_path = out_dir
                float_exr_node.file_slots[0].path = f"{cam.name}_float_"
            if save_depth_effective and depth_node:
                depth_node.base_path = out_dir
                depth_node.file_slots[0].path = f"{cam.name}_depth_"
            bpy.ops.render.render(write_still=True)
            if float_exr_node:
                _rename_float_exr_outputs(out_dir, cam.name, frame_str)
            if save_depth_effective:
                _rename_depth_outputs(out_dir, cam.name, frame_str)
            if cfg.SAVE_POINTCLOUD and save_depth_effective:
                _depth_to_point_cloud(out_dir, cam.name, params.get(cam.name))
            print(f"[Render] Saved {render_file_format}: {scn.render.filepath}")
            if float_exr_node:
                float_exr_path = os.path.join(out_dir, f"{cam.name}_float.exr")
                print(f"[Render] Saved float EXR: {float_exr_path}")
    finally:
        image_settings.file_format = old_file_format
        image_settings.color_mode = old_color_mode
        image_settings.color_depth = old_color_depth
        scn.render.use_compositing = old_use_compositing


def _rename_float_exr_outputs(out_dir, cam_name, frame_str):
    exr_src = os.path.join(out_dir, f"{cam_name}_float_{frame_str}.exr")
    exr_dst = os.path.join(out_dir, f"{cam_name}_float.exr")
    if os.path.exists(exr_src):
        os.replace(exr_src, exr_dst)


def _rename_depth_outputs(out_dir, cam_name, frame_str):
    exr_src = os.path.join(out_dir, f"{cam_name}_depth_{frame_str}.exr")
    exr_dst = os.path.join(out_dir, f"{cam_name}_depth.exr")
    if os.path.exists(exr_src):
        os.replace(exr_src, exr_dst)

    # No normalized PNG output for depth


def _depth_to_point_cloud(out_dir, cam_name, cam_params):
    if cam_params is None:
        print(f"[Render] Missing camera params for {cam_name}, skipping point cloud.")
        return

    depth_path = os.path.join(out_dir, f"{cam_name}_depth.exr")
    if not os.path.exists(depth_path):
        print(f"[Render] Depth file not found: {depth_path}")
        return

    depth = _load_depth_exr(depth_path)
    if depth.size == 0:
        print(f"[Render] Empty depth for {cam_name}, skipping point cloud.")
        return

    K = cam_params["K"].astype(np.float64)
    R = cam_params["R"].astype(np.float64)
    t = cam_params["t"].astype(np.float64).reshape(1, 3)

    h, w = depth.shape
    uu = (np.arange(w, dtype=np.float64) - K[0, 2])[None, :]
    vv = (np.arange(h, dtype=np.float64) - K[1, 2])[:, None]

    z = depth.astype(np.float64)
    if np.nanmax(z) <= 0.0 and np.nanmin(z) < 0.0:
        # Some Blender builds store Z pass as negative (camera looks down -Z).
        z = -z
    valid = np.isfinite(z) & (z > 1.0e-6) & (z < 1.0e8)
    if not np.any(valid):
        print(f"[Render] No valid depth points for {cam_name}.")
        return

    x = uu * z / K[0, 0]
    y = vv * z / K[1, 1]
    pts_cam = np.stack([x, y, z], axis=-1).reshape(-1, 3)
    pts_cam = pts_cam[valid.reshape(-1)]

    # RT is World -> Camera: x_c = R x_w + t
    # Invert with row-vector form: x_w = (x_c - t) R
    pts_world = (pts_cam - t) @ R

    ply_path = os.path.join(out_dir, f"{cam_name}_pointcloud.ply")
    _write_ply_binary(ply_path, pts_world.astype(np.float32))
    print(f"[Render] Saved point cloud: {ply_path}")


def _load_depth_exr(path):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        if hasattr(img, "colorspace_settings"):
            img.colorspace_settings.name = "Non-Color"
        w, h = img.size
        pixels = np.array(img.pixels[:], dtype=np.float32)
        if pixels.size == 0 or w == 0 or h == 0:
            return np.empty((0, 0), dtype=np.float32)
        pixels = pixels.reshape((h, w, 4))
        depth = pixels[:, :, 0]
        # Blender stores pixels bottom-up; flip to top-down to match K with y-down.
        return np.flipud(depth)
    finally:
        bpy.data.images.remove(img)


def _write_ply_binary(path, points_xyz):
    pts = np.asarray(points_xyz, dtype=np.float32).reshape(-1, 3)
    header = "\n".join(
        [
            "ply",
            "format binary_little_endian 1.0",
            f"element vertex {len(pts)}",
            "property float x",
            "property float y",
            "property float z",
            "end_header",
            "",
        ]
    ).encode("ascii")
    with open(path, "wb") as f:
        f.write(header)
        f.write(pts.tobytes(order="C"))
