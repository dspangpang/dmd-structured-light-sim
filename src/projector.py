import bpy
import os
import math
import sys
import json
from pathlib import Path

SIM_TAG_KEY = "sim_tag"
TAG_PROJECTOR_ROOT = "projector_root"
TAG_PROJECTOR_SPOT = "projector_spot"
PROJECTOR_TEXTURE_MIRROR_X_KEY = "projector_texture_mirror_x"
PROJECTOR_TEXTURE_MIRROR_Y_KEY = "projector_texture_mirror_y"
PROJECTOR_TEXTURE_INTERPOLATION_KEY = "projector_texture_interpolation"
PROJECTOR_EXPECTED_RES_X_KEY = "projector_expected_resolution_x"
PROJECTOR_EXPECTED_RES_Y_KEY = "projector_expected_resolution_y"
PROJECTOR_INTRINSIC_KEY = "projector_opencv_intrinsic"


def _projector_settings_owner_module():
    prop = getattr(bpy.types.Object, "proj_settings", None)
    keywords = getattr(prop, "keywords", {})
    settings_type = keywords.get("type") if keywords else None
    return getattr(settings_type, "__module__", "")


def _load_projectors_addon():
    repo_root = Path(__file__).resolve().parents[1]
    vendor_parent = repo_root / "3rdparty"
    vendor_dir = vendor_parent / "Projectors"
    if not vendor_dir.exists():
        raise FileNotFoundError(f"Vendored Projectors add-on not found: {vendor_dir}")

    vendor_parent_str = str(vendor_parent)
    if vendor_parent_str not in sys.path:
        sys.path.insert(0, vendor_parent_str)

    import Projectors
    from Projectors import projector as projectors_projector

    loaded_dir = Path(Projectors.__file__).resolve().parent
    if loaded_dir != vendor_dir.resolve():
        raise RuntimeError(
            "Loaded Projectors add-on is not the vendored copy: "
            f"{loaded_dir} != {vendor_dir.resolve()}"
        )

    if not hasattr(bpy.types.Object, "proj_settings"):
        projectors_projector.register()
    else:
        owner_module = _projector_settings_owner_module()
        if owner_module and owner_module != projectors_projector.__name__:
            print(
                "[Projector] Existing proj_settings registration comes from "
                f"{owner_module}; using callback-independent vendored updates."
            )

    return projectors_projector


def _initialize_projector(projectors_projector, dlp_obj):
    """Initialize with explicit vendored updates, independent of registered callbacks."""
    proj_settings = dlp_obj.proj_settings
    proj_settings["throw_ratio"] = 0.8
    proj_settings["power"] = 1000.0
    proj_settings["projected_texture"] = projectors_projector.Textures.CHECKER.value
    proj_settings["h_shift"] = 0.0
    proj_settings["v_shift"] = 0.0
    proj_settings["projected_color"] = projectors_projector.random_color()
    proj_settings["resolution"] = "1920x1080"
    proj_settings["use_custom_texture_res"] = True

    projectors_projector.update_throw_ratio(proj_settings, bpy.context)
    projectors_projector.update_projected_texture(proj_settings, bpy.context)
    projectors_projector.update_resolution(proj_settings, bpy.context)
    projectors_projector.update_checker_color(proj_settings, bpy.context)
    projectors_projector.update_lens_shift(proj_settings, bpy.context)
    projectors_projector.update_power(proj_settings, bpy.context)
    projectors_projector.update_pixel_grid(proj_settings, bpy.context)


