"""Build an editable Blender scene with four phase steps on its timeline.

Run through scripts/run_simulation.py --interactive, or use Blender -b with
--render-request and --save to generate a .blend file without opening a window.
"""
import argparse
import json
from pathlib import Path
import sys
import traceback

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cli import parse_blender_args
from scene import build_scene
from render import setup_render
from projector import _find_projector_spot


def phase_sequence(projector, files):
    """Pack each core PSF field; native curves select phases without callbacks."""
    from projector import update_projector_image
    from projector_psf import validate_effective_psf
    tree = _find_projector_spot(projector).data.node_tree
    original = tree.nodes["Image Texture"]
    coordinates = original.inputs["Vector"].links[0].from_socket
    previous = None
    states = []
    for i, path in enumerate(files):
        update_projector_image(projector, str(path))
        state = validate_effective_psf(projector)
        states.append(state)
        native = original.image
        native.pack()
        native.filepath = "//" + path.name
        native.use_fake_user = True
        effective = tree.nodes[state["effective_source"]]
        node = tree.nodes.new(effective.bl_idname)
        node.name = f"PSF phase {i * 90} deg"
        node.label = f"Phase {i * 90} deg; sigma={state['sigma_px']:g} source pixels"
        if state["sigma_px"]:
            node.node_tree = effective.node_tree
            for image_node in node.node_tree.nodes:
                if image_node.type == "TEX_IMAGE" and image_node.image and not image_node.image.packed_file:
                    image_node.image.pack()
        else:
            node.image = native
            node.interpolation = "Closest"
            node.extension = original.extension
        tree.links.new(coordinates, node.inputs["Vector"])
        if previous is None:
            previous = node.outputs["Color"]
            continue
        mix = tree.nodes.new("ShaderNodeMixRGB")
        mix.name = f"Select phase {i * 90} deg"
        tree.links.new(previous, mix.inputs[1])
        tree.links.new(node.outputs["Color"], mix.inputs[2])
        for frame in range(1, 5):
            mix.inputs[0].default_value = float(frame == i + 1)
            mix.inputs[0].keyframe_insert("default_value", frame=frame)
        previous = mix.outputs["Color"]
    tree.links.new(previous, tree.nodes["Emission"].inputs["Color"])
    projector["projector_psf_timeline"] = json.dumps(states, sort_keys=True)


def housing_lights(scene):
    """Make device housings visible without lighting the measured target."""
    receivers = bpy.data.collections.new("Device housings")
    scene.collection.children.link(receivers)
    for obj in list(scene.objects):
        if obj.type == "MESH" and obj.parent:
            receivers.objects.link(obj)
            obj.visible_shadow = False
    for name, position, energy in (("Housing key", (.4, -.5, .5), 12),
                                    ("Housing fill", (-.4, .3, .7), 8)):
        data = bpy.data.lights.new(name, "AREA")
        data.energy, data.shape, data.size = energy, "DISK", .5
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        obj.location = position
        obj.rotation_euler = (Vector((0, 0, .65)) - obj.location).to_track_quat("-Z", "Y").to_euler()
        obj.light_linking.receiver_collection = receivers


def arrange_workspace(camera):
    screen = bpy.context.window.screen
    if not bpy.app.background:
        area = next(a for a in screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
            bpy.ops.screen.area_split(direction="VERTICAL", factor=.55)
    views = sorted((a for a in screen.areas if a.type == "VIEW_3D"), key=lambda a: a.x)
    for area in views:
        space = area.spaces.active
        space.shading.type = "RENDERED"
        space.show_region_ui = False
        space.show_region_toolbar = False
        space.overlay.show_extras = False
        space.overlay.show_floor = True
        space.clip_start = .001
        space.clip_end = 10
    left = views[0].spaces.active
    left.region_3d.view_location = Vector((.02, 0, .30))
    direction = Vector((.65, .85, .80)) - left.region_3d.view_location
    left.region_3d.view_rotation = direction.to_track_quat("Z", "Y")
    left.region_3d.view_distance = .85
    left.region_3d.view_perspective = "ORTHO"
    if len(views) > 1:
        right = views[1].spaces.active
        right.camera = camera
        right.region_3d.view_perspective = "CAMERA"
        right.region_3d.view_camera_zoom = 12
        right.overlay.show_overlays = False
    for area in screen.areas:
        if area.type == "DOPESHEET_EDITOR" and not bpy.app.background:
            region = next(r for r in area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
                bpy.ops.action.view_all()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-request", type=Path, required=True)
    parser.add_argument("--save", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if args.save.exists():
        parser.error("Output exists; choose a new .blend file")
    config = json.loads((args.render_request.parent / "config.json").read_text())
    patterns = config["patterns"]
    if patterns.get("kind") != "sinusoidal" or patterns.get("phase_steps", 4) != 4:
        parser.error("Interactive demo requires a generated four-step sinusoidal sequence")
    sys.argv = [sys.argv[0], "--", "--render-request", str(args.render_request)]
    cfg = parse_blender_args()
    directory = Path(cfg.PROJECTOR_IMAGE)
    files = [directory / f"proj_{i:05d}.bmp" for i in range(4)]
    if not all(path.is_file() for path in files) or len(list(directory.glob("proj_*.bmp"))) != 4:
        raise ValueError("Interactive demo requires a four-frame generated sequence")
    cfg.PROJECTOR_IMAGE = str(files[0])
    cfg.SAVE_DEPTH = cfg.SAVE_POINTCLOUD = cfg.SAVE_RENDER_FLOAT_EXR = False
    cfg.USE_OVERVIEW_CAMERA = False
    print("[UI] Building scene", flush=True)
    cameras = build_scene(cfg)
    print("[UI] Configuring render", flush=True)
    setup_render(cfg)
    scene = bpy.context.scene
    scene.camera = cameras[0]
    scene.use_nodes = False
    scene.render.image_settings.file_format, scene.render.image_settings.color_mode = "PNG", "RGB"
    scene.cycles.preview_samples = 32
    scene.cycles.use_preview_denoising = True
    scene.view_settings.view_transform = "Standard"
    scene.frame_start, scene.frame_end, scene.render.fps = 1, 4, 1
    print("[UI] Preparing timeline", flush=True)
    phase_sequence(bpy.data.objects["Projector"], files)
    for i in range(4):
        scene.timeline_markers.new(f"Phase {i * 90} deg", frame=i + 1)
    print("[UI] Preparing housing lights", flush=True)
    housing_lights(scene)
    scene.frame_set(1)
    bpy.ops.object.select_all(action="DESELECT")
    cameras[0].select_set(True)
    bpy.context.view_layer.objects.active = cameras[0]
    cameras[0].data.display_size = .07
    bpy.data.objects["Projector"].data.display_size = .07
    bpy.context.workspace.name = "DMD phase-shift demo"
    print("[UI] Arranging workspace", flush=True)
    arrange_workspace(cameras[0])
    args.save.parent.mkdir(parents=True, exist_ok=True)
    print("[UI] Saving scene", flush=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.save.resolve()), compress=True)
    print(f"Interactive scene saved: {args.save}")


if __name__ == "__main__":
    if bpy.app.background:
        main()
    else:
        # Window regions must be initialized before arranging the workspace.
        def initialize():
            try:
                main()
            except (Exception, SystemExit):
                traceback.print_exc()
                bpy.ops.wm.quit_blender()
        bpy.app.timers.register(initialize, first_interval=1.0)
