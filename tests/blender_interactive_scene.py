"""Check a saved demo in a fresh Blender process with script auto-run disabled."""
from pathlib import Path
import json

import bpy
import numpy as np

scene = bpy.context.scene
assert (scene.frame_start, scene.frame_end) == (1, 4)
assert not bpy.data.texts, "The demo must not depend on embedded Python"
packed = [image for image in bpy.data.images if image.packed_file and image.filepath.startswith("//proj_")]
assert len(packed) == 4
assert scene.camera.name == "camera_000"
projector = bpy.data.objects["Projector"]
states = json.loads(projector["projector_psf_timeline"])
assert len(states) == 4
spot = next(obj for obj in projector.children if obj.type == "LIGHT")
for i, state in enumerate(states):
    node = spot.data.node_tree.nodes[f"PSF phase {i*90} deg"]
    assert state["stage"] == "projector_emission_before_surface_reflection"
    assert state["camera_image_filter"] is False
    if state["sigma_px"]:
        assert node.type == "GROUP"
        textures = [n for n in node.node_tree.nodes if n.type == "TEX_IMAGE"]
        assert textures and all(n.image.packed_file for n in textures)
    else:
        assert node.interpolation == "Closest"
scene.render.resolution_percentage = 25
scene.cycles.samples = 8
frames = []
for phase in range(1, 5):
    scene.frame_set(phase)
    bpy.context.view_layer.update()
    for obj in scene.objects:
        if obj.type == "LIGHT" and obj.data.type == "SPOT":
            for node in obj.data.node_tree.nodes:
                if node.name.startswith("Select phase"):
                    index = int(node.name.split()[2]) // 90
                    assert node.inputs[0].default_value == float(phase == index + 1)
    output = Path(bpy.data.filepath).parent / f"interactive_phase_{phase}.png"
    scene.render.filepath = str(output)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(output))
    pixels = np.array(image.pixels[:]).reshape(-1, 4)[:, :3]
    assert np.isfinite(pixels).all() and pixels.std() > .01
    assert all(not np.allclose(pixels, previous) for previous in frames)
    frames.append(pixels)
print("PASS: standalone packed scene and all four native animation frames")