def _set_custom_texture_image(projectors_projector, dlp_obj, image):
    spot = _find_projector_spot(dlp_obj)
    if spot is None or not spot.data or not spot.data.node_tree:
        raise RuntimeError("[projector] Spot light or its node tree not found")

    root_tree = spot.data.node_tree
    image_node = root_tree.nodes["Image Texture"]
    image_node.image = image
    image.colorspace_settings.name = "Non-Color"
    proj_settings = dlp_obj.proj_settings
    proj_settings["projected_texture"] = projectors_projector.Textures.CUSTOM_TEXTURE.value
    proj_settings["use_custom_texture_res"] = True
    projectors_projector.update_projected_texture(proj_settings, bpy.context)
    projectors_projector.refresh_projector_geometry(dlp_obj)

    # ``proj_settings`` may be registered by a globally installed copy of the
    # Projectors add-on. In that case its RNA enum can remain on the default
    # checker texture even though the vendored ID property was changed above.
    # Enforce the effective light-node state directly so scripted renders are
    # independent of whichever add-on copy Blender registered first.
    emission_node = root_tree.nodes["Emission"]
    emission_color = emission_node.inputs["Color"]
    for link in list(emission_color.links):
        root_tree.links.remove(link)
    root_tree.links.new(image_node.outputs["Color"], emission_color)

    width = float(image.size[0])
    height = float(image.size[1])
    if width <= 0.0 or height <= 0.0:
        raise ValueError(f"Invalid custom projector image size: {width:g}x{height:g}")
    throw_ratio = float(proj_settings.get("throw_ratio", 0.0))
    if throw_ratio <= 0.0:
        raise ValueError(f"Invalid projector throw ratio: {throw_ratio:g}")
    inverted_aspect_ratio = height / width

    group_nodes = root_tree.nodes["Group"].node_tree.nodes
    projection_mapping = group_nodes["Mapping.001"]
    _set_mapping_scale(projection_mapping, 0, 1.0 / throw_ratio)
    _set_mapping_scale(
        projection_mapping,
        1,
        inverted_aspect_ratio / throw_ratio,
    )

    horizontal_shift = float(proj_settings.get("h_shift", 0.0)) / 100.0
    vertical_shift = float(proj_settings.get("v_shift", 0.0)) / 100.0
    _set_mapping_location(
        projection_mapping,
        0,
        horizontal_shift / throw_ratio,
    )
    _set_mapping_location(
        projection_mapping,
        1,
        vertical_shift * inverted_aspect_ratio / throw_ratio,
    )
    dlp_obj.data.shift_x = horizontal_shift
    dlp_obj.data.shift_y = vertical_shift * inverted_aspect_ratio

    pixel_grid = root_tree.nodes.get("pixel_grid")
    if pixel_grid is not None and pixel_grid.node_tree is not None:
        pixel_grid_nodes = pixel_grid.node_tree.nodes
        pixel_grid_nodes["_width"].outputs[0].default_value = width
        pixel_grid_nodes["_height"].outputs[0].default_value = height

    print(
        "[Projector] Custom texture node state enforced: "
        f"{int(width)}x{int(height)}"
    )


def set_projector_pixel_grid(dlp_obj, enabled):
    """Set the pixel-grid state and enforce the effective output-node link."""
    projectors_projector = _load_projectors_addon()
    proj_settings = dlp_obj.proj_settings
    enabled = bool(enabled)
    proj_settings["show_pixel_grid"] = enabled
    projectors_projector.update_pixel_grid(proj_settings, bpy.context)

    spot = _find_projector_spot(dlp_obj)
    if spot is None or not spot.data or not spot.data.node_tree:
        raise RuntimeError("[projector] Spot light or its node tree not found")
    root_tree = spot.data.node_tree
    light_output = root_tree.nodes["Light Output"].inputs[0]
    for link in list(light_output.links):
        root_tree.links.remove(link)
    source = root_tree.nodes["pixel_grid"] if enabled else root_tree.nodes["Emission"]
    root_tree.links.new(source.outputs[0], light_output)


