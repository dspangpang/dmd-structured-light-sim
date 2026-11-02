import bpy
import math
import os
from config import SimConfig
from cleanup import delete_all_objects
from projector import add_projector
from pixel_coordinates import camera_lens_shift
from mathutils import Matrix, Vector, Euler
from variant_api import get_variant
from rig_topology import (
    camera_specs,
    projector_name,
    projector_local_rotation_matrix,
)
from rig_geometry import projector_world_position


def _resolve_repo_path(path):
    if not path:
        return None
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if path.startswith("//"):
        return os.path.join(repo_root, path[2:])
    if os.path.isabs(path):
        return path
    return os.path.join(repo_root, path)


def _ensure_uv_map(obj, uv_name="UVMap"):
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name=uv_name)
    if uv_name in mesh.uv_layers:
        mesh.uv_layers.active = mesh.uv_layers[uv_name]
    else:
        mesh.uv_layers.active = mesh.uv_layers[0]
    mesh.uv_layers.active_index = mesh.uv_layers.active_index
    if hasattr(mesh.uv_layers, "active_render"):
        mesh.uv_layers.active_render = mesh.uv_layers.active
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=66.0, island_margin=0.02)
    bpy.ops.uv.average_islands_scale()
    bpy.ops.uv.pack_islands(margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")

    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        raise RuntimeError("[Scene] UV layer missing after unwrap.")
    uvs = [loop.uv for loop in uv_layer.data]
    if not uvs:
        raise RuntimeError("[Scene] UV data empty after unwrap.")
    min_u = min(uv.x for uv in uvs)
    max_u = max(uv.x for uv in uvs)
    min_v = min(uv.y for uv in uvs)
    max_v = max(uv.y for uv in uvs)
    print(f"[Scene] UV bounds: U[{min_u:.4f},{max_u:.4f}] V[{min_v:.4f},{max_v:.4f}]")
    if (max_u - min_u) < 1e-4 or (max_v - min_v) < 1e-4:
        raise RuntimeError("[Scene] UVs collapsed to a line/point after unwrap.")


def _bake_channel_to_image(
    obj,
    nt,
    emission_node,
    image_name,
    image_size,
    image_dir,
    colorspace,
    socket,
):
    image = bpy.data.images.new(image_name, width=image_size, height=image_size, alpha=False)
    image.filepath_raw = os.path.join(image_dir, f"{image_name}.png")
    image.file_format = "PNG"
    image.colorspace_settings.name = colorspace

    image_node = nt.nodes.new("ShaderNodeTexImage")
    image_node.image = image
    image_node.select = True
    nt.nodes.active = image_node

    bake_link = nt.links.new(socket, emission_node.inputs["Color"])
    bpy.ops.object.bake(type="EMIT")
    image.save()

    nt.links.remove(bake_link)
    nt.nodes.remove(image_node)
    return image


def _bake_vertex_pbr_maps(obj, bake_dir, image_size=1024):
    mesh = obj.data
    has_scalar_attrs = all(
        name in mesh.attributes for name in ("albedo", "roughness", "metallic")
    )
    has_color_attrs = bool(mesh.color_attributes)
    if not has_scalar_attrs and not has_color_attrs:
        raise RuntimeError(
            "[Scene] Missing PLY BRDF attributes: require albedo/roughness/metallic."
        )

    os.makedirs(bake_dir, exist_ok=True)

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.bake.use_clear = True
    scene.render.bake.use_selected_to_active = False
    scene.render.bake.margin = 2

    mat = bpy.data.materials.new("VertexPBR_Bake")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    output = nt.nodes.new("ShaderNodeOutputMaterial")
    emission = nt.nodes.new("ShaderNodeEmission")
    combine = nt.nodes.new("ShaderNodeCombineRGB")

    nt.links.new(emission.outputs["Emission"], output.inputs["Surface"])

    obj.data.materials.clear()
    obj.data.materials.append(mat)

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    combine.inputs["R"].default_value = 0.0
    combine.inputs["G"].default_value = 0.0
    combine.inputs["B"].default_value = 0.0

    if has_scalar_attrs:
        attr_albedo = nt.nodes.new("ShaderNodeAttribute")
        attr_roughness = nt.nodes.new("ShaderNodeAttribute")
        attr_metallic = nt.nodes.new("ShaderNodeAttribute")
        attr_albedo.attribute_name = "albedo"
        attr_roughness.attribute_name = "roughness"
        attr_metallic.attribute_name = "metallic"

        link_r = nt.links.new(attr_albedo.outputs["Fac"], combine.inputs["R"])
        link_g = nt.links.new(attr_albedo.outputs["Fac"], combine.inputs["G"])
        link_b = nt.links.new(attr_albedo.outputs["Fac"], combine.inputs["B"])
        albedo = _bake_channel_to_image(
            obj,
            nt,
            emission,
            "baked_albedo",
            image_size,
            bake_dir,
            "sRGB",
            combine.outputs["Image"],
        )
        nt.links.remove(link_r)
        nt.links.remove(link_g)
        nt.links.remove(link_b)

        link_r = nt.links.new(attr_roughness.outputs["Fac"], combine.inputs["R"])
        link_g = nt.links.new(attr_roughness.outputs["Fac"], combine.inputs["G"])
        link_b = nt.links.new(attr_roughness.outputs["Fac"], combine.inputs["B"])
        roughness = _bake_channel_to_image(
            obj,
            nt,
            emission,
            "baked_roughness",
            image_size,
            bake_dir,
            "Non-Color",
            combine.outputs["Image"],
        )
        nt.links.remove(link_r)
        nt.links.remove(link_g)
        nt.links.remove(link_b)

        nt.links.new(attr_metallic.outputs["Fac"], combine.inputs["R"])
        nt.links.new(attr_metallic.outputs["Fac"], combine.inputs["G"])
        nt.links.new(attr_metallic.outputs["Fac"], combine.inputs["B"])
        metallic = _bake_channel_to_image(
            obj,
            nt,
            emission,
            "baked_metallic",
            image_size,
            bake_dir,
            "Non-Color",
            combine.outputs["Image"],
        )
    else:
        color_attr = mesh.color_attributes.active or mesh.color_attributes[0]
        attr = nt.nodes.new("ShaderNodeAttribute")
        attr.attribute_name = color_attr.name
        separate = nt.nodes.new("ShaderNodeSeparateRGB")
        nt.links.new(attr.outputs["Color"], separate.inputs["Image"])

        # Treat vertex color channels as scalar PBR parameters: R=albedo, G=roughness, B=metallic.
        link_r = nt.links.new(separate.outputs["R"], combine.inputs["R"])
        link_g = nt.links.new(separate.outputs["R"], combine.inputs["G"])
        link_b = nt.links.new(separate.outputs["R"], combine.inputs["B"])
        albedo = _bake_channel_to_image(
            obj,
            nt,
            emission,
            "baked_albedo",
            image_size,
            bake_dir,
            "sRGB",
            combine.outputs["Image"],
        )
        nt.links.remove(link_r)
        nt.links.remove(link_g)
        nt.links.remove(link_b)

        link_r = nt.links.new(separate.outputs["G"], combine.inputs["R"])
        link_g = nt.links.new(separate.outputs["G"], combine.inputs["G"])
        link_b = nt.links.new(separate.outputs["G"], combine.inputs["B"])
        roughness = _bake_channel_to_image(
            obj,
            nt,
            emission,
            "baked_roughness",
            image_size,
            bake_dir,
            "Non-Color",
            combine.outputs["Image"],
        )
        nt.links.remove(link_r)
        nt.links.remove(link_g)
        nt.links.remove(link_b)

        nt.links.new(separate.outputs["B"], combine.inputs["R"])
        nt.links.new(separate.outputs["B"], combine.inputs["G"])
        nt.links.new(separate.outputs["B"], combine.inputs["B"])
        metallic = _bake_channel_to_image(
            obj,
            nt,
            emission,
            "baked_metallic",
            image_size,
            bake_dir,
            "Non-Color",
            combine.outputs["Image"],
        )

    return {
        "albedo": albedo,
        "roughness": roughness,
        "metallic": metallic,
    }


def _build_principled_material(baked_images):
    mat = bpy.data.materials.new("Baked_PBR_Mat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    tex_coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")

    nt.links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    if baked_images.get("albedo"):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = baked_images["albedo"]
        nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

    if baked_images.get("roughness"):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = baked_images["roughness"]
        tex.image.colorspace_settings.name = "Non-Color"
        nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Roughness"])

    if baked_images.get("metallic"):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = baked_images["metallic"]
        tex.image.colorspace_settings.name = "Non-Color"
        nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Metallic"])

    return mat


def add_camera(
    cfg: SimConfig,
    name,
    loc,
    parent=None,
    yaw_deg=0.0,
    rotation_matrix=None,
    intrinsic_matrix=None,
    yaw_deg_inward=None,
    is_left=True,
):
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0), rotation=(0, 0, 0))
    cam = bpy.context.object
    cam.name = name
    if parent is not None:
        cam.parent = parent
    cam.location = loc

    cam.data.lens = cfg.f_mm
    cam.data.sensor_width = cfg.sensor_w
    cam.data.sensor_height = cfg.sensor_h
    cam.data.sensor_fit = "HORIZONTAL" if cfg.RES_X >= cfg.RES_Y else "VERTICAL"
    intrinsic = intrinsic_matrix or cfg.CAMERA_INTRINSIC_MATRIX_PX
    if intrinsic is None:
        raise ValueError(f"Camera {name} has no imported intrinsic matrix")
    cam.data.shift_x, cam.data.shift_y = camera_lens_shift(
        float(intrinsic[0][2]), float(intrinsic[1][2]), cfg.RES_X, cfg.RES_Y
    )
    cam.data.clip_start = cfg.CAMERA_NEAR_CLIP
    cam.data.clip_end = 100.0

    if rotation_matrix is not None:
        cam.matrix_basis = (
            Matrix.Translation(Vector(loc)) @ Matrix(rotation_matrix).to_4x4()
        )
    elif yaw_deg_inward is not None:
        yaw_deg = -yaw_deg_inward if is_left else +yaw_deg_inward
        cam.rotation_euler = (math.pi, math.radians(yaw_deg), 0.0)
    else:
        cam.rotation_euler = (math.pi, math.radians(yaw_deg), 0.0)
    return cam


