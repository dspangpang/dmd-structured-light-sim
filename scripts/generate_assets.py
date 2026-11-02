"""Rebuild original demo meshes: blender -b --factory-startup --python scripts/generate_assets.py.

Dimensions are in metres. Device optical origins are at zero and face local -Z
before OBJ export. The exported Y-up meshes face -Y: use rotation_deg [90, 0, 0]
for their housing children in the scene configuration.
The device bodies are schematic visualization meshes, not manufacturer CAD.
"""
from pathlib import Path
import bpy

OUT = Path(__file__).resolve().parents[1] / "assets" / "models"
OUT.mkdir(parents=True, exist_ok=True)


def material(name, color, metallic=0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = .4
    return mat


def finish(obj, mat, bevel=0):
    obj.data.materials.append(mat)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = obj.modifiers.new("Rounded edges", "BEVEL")
        mod.width, mod.segments = bevel, 3
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return obj


def box(size, center, mat, bevel=.001):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.dimensions = size
    return finish(obj, mat, bevel)


def cylinder(radius, depth, z, mat):
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=radius, depth=depth, location=(0, 0, z))
    return finish(bpy.context.object, mat, .0003)


def clear():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def export(name, materials=True):
    bpy.ops.object.select_all(action="SELECT")
    bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
    bpy.ops.object.join()
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.wm.obj_export(filepath=str(OUT / f"{name}.obj"), export_selected_objects=True,
                          export_materials=materials, export_uv=False, export_normals=True)


clear()
body = material("Body", (.12, .15, .19), .5)
trim = material("Trim", (.48, .53, .58), .7)
glass = material("Lens", (.015, .045, .07), .2)
accent = material("Accent", (.04, .38, .55), .4)
box((.06, .05, .05), (0, 0, .06), body, .004)
box((.062, .052, .005), (0, 0, .0375), trim)
cylinder(.02, .025, .0225, body)
cylinder(.021, .004, .008, trim)
cylinder(.016, .002, .005, glass)
box((.045, .006, .025), (0, -.027, .06), accent)
export("camera")

clear()
box((.12, .05, .09), (.03, 0, .075), body, .005)
box((.122, .052, .005), (.03, 0, .0325), trim)
cylinder(.022, .02, .02, body)
cylinder(.023, .004, .008, accent)
cylinder(.018, .002, .005, glass)
for i in range(7):
    box((.003, .0015, .04), (.02 + i * .008, .025, .08), glass, .0004)
export("projector")

clear()
bpy.ops.mesh.primitive_uv_sphere_add(segments=192, ring_count=96, radius=.1)
for face in bpy.context.object.data.polygons:
    face.use_smooth = True
export("sphere", materials=False)
print(f"Demo meshes written to {OUT}")