def validate_projector_projection_state(
    dlp_obj,
    expected_image_path,
    *,
    pixel_grid_enabled=False,
):
    """Fail before rendering when the effective projector node state is wrong."""
    spot = _find_projector_spot(dlp_obj)
    if spot is None or not spot.data or not spot.data.node_tree:
        raise RuntimeError("[projector] Spot light or its node tree not found")
    root_tree = spot.data.node_tree
    image_node = root_tree.nodes["Image Texture"]
    emission_node = root_tree.nodes["Emission"]
    light_output_node = root_tree.nodes["Light Output"]
    if image_node.image is None:
        raise RuntimeError("[projector] Custom Image Texture has no image")

    expected_path = Path(bpy.path.abspath(str(expected_image_path))).resolve()
    actual_path = Path(bpy.path.abspath(image_node.image.filepath)).resolve()
    if actual_path != expected_path:
        raise RuntimeError(
            "[projector] Effective custom image differs from the requested image: "
            f"{actual_path} != {expected_path}"
        )

    actual_size = (int(image_node.image.size[0]), int(image_node.image.size[1]))
    expected_size = (
        int(dlp_obj[PROJECTOR_EXPECTED_RES_X_KEY]),
        int(dlp_obj[PROJECTOR_EXPECTED_RES_Y_KEY]),
    )
    if actual_size != expected_size:
        raise RuntimeError(
            "[projector] Effective custom image resolution differs from the App device: "
            f"{actual_size[0]}x{actual_size[1]} != "
            f"{expected_size[0]}x{expected_size[1]}"
        )

    from projector_psf import SETTINGS_KEY, validate_effective_psf
    psf_state = validate_effective_psf(dlp_obj) if dlp_obj.get(SETTINGS_KEY) else None
    effective_node = root_tree.nodes[psf_state["effective_source"]] if psf_state else image_node
    emission_links = list(emission_node.inputs["Color"].links)
    if (
        len(emission_links) != 1
        or emission_links[0].from_node != effective_node
        or emission_links[0].from_socket != effective_node.outputs["Color"]
    ):
        raise RuntimeError(
            "[projector] Requested projector field is not the effective Emission color source"
        )

    expected_output_node = (
        root_tree.nodes["pixel_grid"]
        if bool(pixel_grid_enabled)
        else emission_node
    )
    output_links = list(light_output_node.inputs[0].links)
    if len(output_links) != 1 or output_links[0].from_node != expected_output_node:
        raise RuntimeError(
            "[projector] Effective Light Output disagrees with the requested pixel-grid state"
        )

    mapping = root_tree.nodes["Group"].node_tree.nodes["Mapping.001"]
    throw_ratio = float(dlp_obj.proj_settings.get("throw_ratio", 0.0))
    if throw_ratio <= 0.0:
        raise RuntimeError("[projector] Effective throw ratio must be positive")
    expected_scale = (
        1.0 / throw_ratio,
        (actual_size[1] / actual_size[0]) / throw_ratio,
    )
    actual_scale = (
        _get_mapping_scale(mapping, 0),
        _get_mapping_scale(mapping, 1),
    )
    # Blender stores Mapping node values as float32. Compare against the
    # analytical Python float with a tolerance above one float32 ULP while
    # still rejecting any meaningful projector-geometry mismatch.
    if any(
        not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1.0e-6)
        for actual, expected in zip(actual_scale, expected_scale)
    ):
        raise RuntimeError(
            "[projector] Effective projection mapping does not match the custom image: "
            f"scale={actual_scale}, expected={expected_scale}"
        )

    # Check the effective node geometry against the OpenCV K, independently
    # of the lens-shift settings that constructed it. TEXTURE mapping applies
    # inverse translation/scale, followed by the add-on's UV +0.5 offset.
    intrinsic = dlp_obj.get(PROJECTOR_INTRINSIC_KEY)
    if intrinsic is None or len(intrinsic) != 9:
        raise RuntimeError("[projector] Missing OpenCV intrinsic contract")
    location = mapping.inputs["Location"].default_value
    width, height = actual_size
    actual_intrinsic = (
        width / actual_scale[0], height / actual_scale[1],
        (0.5 - location[0] / actual_scale[0]) * width - 0.5,
        (0.5 + location[1] / actual_scale[1]) * height - 0.5,
    )
    expected_intrinsic = (intrinsic[0], intrinsic[4], intrinsic[2], intrinsic[5])
    if any(abs(a - b) > 0.02 for a, b in zip(actual_intrinsic, expected_intrinsic)):
        raise RuntimeError(
            "[projector] Effective texture projection disagrees with OpenCV K: "
            f"actual={actual_intrinsic}, expected={expected_intrinsic}"
        )

    return {
        "projector": dlp_obj.name,
        "image_path": str(actual_path),
        "image_size": list(actual_size),
        "texture_mode": "custom_image",
        "pixel_grid_enabled": bool(pixel_grid_enabled),
        "mapping_scale_xy": list(actual_scale),
        "projector_psf": psf_state,
    }


