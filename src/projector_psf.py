"""Continuous Gaussian convolution of the source image's Closest pixel cells.

The two-dimensional field is evaluated separably: an X-oversampled float table
stores the exact pixel-cell integrals, and the shader sums native rows with a
small continuously sampled CDF kernel. This has O(q*W*H) storage rather than
O(q*q*W*H), without averaging rows or replacing the native pixel support.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

DEFAULT_PSF = dict(model="gaussian", sigma_px=1.0, subsamples_per_pixel=32,
                   boundary="constant", sigma_unit="source_pixel")
SETTINGS_KEY = "projector_psf_settings"
STATE_KEY = "projector_psf_effective"
LOG_KEY = "projector_psf_update_log"
NODE_NAME = "Projector Gaussian PSF"
SCHEMA = "core-sim.projector_psf.v1"
OWNED = "continuous_projector_psf"
_RETIRED_GROUPS = []
_RETIRED_IMAGES = []


def parse_settings(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULT_PSF):
        raise ValueError("projector_psf accepts " + ", ".join(DEFAULT_PSF))
    result = {**DEFAULT_PSF, **value}
    sigma = result["sigma_px"]
    if isinstance(sigma, bool) or not isinstance(sigma, (int, float)) or not math.isfinite(sigma) or sigma < 0:
        raise ValueError("projector_psf.sigma_px must be finite and nonnegative")
    q = result["subsamples_per_pixel"]
    if isinstance(q, bool) or not isinstance(q, int) or q not in (4, 8, 16, 32, 64, 128, 256):
        raise ValueError("projector_psf.subsamples_per_pixel must be 4, 8, 16, 32, 64, 128 or 256")
    if result["model"] != "gaussian" or result["boundary"] not in ("constant", "repeat"):
        raise ValueError("projector_psf supports gaussian with constant-zero or repeat boundary")
    if result["sigma_unit"] not in ("source_pixel", "carrier_pixel"):
        raise ValueError("projector_psf.sigma_unit must name the source/carrier pixel; native (U,V) units require conversion")
    result["sigma_px"] = float(sigma)
    if support_radius(sigma) > 128:
        raise ValueError("projector_psf exceeds the 128-pixel shader support budget")
    return result


def support_radius(sigma):
    return int(math.ceil(8.0 * sigma + 0.5)) if sigma else 0


def normal_cdf(x):
    a = np.asarray(x, dtype=np.float64)
    return np.fromiter((0.5 * math.erfc(-float(v) / math.sqrt(2.0)) for v in a.flat),
                       dtype=np.float64, count=a.size).reshape(a.shape)


def cell_weight(distance, sigma):
    """Integral over one unit pixel, with distance = pixel centre - query."""
    if sigma <= 0:
        raise ValueError("CDF cell weights require a positive sigma")
    d = np.asarray(distance, dtype=np.float64)
    return normal_cdf((d + 0.5) / sigma) - normal_cdf((d - 0.5) / sigma)


def dense_x_table(pixels, settings):
    """Return H x ((W+2*halo)*q) x C; rows remain distinct native rows.

    Texel j samples x=(j+0.5)/q-0.5-halo, so normalized source UVs
    map to (u*W+halo)/(W+2*halo). Source pixel centres remain integers.
    """
    settings = parse_settings(settings)
    sigma, q = settings["sigma_px"], settings["subsamples_per_pixel"]
    a = np.asarray(pixels, dtype=np.float32)
    if a.ndim == 2:
        a = a[..., None]
    if a.ndim != 3 or not a.shape[0] or not a.shape[1] or not np.isfinite(a).all():
        raise ValueError("Expected finite nonempty H x W [x C] source pixels")
    if sigma == 0:
        raise ValueError("sigma=0 must use the original Closest image directly")
    radius = support_radius(sigma)
    halo = radius if settings["boundary"] == "constant" else 0
    h, w, channels = a.shape
    width = w + 2 * halo
    # One generated Blender image is RGBA float32, irrespective of source C.
    if h * width * q * 16 > 4 * 1024**3:
        raise ValueError("Continuous PSF X table exceeds 4 GiB; reduce the source region or explicitly choose a smaller sampling density")
    mode = "constant" if settings["boundary"] == "constant" else "wrap"
    padded = np.pad(a, ((0, 0), (halo + radius, halo + radius), (0, 0)), mode=mode)
    output = np.empty((h, width * q, channels), dtype=np.float32)
    offsets = np.arange(-radius, radius + 1)
    for phase in range(q):
        fractional = (phase + 0.5) / q - 0.5
        weights = cell_weight(offsets - fractional, sigma)
        values = np.zeros((h, width, channels), dtype=np.float32)
        for offset, weight in zip(offsets, weights):
            if weight:
                values += padded[:, radius + offset:radius + offset + width] * np.float32(weight)
        output[:, phase::q] = values
    return output, halo


def kernel_table(settings):
    settings = parse_settings(settings)
    q = settings["subsamples_per_pixel"]
    extent = support_radius(settings["sigma_px"]) + 1
    distances = (np.arange(2 * extent * q, dtype=np.float64) + 0.5) / q - extent
    return cell_weight(distances, settings["sigma_px"]).astype(np.float32), extent


def gaussian_pixel_field(values, u, sigma):
    """把原生分段常数像素场与归一化高斯卷积，在任意 u 连续求值。"""
    values = np.asarray(values, np.float64)
    u = np.asarray(u, np.float64)
    if values.ndim not in (1, 2) or not len(values) or not np.isfinite(values).all():
        raise ValueError('Expected finite nonempty scalar or RGB pixel profile')
    if not np.isfinite(u).all() or not math.isfinite(sigma) or sigma < 0:
        raise ValueError('Coordinates and sigma must be finite; sigma must be nonnegative')
    nearest = np.floor(u+.5).astype(np.int64)
    if sigma == 0:
        return values[nearest % len(values)]
    out = np.zeros(u.shape+values.shape[1:], np.float64)
    for offset in range(-int(math.ceil(8*sigma+.5)), int(math.ceil(8*sigma+.5))+1):
        n = nearest+offset
        weight = normal_cdf((n+.5-u)/sigma)-normal_cdf((n-.5-u)/sigma)
        if values.ndim == 2:
            weight = weight[..., None]
        out += values[n % len(values)]*weight
    return out


def invariant_profile(pixels):
    """返回原生灰度/RGB 图像的不变轴；不平均不同像素、不做降采样。"""
    a = np.asarray(pixels)
    if a.ndim not in (2, 3) or not np.isfinite(a).all():
        raise ValueError('Expected finite H x W scalar or RGB image')
    if np.array_equal(a, np.broadcast_to(a[0:1], a.shape)):
        return 'x', a[0].copy()
    if np.array_equal(a, np.broadcast_to(a[:, 0:1], a.shape)):
        return 'y', a[:, 0].copy()
    raise ValueError('Continuous projector PSF currently requires row- or column-invariant fringes; arbitrary 2D textures are not supported')


def dense_profile(profile, settings):
    settings = parse_settings(settings)
    if settings['boundary'] != 'repeat':
        raise ValueError('dense_profile requires repeat boundary')
    q = settings['subsamples_per_pixel']
    u = (np.arange(len(profile)*q, dtype=np.float64)+.5)/q-.5
    return gaussian_pixel_field(profile, u, settings['sigma_px']).astype(np.float32)



def implementation_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _image(name, values):
    import bpy
    existing = bpy.data.images.get(name)
    if existing is not None:
        return existing
    a = np.asarray(values, dtype=np.float32)
    if a.ndim == 1:
        a = a[None, :, None]
    h, w, channels = a.shape
    image = bpy.data.images.new(name, width=w, height=h, alpha=False,
                                float_buffer=True, is_data=True)
    image[OWNED] = True
    image.colorspace_settings.name = "Non-Color"
    rgba = np.ones((h, w, 4), dtype=np.float32)
    rgba[..., :3] = a if channels == 3 else a[..., :1]
    image.pixels.foreach_set(rgba.ravel())
    image.update()
    return image


def _build_group(table, kernel, width, height, halo, settings):
    import bpy
    group = bpy.data.node_groups.new("Continuous Gaussian pixel-cell field", "ShaderNodeTree")
    group[OWNED] = True
    if bpy.app.version >= (4, 0):
        group.interface.new_socket(name="Vector", in_out="INPUT", socket_type="NodeSocketVector")
        group.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    else:
        group.inputs.new("NodeSocketVector", "Vector")
        group.outputs.new("NodeSocketColor", "Color")
    nodes, links = group.nodes, group.links
    entry, out = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")

    def wire(value, socket):
        if isinstance(value, (float, int)):
            socket.default_value = value
        else:
            links.new(value, socket)

    def scalar(operation, a, b=0.0):
        node = nodes.new("ShaderNodeMath"); node.operation = operation
        wire(a, node.inputs[0]); wire(b, node.inputs[1])
        return node.outputs[0]

    def vector(x, y):
        node = nodes.new("ShaderNodeCombineXYZ")
        wire(x, node.inputs[0]); wire(y, node.inputs[1])
        return node.outputs[0]

    def sample(image, coordinate, name):
        node = nodes.new("ShaderNodeTexImage"); node.name = name
        node.image = image; node.interpolation = "Linear"
        node.extension = "REPEAT" if image == table and settings["boundary"] == "repeat" else "CLIP"
        links.new(coordinate, node.inputs["Vector"])
        return node.outputs["Color"]

    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(entry.outputs["Vector"], sep.inputs[0])
    x = scalar("DIVIDE", scalar("ADD", scalar("MULTIPLY", sep.outputs[0], float(width)), float(halo)), float(width + 2 * halo))
    vheight = scalar("MULTIPLY", sep.outputs[1], float(height))
    row = scalar("FLOOR", vheight)
    fraction = scalar("SUBTRACT", vheight, row)
    radius = support_radius(settings["sigma_px"])
    extent = radius + 1
    total = None
    for offset in range(-radius, radius + 1):
        native_row = scalar("ADD", row, float(offset))
        if settings["boundary"] == "repeat":
            native_row = scalar("FLOORED_MODULO", native_row, float(height))
        y = scalar("DIVIDE", scalar("ADD", native_row, 0.5), float(height))
        color = sample(table, vector(x, y), f"Native row {offset:+d}")
        distance = scalar("SUBTRACT", float(offset) + 0.5, fraction)
        kernel_u = scalar("DIVIDE", scalar("ADD", distance, float(extent)), float(2 * extent))
        weight = sample(kernel, vector(kernel_u, 0.5), f"CDF row weight {offset:+d}")
        multiply = nodes.new("ShaderNodeMixRGB"); multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1.0
        links.new(color, multiply.inputs[1]); links.new(weight, multiply.inputs[2])
        if total is None:
            total = multiply.outputs[0]
        else:
            add = nodes.new("ShaderNodeVectorMath"); add.operation = "ADD"
            links.new(total, add.inputs[0]); links.new(multiply.outputs[0], add.inputs[1])
            total = add.outputs[0]
    links.new(total, out.inputs["Color"])
    return group


def _discard_group(group):
    import bpy
    if group is None or not group.get(OWNED) or group.users:
        return
    images = {node.image for node in group.nodes if node.type == "TEX_IMAGE" and node.image}
    bpy.data.node_groups.remove(group)
    for image in images:
        if image.get(OWNED) and not image.users:
            bpy.data.images.remove(image)


def _collect_retired(*_args):
    """Free IDs only after Cycles has synchronized the replacement shader.

    Freeing a preceding frame's image before synchronization can let Blender
    reuse its ID address while a persistent Cycles session still caches it.
    Keeping retired IDs alive until render_post prevents that aliasing.
    """
    import bpy
    while _RETIRED_GROUPS:
        _discard_group(_RETIRED_GROUPS.pop())
    while _RETIRED_IMAGES:
        image = _RETIRED_IMAGES.pop()
        if not image.users:
            bpy.data.images.remove(image)


def retire_source_image(image):
    if image is not None and image.get("projector_psf_native_source") and not image.users:
        _RETIRED_IMAGES.append(image)


def _group_fingerprint(group):
    """Record actual shader operations, constants, image identities and links."""
    records = []
    for node in group.nodes:
        defaults = []
        for socket in node.inputs:
            if socket.is_linked or not hasattr(socket, "default_value"):
                continue
            value = socket.default_value
            if not isinstance(value, (str, int, float, bool)):
                try:
                    value = list(value)
                except TypeError:
                    continue
            defaults.append([socket.identifier, value])
        records.append([node.name, node.bl_idname, getattr(node, "operation", None),
                        getattr(node, "blend_type", None), defaults])
    links = sorted([link.from_node.name, link.from_socket.identifier,
                    link.to_node.name, link.to_socket.identifier] for link in group.links)
    return _digest([records, links])


def apply_projector_psf(projector, settings=None):
    import bpy
    from projector import _find_projector_spot
    if _collect_retired not in bpy.app.handlers.render_post:
        bpy.app.handlers.render_post.append(_collect_retired)
    if settings is None:
        settings = json.loads(projector[SETTINGS_KEY])
    settings = parse_settings(settings)
    projector[SETTINGS_KEY] = json.dumps(settings, sort_keys=True)
    tree = _find_projector_spot(projector).data.node_tree
    original, emission = tree.nodes["Image Texture"], tree.nodes["Emission"]
    if original.interpolation != "Closest":
        raise ValueError("Projector PSF requires native Closest pixel cells")
    original.extension = "REPEAT" if settings["boundary"] == "repeat" else "CLIP"
    w, h = map(int, original.image.size)
    source = Path(bpy.path.abspath(original.image.filepath)).resolve()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    previous = tree.nodes.get(NODE_NAME)
    old_group = previous.node_tree if previous else None
    state = dict(schema=SCHEMA, projector=projector.name, **settings, source_path=str(source), source_sha256=source_hash,
                 physical_resolution=[w, h], stage="projector_emission_before_surface_reflection",
                 native_sampling="Closest", backend="separable_cdf_x_table_y_shader",
                 camera_image_filter=False, coarse_requantization=False,
                 effective_source="Image Texture", implementation_sha256=implementation_sha256())
    effective = original
    if settings["sigma_px"]:
        # Blender ID names are limited to 63 bytes. Keep lookup keys intact.
        key = _digest([SCHEMA, source_hash, settings, state["implementation_sha256"]])[:48]
        pixels = np.empty(w * h * 4, dtype=np.float32)
        original.image.pixels.foreach_get(pixels)
        rgb = pixels.reshape(h, w, 4)[..., :3]
        profile = None
        if settings["boundary"] == "repeat":
            try:
                axis, profile = invariant_profile(rgb)
            except ValueError:
                pass
        if profile is not None:
            values = dense_profile(profile, settings)
            values = values[None, :, :] if axis == "x" else values[:, None, :]
            table = _image("PSF.P." + key, values)
            group = _build_profile_group(table)
            state.update(backend="periodic_invariant_cdf_profile", invariant_profile_axis=axis,
                         numerical_lut_resolution=list(table.size), profile_name=table.name)
        else:
            table = bpy.data.images.get("PSF.X." + key)
            halo = support_radius(settings["sigma_px"]) if settings["boundary"] == "constant" else 0
            if table is None:
                if np.array_equal(rgb[..., 0], rgb[..., 1]) and np.array_equal(rgb[..., 0], rgb[..., 2]):
                    rgb = rgb[..., :1]
                values, halo = dense_x_table(rgb, settings)
                table = _image("PSF.X." + key, values)
            kernel_name = "PSF.K." + _digest([state["implementation_sha256"], settings["sigma_px"], settings["subsamples_per_pixel"]])[:48]
            kernel = bpy.data.images.get(kernel_name)
            if kernel is None:
                values, _extent = kernel_table(settings)
                kernel = _image(kernel_name, values)
            group = _build_group(table, kernel, w, h, halo, settings)
            state.update(x_table_resolution=list(table.size), x_halo_px=halo,
                         kernel_resolution=list(kernel.size), y_support_radius_px=support_radius(settings["sigma_px"]),
                         x_table_name=table.name, kernel_name=kernel.name)
        node = previous or tree.nodes.new("ShaderNodeGroup")
        node.name = NODE_NAME; node.label = "Continuous 2D Gaussian pixel-cell PSF"
        node.node_tree = group
        for link in list(node.inputs["Vector"].links):
            tree.links.remove(link)
        links = list(original.inputs["Vector"].links)
        if len(links) != 1:
            raise RuntimeError("Native projection vector is missing or ambiguous")
        tree.links.new(links[0].from_socket, node.inputs["Vector"])
        effective = node
        state.update(effective_source=NODE_NAME, numerical_interpolation="Linear",
                     shader_sha256=_group_fingerprint(group),
                     texture_bytes=int(table.size[0]) * int(table.size[1]) * 16)
    elif previous:
        tree.nodes.remove(previous)
    for link in list(emission.inputs["Color"].links):
        tree.links.remove(link)
    tree.links.new(effective.outputs["Color"], emission.inputs["Color"])
    vector_link = original.inputs["Vector"].links[0]
    state.update(emission_connection={"from_node": effective.name, "from_socket": "Color",
                 "to_node": emission.name, "to_socket": "Color"},
                 projection_vector_connection={"from_node": vector_link.from_node.name,
                 "from_socket": vector_link.from_socket.name},
                 native_color_space=original.image.colorspace_settings.name,
                 truncation_sigma=8.0)
    if old_group is not None:
        _RETIRED_GROUPS.append(old_group)
    projector[STATE_KEY] = json.dumps(state, sort_keys=True)
    validate_effective_psf(projector)
    if projector.get(LOG_KEY):
        with Path(projector[LOG_KEY]).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(state, sort_keys=True) + "\n")
    return state


def validate_effective_psf(projector):
    from projector import _find_projector_spot
    state = json.loads(projector[STATE_KEY])
    settings = parse_settings(json.loads(projector[SETTINGS_KEY]))
    tree = _find_projector_spot(projector).data.node_tree
    native = tree.nodes["Image Texture"]
    source = tree.nodes.get(state["effective_source"])
    links = list(tree.nodes["Emission"].inputs["Color"].links)
    if source is None or len(links) != 1 or links[0].from_node != source or links[0].from_socket != source.outputs["Color"]:
        raise RuntimeError("Requested projector PSF is not the effective Emission source")
    if (native.interpolation != "Closest" or list(native.image.size) != state["physical_resolution"]
            or native.image.colorspace_settings.name != "Non-Color"):
        raise RuntimeError("Projector PSF changed the native pixel field or physical resolution")
    if any(state[k] != v for k, v in settings.items()):
        raise RuntimeError("Effective projector PSF settings differ from the request")
    if settings["sigma_px"]:
        if _group_fingerprint(source.node_tree) != state["shader_sha256"]:
            raise RuntimeError("Continuous projector PSF shader operations or connections were modified")
        mapping = list(source.inputs["Vector"].links)
        original_mapping = list(native.inputs["Vector"].links)
        if len(mapping) != 1 or len(original_mapping) != 1 or mapping[0].from_socket != original_mapping[0].from_socket:
            raise RuntimeError("Projector PSF changed the projection coordinate mapping")
        image_nodes = [n for n in source.node_tree.nodes if n.type == "TEX_IMAGE"]
        profile_backend = state["backend"] == "periodic_invariant_cdf_profile"
        expected_count = 1 if profile_backend else 2 * (2 * support_radius(settings["sigma_px"]) + 1)
        if len(image_nodes) != expected_count:
            raise RuntimeError("Projector PSF integration nodes are incomplete")
        for node in image_nodes:
            is_table = node.name.startswith("Native row")
            if profile_backend:
                expected, expected_size = state["profile_name"], state["numerical_lut_resolution"]
            else:
                expected = state["x_table_name"] if is_table else state["kernel_name"]
                expected_size = state["x_table_resolution"] if is_table else state["kernel_resolution"]
            extension = "REPEAT" if profile_backend or is_table and settings["boundary"] == "repeat" else "CLIP"
            if (node.image is None or node.image.name != expected or list(node.image.size) != expected_size
                    or node.interpolation != "Linear" or node.extension != extension
                    or node.image.colorspace_settings.name != "Non-Color" or not node.image.is_float):
                raise RuntimeError("Continuous projector PSF table or sampling was modified")
    return state


def configure_recording(projector, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    projector[LOG_KEY] = str(path)
    state = validate_effective_psf(projector)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(state, sort_keys=True) + "\n")
    return state


def _build_profile_group(table):
    import bpy
    group = bpy.data.node_groups.new("Periodic Gaussian pixel-cell profile", "ShaderNodeTree")
    group[OWNED] = True
    if bpy.app.version >= (4, 0):
        group.interface.new_socket(name="Vector", in_out="INPUT", socket_type="NodeSocketVector")
        group.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    else:
        group.inputs.new("NodeSocketVector", "Vector")
        group.outputs.new("NodeSocketColor", "Color")
    entry = group.nodes.new("NodeGroupInput")
    output = group.nodes.new("NodeGroupOutput")
    node = group.nodes.new("ShaderNodeTexImage")
    node.image = table
    node.interpolation = "Linear"
    node.extension = "REPEAT"
    group.links.new(entry.outputs["Vector"], node.inputs["Vector"])
    group.links.new(node.outputs["Color"], output.inputs["Color"])
    return group


def configure_scene_recording(output_dir):
    """Start one run receipt for all effective projector sources, before capture."""
    import bpy
    from projector import SIM_TAG_KEY, TAG_PROJECTOR_ROOT
    path = Path(output_dir) / "projector_psf_updates.jsonl"
    path.unlink(missing_ok=True)
    states = []
    for obj in bpy.context.scene.objects:
        if obj.get(SIM_TAG_KEY) == TAG_PROJECTOR_ROOT and obj.get(SETTINGS_KEY):
            states.append(configure_recording(obj, path))
    if not states:
        raise RuntimeError("No effective projector PSF was configured for capture")
    return states