def _coerce_xyz(value, default):
    if value is None:
        value = default
    coords = tuple(float(v) for v in value)
    if len(coords) != 3:
        raise ValueError(f"Expected 3 coordinates, got: {value}")
    return coords


def add_overview_camera(cfg: SimConfig):
    loc = Vector(
        _coerce_xyz(
            getattr(cfg, "OVERVIEW_CAMERA_LOCATION", None),
            (0.65, -0.85, 0.35),
        )
    )
    look_at = Vector(
        _coerce_xyz(
            getattr(cfg, "OVERVIEW_CAMERA_LOOK_AT", None),
            (-0.08, 0.0, -0.14),
        )
    )
    direction = look_at - loc
    if direction.length <= 1.0e-9:
        raise ValueError("[Scene] Overview camera location and look-at point overlap.")

    bpy.ops.object.camera_add(location=tuple(loc), rotation=(0.0, 0.0, 0.0))
    cam = bpy.context.object
    cam.name = getattr(cfg, "OVERVIEW_CAMERA_NAME", "overview_camera")
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = float(getattr(cfg, "OVERVIEW_CAMERA_LENS_MM", 32.0))
    cam.data.sensor_width = float(getattr(cfg, "OVERVIEW_CAMERA_SENSOR_WIDTH", 36.0))
    cam.data.sensor_fit = "HORIZONTAL" if cfg.RES_X >= cfg.RES_Y else "VERTICAL"
    cam.data.clip_start = cfg.CAMERA_NEAR_CLIP
    cam.data.clip_end = 100.0
    return cam