def _load_projector_image(image_path, *, immutable=False):
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Projector image not found: {image_path}")
    try:
        img = bpy.data.images.load(filepath=image_path, check_existing=not immutable)
    except Exception as exc:
        raise RuntimeError(f"Failed to load projector image: {image_path}") from exc
    img.colorspace_settings.name = "Non-Color"
    if immutable:
        img["projector_psf_native_source"] = True
    return img


def _validate_projector_image_resolution(img, expected_x, expected_y):
    actual = (int(img.size[0]), int(img.size[1]))
    expected = (int(expected_x), int(expected_y))
    if actual != expected:
        raise ValueError(
            "Projector image resolution does not match the App device: "
            f"image={actual[0]}x{actual[1]}, App={expected[0]}x{expected[1]}, "
            f"path={img.filepath}. Regenerate/select a matching fringe sequence; "
            "the image is not allowed to redefine projector geometry."
        )


def _resolve_repo_path(path):
    repo_root = Path(__file__).resolve().parents[1]
    if path.startswith("//"):
        return str(repo_root / path[2:])
    if os.path.isabs(path):
        return path
    return str(repo_root / path)


def _find_projector_spot(dlp_obj):
    for ch in dlp_obj.children:
        if ch.type == 'LIGHT' and getattr(ch.data, "type", None) == 'SPOT':
            return ch
    return None


def _set_projector_energy(dlp_obj, energy):
    spot = _find_projector_spot(dlp_obj)
    if spot is None or not spot.data:
        raise RuntimeError("[projector] Spot light not found for energy update")

    energy = float(energy)
    dlp_obj.proj_settings["power"] = energy
    spot.data.energy = energy

    if spot.data.node_tree:
        emission = spot.data.node_tree.nodes.get("Emission")
        if emission and "Strength" in emission.inputs:
            emission.inputs["Strength"].default_value = energy
            print(f"[Projector] Emission strength set to: {energy}")
        else:
            print("[Projector] Warning: Emission strength node not found")


def set_projector_energy(dlp_obj, energy):
    _set_projector_energy(dlp_obj, energy)


def _set_mapping_scale(mapping_node, axis, value):
    if bpy.app.version < (2, 81):
        mapping_node.scale[axis] = value
    else:
        scale_input = mapping_node.inputs.get("Scale") or mapping_node.inputs[3]
        scale_input.default_value[axis] = value


def _get_mapping_scale(mapping_node, axis):
    if bpy.app.version < (2, 81):
        return float(mapping_node.scale[axis])
    scale_input = mapping_node.inputs.get("Scale") or mapping_node.inputs[3]
    return float(scale_input.default_value[axis])


def _set_mapping_location(mapping_node, axis, value):
    if bpy.app.version < (2, 81):
        mapping_node.translation[axis] = value
    else:
        location_input = mapping_node.inputs.get("Location") or mapping_node.inputs[1]
        location_input.default_value[axis] = value


