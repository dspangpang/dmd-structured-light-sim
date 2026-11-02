"""Verify standalone primitive transforms/materials against real Blender objects."""
import math
from pathlib import Path
import sys
import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cli import parse_blender_args
from scene import build_scene
from render import setup_render, validate_blender_camera_intrinsics

cfg = parse_blender_args()
cfg.USE_CUSTOM_TARGET = False
cfg.TARGET_SHAPE = "plane"
cfg.PROJECTOR_IMAGE = str(sorted(Path(cfg.PROJECTOR_IMAGE).glob("proj_*.bmp"))[0])
cfg.MAT_ALBEDO = 0.27
cfg.MAT_ROUGHNESS = 0.43
cfg.MAT_METALLIC = 0.31
cfg.MAT_SUBSURFACE = 0.12
cfg.CUSTOM_TARGET_ROT_Z_DEG = 20.0
cfg.TARGET_SCALE = 0.8
cameras = build_scene(cfg)
assert any(obj.type == "MESH" and obj.parent == cameras[0] for obj in bpy.data.objects), "Camera model missing"
assert any(obj.type == "MESH" and obj.parent and obj.parent.name == "Projector_model_root"
           for obj in bpy.data.objects), "Projector model missing"
bpy.context.view_layer.update()
for parent in (cameras[0], bpy.data.objects['Projector_model_root']):
    housing = next(obj for obj in parent.children if obj.type == 'MESH')
    # Inspect the actual frontmost mesh face, independently of its Euler values.
    front = min(housing.data.polygons, key=lambda face: face.center.y)
    outward = (housing.matrix_world.to_3x3() @ front.normal).normalized()
    optical_axis = (parent.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    assert outward.dot(optical_axis) > .9999, (housing.name, outward, optical_axis)
setup_render(cfg)
validate_blender_camera_intrinsics(cameras, cfg)
target = bpy.data.objects["Target_Plane"]
bsdf = target.data.materials[0].node_tree.nodes.get("Principled BSDF")
np.testing.assert_allclose(bsdf.inputs["Base Color"].default_value[:3], [0.27] * 3, atol=1e-6)
for key, value in (("Roughness", .43), ("Metallic", .31), ("Subsurface Weight", .12)):
    assert abs(bsdf.inputs[key].default_value - value) < 1e-6, key
expected = Matrix.Rotation(math.radians(20), 3, 'Z') @ Matrix.Rotation(math.pi, 3, 'X')
np.testing.assert_allclose(np.array(target.rotation_euler.to_matrix()), np.array(expected), atol=1e-6)
print("STANDALONE_SCENE_OPTIONS_PASSED")