def add_target_plane(
    size_xy=(0.15, 0.15),
    center=(0.0, 0.0, 0.0),
    square_size=0.02,
    color1=(1, 1, 1, 1),
    color2=(0, 0, 0, 1),
):
    sx, sy = size_xy

    bpy.ops.mesh.primitive_plane_add(size=1.0, location=center)
    plane = bpy.context.object
    plane.name = "Target_Plane"
    plane.rotation_euler = (math.pi, 0.0, 0.0)
    plane.dimensions = (sx, sy, 0.0)

    # solid material (no texture)
    mat = bpy.data.materials.new("Solid_Plane_Mat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.inputs["Base Color"].default_value = color1
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    plane.data.materials.clear()
    plane.data.materials.append(mat)
    return plane


def add_target_box(
    size_xyz=(0.15, 0.15, 0.15),
    center=(0.0, 0.0, 0.0),
    square_size=0.02,
    color1=(1, 1, 1, 1),
    color2=(0, 0, 0, 1),
):
    sx, sy, sz = size_xyz

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=center)
    box = bpy.context.object
    box.name = "Target_Box"
    box.rotation_euler = (math.pi, 0.0, 0.0)
    box.dimensions = (sx, sy, sz)

    mat = bpy.data.materials.new("Solid_Box_Mat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.inputs["Base Color"].default_value = color1
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    box.data.materials.clear()
    box.data.materials.append(mat)
    return box


def add_target_cylinder(
    radius=0.1,
    depth=0.1,
    center=(0.0, 0.0, 0.0),
    color=(0.8, 0.8, 0.8, 1.0),
    axis="z",
):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, location=center)
    cyl = bpy.context.object
    cyl.name = "Target_Cylinder"
    axis = (axis or "z").lower()
    if axis == "x":
        cyl.rotation_euler = (0.0, math.pi / 2.0, 0.0)
    elif axis == "y":
        cyl.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    else:
        cyl.rotation_euler = (0.0, 0.0, 0.0)

    mat = bpy.data.materials.new("Solid_Cyl_Mat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.inputs["Base Color"].default_value = color
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    cyl.data.materials.clear()
    cyl.data.materials.append(mat)
    return cyl


def add_custom_target(
    filepath,
    center=(0.0, 0.0, 0.0),
    rotation_deg=(0.0, 0.0, 0.0),
    scale=1.0,
    albedo=0.8,
    roughness=0.5,
    metallic=0.0,
    subsurface=0.0,
    bake_dir=None,
    bake_image_size=1024,
    use_ply_brdf=True,
):
    if not os.path.isfile(filepath):
        print(f"[Scene] Error: Custom target file not found: {filepath}")
        return None
    
    print(f"[Scene] Importing {filepath}...")
    
    # Deselect all first to identify the new object easily
    bpy.ops.object.select_all(action='DESELECT')
    existing_names = {obj.name for obj in bpy.data.objects}
    
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".ply":
        if hasattr(bpy.ops.wm, "ply_import"):
            bpy.ops.wm.ply_import(filepath=filepath)
        else:
            if not hasattr(bpy.ops.import_mesh, "ply"):
                if hasattr(bpy.ops.preferences, "addon_enable"):
                    bpy.ops.preferences.addon_enable(module="io_mesh_ply")
            if hasattr(bpy.ops.import_mesh, "ply"):
                bpy.ops.import_mesh.ply(filepath=filepath)
            else:
                print("[Scene] Error: No PLY importer found.")
                return None
    elif ext == ".obj":
        imported = _import_obj(filepath)
        if not imported:
            return None
        if len(imported) > 1:
            bpy.ops.object.select_all(action="DESELECT")
            for o in imported:
                o.select_set(True)
            bpy.context.view_layer.objects.active = imported[0]
            bpy.ops.object.join()
    else:
        print(f"[Scene] Warning: Unsupported file extension {ext}")
        return None

    # Get the imported object
    new_objects = [obj for obj in bpy.data.objects if obj.name not in existing_names]
    if new_objects:
        obj = new_objects[0]
    else:
        selected_objects = bpy.context.selected_objects
        if not selected_objects:
            print("[Scene] Warning: No object selected after import.")
            return None
        obj = selected_objects[0]
    obj.name = "Target_Custom"
    obj.location = center
    obj.rotation_euler = tuple(math.radians(float(value)) for value in rotation_deg)
    obj.scale = (float(scale),) * 3

    # Setup PBR material
    if bake_dir and use_ply_brdf:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        poly_count = len(obj.data.polygons)
        uv_count = len(obj.data.uv_layers)
        print(f"[Scene] Mesh polygons: {poly_count}, UV layers: {uv_count}")
        if poly_count == 0:
            raise RuntimeError("[Scene] Imported mesh has no faces; cannot bake.")
        _ensure_uv_map(obj)
        if not obj.data.uv_layers:
            raise RuntimeError("[Scene] UV generation failed; no UV layers found.")
        if obj.data.uv_layers.active is None:
            raise RuntimeError("[Scene] UV layer not set as active layer.")
        baked = _bake_vertex_pbr_maps(obj, bake_dir, image_size=bake_image_size)
        if baked:
            mat = _build_principled_material(baked)
            obj.data.materials.clear()
            obj.data.materials.append(mat)
            return obj

    if obj.data.materials:
        mat = obj.data.materials[0]
    else:
        mat = bpy.data.materials.new("Custom_PBR_Mat")
        obj.data.materials.append(mat)

    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")

    bsdf.inputs["Base Color"].default_value = (albedo, albedo, albedo, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic

    if "Subsurface Weight" in bsdf.inputs:
        bsdf.inputs["Subsurface Weight"].default_value = subsurface
    elif "Subsurface" in bsdf.inputs:
        bsdf.inputs["Subsurface"].default_value = subsurface

    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        
    return obj


def _import_obj(filepath):
    if not os.path.isfile(filepath):
        print(f"[Scene] Error: OBJ file not found: {filepath}")
        return []
    bpy.ops.object.select_all(action="DESELECT")
    existing_names = {obj.name for obj in bpy.data.objects}

    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=filepath)
    else:
        if not hasattr(bpy.ops.import_scene, "obj"):
            if hasattr(bpy.ops.preferences, "addon_enable"):
                bpy.ops.preferences.addon_enable(module="io_scene_obj")
        if hasattr(bpy.ops.import_scene, "obj"):
            bpy.ops.import_scene.obj(filepath=filepath)
        else:
            print("[Scene] Error: No OBJ importer found.")
            return []

    new_objects = [obj for obj in bpy.data.objects if obj.name not in existing_names]
    if new_objects:
        return new_objects
    return list(bpy.context.selected_objects or [])


def _load_model_template(model_path, scale, label):
    model_path = _resolve_repo_path(model_path)
    if not model_path:
        print(f"[Scene] {label} model path not set.")
        return []
    if os.path.isdir(model_path):
        obj_files = [f for f in os.listdir(model_path) if f.lower().endswith(".obj")]
        if not obj_files:
            print(f"[Scene] Error: No .obj files found in {model_path}")
            return []
        model_path = os.path.join(model_path, sorted(obj_files)[0])

    print(f"[Scene] Importing {label} model: {model_path}")
    objs = _import_obj(model_path)
    if not objs:
        return []
    if scale != 1.0:
        for obj in objs:
            obj.scale = (scale,) * 3
    return objs


def _add_model_duplicates(
    parent_obj,
    template_objects,
    rot_deg_tuple,
    disable_render=False,
    disable_shadows=False,
):
    if not template_objects:
        return []
    rot = tuple(math.radians(v) for v in rot_deg_tuple)
    created = []
    for src in template_objects:
        dup = src.copy()
        if hasattr(src, "data") and src.data is not None:
            dup.data = src.data
        dup.name = f"{parent_obj.name}_{src.name}"
        bpy.context.scene.collection.objects.link(dup)
        dup.parent = parent_obj
        dup.location = (0.0, 0.0, 0.0)
        # Absolute child rotation in raw mesh coordinates, replacing OBJ's
        # import rotation. Bundled Y-up housings therefore require X90.
        dup.rotation_euler = rot
        if disable_render:
            dup.hide_render = True
        if disable_shadows and hasattr(dup, "visible_shadow"):
            dup.visible_shadow = False
        created.append(dup)
    return created


def build_scene(cfg: SimConfig, bake_dir=None):
    print("[Scene] Cleaning scene")
    delete_all_objects()

    target_center = (
        float(getattr(cfg, "TARGET_CENTER_X", 0.0)),
        float(getattr(cfg, "TARGET_CENTER_Y", 0.0)),
        float(getattr(cfg, "TARGET_CENTER_Z", 0.0)),
    )
    target_scale = float(getattr(cfg, "TARGET_SCALE", 1.0))
    if not math.isfinite(target_scale) or target_scale <= 0.0:
        raise ValueError("TARGET_SCALE must be a finite positive number")

    if cfg.USE_CUSTOM_TARGET and cfg.CUSTOM_TARGET_PATH:
        print(f"[Scene] Adding custom target: {cfg.CUSTOM_TARGET_PATH}")
        target_path = _resolve_repo_path(cfg.CUSTOM_TARGET_PATH)
        target = add_custom_target(
            target_path, 
            center=target_center,
            rotation_deg=(
                getattr(cfg, "CUSTOM_TARGET_ROT_X_DEG", 0.0),
                getattr(cfg, "CUSTOM_TARGET_ROT_Y_DEG", 0.0),
                getattr(cfg, "CUSTOM_TARGET_ROT_Z_DEG", 0.0),
            ),
            scale=target_scale,
            albedo=cfg.MAT_ALBEDO,
            roughness=cfg.MAT_ROUGHNESS,
            metallic=cfg.MAT_METALLIC,
            subsurface=cfg.MAT_SUBSURFACE,
            bake_dir=bake_dir,
            use_ply_brdf=cfg.USE_PLY_BRDF,
        )
        if target is None:
            raise RuntimeError(f"[Scene] Failed to import custom target: {target_path}")
    else:
        print("[Scene] Adding target")
        shape = (cfg.TARGET_SHAPE or "box").lower()
        if shape == "plane":
            target = add_target_plane(
                size_xy=(cfg.target_size_x, cfg.target_size_y),
                center=target_center,
                color1=(cfg.MAT_ALBEDO, cfg.MAT_ALBEDO, cfg.MAT_ALBEDO, 1.0),
            )
        elif shape == "cylinder":
            target = add_target_cylinder(
                radius=cfg.target_cyl_radius,
                depth=cfg.target_cyl_height,
                center=target_center,
                axis=cfg.target_cyl_axis,
            )
        else:
            target = add_target_box(
                size_xyz=(cfg.target_size_x, cfg.target_size_y, cfg.target_size_z),
                center=target_center,
            )
        if target is not None:
            target.scale = (target_scale,) * 3
            rotation = Euler(tuple(math.radians(value) for value in (
                cfg.CUSTOM_TARGET_ROT_X_DEG, cfg.CUSTOM_TARGET_ROT_Y_DEG,
                cfg.CUSTOM_TARGET_ROT_Z_DEG)), 'XYZ')
            target.rotation_euler = (rotation.to_matrix() @ target.rotation_euler.to_matrix()).to_euler()
            for material in target.data.materials:
                if not material or not material.use_nodes:
                    continue
                bsdf = material.node_tree.nodes.get("Principled BSDF")
                if bsdf:
                    bsdf.inputs["Base Color"].default_value = (cfg.MAT_ALBEDO,) * 3 + (1.0,)
                    bsdf.inputs["Roughness"].default_value = cfg.MAT_ROUGHNESS
                    bsdf.inputs["Metallic"].default_value = cfg.MAT_METALLIC
                    subsurface = bsdf.inputs.get("Subsurface Weight") or bsdf.inputs.get("Subsurface")
                    if subsurface is not None:
                        subsurface.default_value = cfg.MAT_SUBSURFACE

    print("[Scene] Adding cameras")
    cameras = []
    camera_model_templates = []
    projector_model_templates = []
    if cfg.USE_CAMERA_MODEL and cfg.CAMERA_MODEL_PATH:
        camera_model_templates = _load_model_template(
            cfg.CAMERA_MODEL_PATH, cfg.CAMERA_MODEL_SCALE, "camera"
        )
    if cfg.USE_PROJECTOR_MODEL and cfg.PROJECTOR_MODEL_PATH:
        projector_model_templates = _load_model_template(
            cfg.PROJECTOR_MODEL_PATH, cfg.PROJECTOR_MODEL_SCALE, "projector"
        )

    projector_origin = projector_world_position(cfg)
    bpy.ops.object.empty_add(type="PLAIN_AXES", location=projector_origin)
    rig_root = bpy.context.object
    rig_root.name = "rig_root"

    for spec in camera_specs(cfg):
        cam = add_camera(
            cfg,
            spec["name"],
            spec["local_position"],
            parent=rig_root,
            rotation_matrix=spec["local_rotation"],
            intrinsic_matrix=spec["intrinsic_matrix_px"],
        )
        cameras.append(cam)
        _add_model_duplicates(
            cam,
            camera_model_templates,
            spec["model_rotation"],
        )

    print("[Scene] Adding projector (Projectors add-on)")
    proj = add_projector(cfg)
    proj.name = projector_name()
    proj.parent = rig_root
    proj.matrix_basis = Matrix(projector_local_rotation_matrix(cfg)).to_4x4()
    bpy.ops.object.empty_add(type="PLAIN_AXES", location=(0.0, 0.0, 0.0))
    projector_model_root = bpy.context.object
    projector_model_root.name = f"{proj.name}_model_root"
    projector_model_root.parent = rig_root
    projector_model_root.matrix_basis = Matrix(
        projector_local_rotation_matrix(cfg)
    ).to_4x4()
    _add_model_duplicates(
        projector_model_root,
        projector_model_templates,
        (
            cfg.PROJECTOR_MODEL_ROT_X_DEG,
            cfg.PROJECTOR_MODEL_ROT_Y_DEG,
            cfg.PROJECTOR_MODEL_ROT_Z_DEG,
        ),
        disable_render=cfg.PROJECTOR_MODEL_DISABLE_RENDER,
        disable_shadows=cfg.PROJECTOR_MODEL_DISABLE_SHADOWS,
    )

    for obj in camera_model_templates:
        bpy.data.objects.remove(obj, do_unlink=True)
    for obj in projector_model_templates:
        bpy.data.objects.remove(obj, do_unlink=True)

    get_variant().extend_scene(cfg)

    if getattr(cfg, "USE_OVERVIEW_CAMERA", False):
        print("[Scene] Adding overview camera")
        overview_cam = add_overview_camera(cfg)
        bpy.context.scene.camera = overview_cam
        if getattr(cfg, "RENDER_OVERVIEW_ONLY", False):
            return [overview_cam]
        cameras.append(overview_cam)

    return cameras