def _get_projector_texture_mapping(dlp_obj):
    spot = _find_projector_spot(dlp_obj)
    if spot is None or not spot.data or not spot.data.node_tree:
        raise RuntimeError("[projector] Spot light or its node tree not found")

    group = spot.data.node_tree.nodes.get("Group")
    if group is None or group.node_tree is None:
        raise RuntimeError("[projector] Projector texture node group not found")

    nodes = group.node_tree.nodes
    mapping = nodes.get("Mapping")
    if mapping is None:
        mapping_nodes = [node for node in nodes if node.bl_idname == "ShaderNodeMapping"]
        if not mapping_nodes:
            raise RuntimeError("[projector] Projector texture Mapping node not found")
        mapping = mapping_nodes[0]
    return mapping


def _set_projector_texture_mirror(dlp_obj, mirror_x, mirror_y):
    mapping = _get_projector_texture_mapping(dlp_obj)
    scale_x = -1.0 if mirror_x else 1.0
    scale_y = -1.0 if mirror_y else 1.0
    _set_mapping_scale(mapping, 0, scale_x)
    _set_mapping_scale(mapping, 1, scale_y)
    print("[Projector] Vendored add-on texture basis adapter applied.")


def _set_projector_texture_interpolation(dlp_obj, interpolation):
    spot = _find_projector_spot(dlp_obj)
    if spot is None or not spot.data or not spot.data.node_tree:
        raise RuntimeError("[projector] Spot light or its node tree not found")

    node = spot.data.node_tree.nodes.get("Image Texture")
    if node is None:
        raise RuntimeError("[projector] Custom Image Texture node not found")

    requested = str(interpolation or "Linear").strip()
    aliases = {
        "nearest": "Closest",
        "neighbor": "Closest",
        "point": "Closest",
        "closest": "Closest",
        "linear": "Linear",
        "cubic": "Cubic",
        "smart": "Smart",
    }
    value = aliases.get(requested.lower(), requested)
    try:
        node.interpolation = value
    except TypeError as exc:
        raise ValueError(
            f"unsupported projector texture interpolation: {interpolation}"
        ) from exc
    dlp_obj[PROJECTOR_TEXTURE_INTERPOLATION_KEY] = node.interpolation
    print(f"[Projector] Texture interpolation: {node.interpolation}")


def add_projector(cfg):
    projectors_projector = _load_projectors_addon()
    dlp = projectors_projector.create_projector(bpy.context)
    _initialize_projector(projectors_projector, dlp)

    dlp[PROJECTOR_INTRINSIC_KEY] = [
        float(value) for row in cfg.PROJECTOR_INTRINSIC_MATRIX_PX for value in row
    ]

    # tag root
    dlp[SIM_TAG_KEY] = TAG_PROJECTOR_ROOT
    # Fixed basis adapter for the vendored Projectors add-on.
    texture_mirror_x = True
    texture_mirror_y = True
    cfg.PROJECTOR_TEXTURE_MIRROR_X = True
    cfg.PROJECTOR_TEXTURE_MIRROR_Y = True
    dlp[PROJECTOR_TEXTURE_MIRROR_X_KEY] = texture_mirror_x
    dlp[PROJECTOR_TEXTURE_MIRROR_Y_KEY] = texture_mirror_y
    texture_interpolation = getattr(cfg, "PROJECTOR_TEXTURE_INTERPOLATION", "Linear")

    # Scene construction immediately applies the authoritative App matrix.
    # This temporary add-on pose therefore carries no structural interpretation.
    dlp.rotation_euler = (0.0, math.pi, 0.0)
    dlp.proj_settings["throw_ratio"] = cfg.PROJECTOR_THROW_RATIO
    dlp.proj_settings["h_shift"] = cfg.PROJECTOR_LENS_SHIFT_X * 100.0
    dlp.proj_settings["v_shift"] = cfg.PROJECTOR_LENS_SHIFT_Y * 100.0
    projectors_projector.refresh_projector_geometry(dlp)
    _set_projector_energy(dlp, cfg.PROJECTOR_ENERGY)

    img_path = _resolve_repo_path(cfg.PROJECTOR_IMAGE)
    psf_settings = getattr(cfg, "PROJECTOR_PSF", None)
    img = _load_projector_image(img_path, immutable=psf_settings is not None)
    expected_res_x = int(cfg.PROJECTOR_RES_X)
    expected_res_y = int(cfg.PROJECTOR_RES_Y)
    _validate_projector_image_resolution(img, expected_res_x, expected_res_y)
    dlp[PROJECTOR_EXPECTED_RES_X_KEY] = expected_res_x
    dlp[PROJECTOR_EXPECTED_RES_Y_KEY] = expected_res_y

    # find spot child created by add-on
    spot = _find_projector_spot(dlp)
    if spot is None or not spot.data or not spot.data.node_tree:
        raise RuntimeError("[projector] Spot light or its node tree not found")

    # tag spot
    spot[SIM_TAG_KEY] = TAG_PROJECTOR_SPOT

    _set_custom_texture_image(projectors_projector, dlp, img)
    _set_projector_texture_mirror(dlp, texture_mirror_x, texture_mirror_y)
    _set_projector_texture_interpolation(dlp, texture_interpolation)
    _set_projector_energy(dlp, cfg.PROJECTOR_ENERGY)
    if psf_settings is not None:
        from projector_psf import apply_projector_psf
        apply_projector_psf(dlp, psf_settings)
    validate_projector_projection_state(dlp, img_path)
    return dlp


def update_projector_image(dlp_obj, image_path):
    """
    Update the image texture of the projector.
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"[Projector] Image file not found: {image_path}")

    from projector_psf import SETTINGS_KEY, apply_projector_psf, retire_source_image
    settings = json.loads(dlp_obj[SETTINGS_KEY]) if dlp_obj.get(SETTINGS_KEY) else None
    current_spot = _find_projector_spot(dlp_obj)
    if current_spot is None or not current_spot.data or not current_spot.data.node_tree:
        raise RuntimeError("[Projector] Spot light not found for image update")
    old_image = current_spot.data.node_tree.nodes["Image Texture"].image
    # Load a fresh immutable source when PSF is active, even if the pathname
    # was reused for different bytes. Other projector workflows retain caching.
    try:
        img = _load_projector_image(image_path, immutable=settings is not None)
    except Exception as e:
        raise RuntimeError(
            f"[Projector] Failed to load image {image_path}: {e}"
        ) from e

    _validate_projector_image_resolution(
        img,
        dlp_obj[PROJECTOR_EXPECTED_RES_X_KEY],
        dlp_obj[PROJECTOR_EXPECTED_RES_Y_KEY],
    )

    spot = _find_projector_spot(dlp_obj)
    if spot is None or not spot.data or not spot.data.node_tree:
        raise RuntimeError("[Projector] Spot light not found for image update")

    projectors_projector = _load_projectors_addon()
    _set_custom_texture_image(projectors_projector, dlp_obj, img)
    _set_projector_texture_mirror(
        dlp_obj,
        bool(dlp_obj.get(PROJECTOR_TEXTURE_MIRROR_X_KEY, True)),
        bool(dlp_obj.get(PROJECTOR_TEXTURE_MIRROR_Y_KEY, True)),
    )
    _set_projector_texture_interpolation(
        dlp_obj,
        dlp_obj.get(PROJECTOR_TEXTURE_INTERPOLATION_KEY, "Linear"),
    )
    if settings is not None:
        apply_projector_psf(dlp_obj, settings)
    validate_projector_projection_state(dlp_obj, image_path)
    retire_source_image(old_image)
    print(
        f"[Projector] Updated image to: {os.path.basename(image_path)} "
        f"({int(img.size[0])}x{int(img.size[1])})"
    )
