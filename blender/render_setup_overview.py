"""Render a schematic overview of the bundled 0.1 m sphere setup.

blender -b --factory-startup --python blender/render_setup_overview.py -- \
    --render-request output/orthogonal/inputs/render_request.json --output output/setup.png
"""
import argparse
import math
from pathlib import Path
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cli import parse_blender_args
from scene import build_scene
from render import setup_render


def emission(name, rgb, strength=1):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    source = nodes.new("ShaderNodeEmission")
    source.inputs["Color"].default_value = (*rgb, 1)
    source.inputs["Strength"].default_value = strength
    material.node_tree.links.new(source.outputs[0], nodes.new("ShaderNodeOutputMaterial").inputs[0])
    return material


def line(name, points, material, radius=.001):
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.bevel_depth, data.bevel_resolution = radius, 2
    spline = data.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for vertex, point in zip(spline.points, points):
        vertex.co = (*point, 1)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    obj.visible_shadow = False
    return obj


def arrow(name, start, end, material):
    start, end = Vector(start), Vector(end)
    line(name, [start, end], material)
    direction = (end - start).normalized()
    bpy.ops.mesh.primitive_cone_add(vertices=20, radius1=.006, radius2=0, depth=.022,
                                    location=end - direction * .011)
    head = bpy.context.object
    head.name = name + " arrow"
    head.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    head.data.materials.append(material)
    head.visible_shadow = False


def label(camera, text, x, y, material, size=.027):
    curve = bpy.data.curves.new(text, "FONT")
    curve.body, curve.size, curve.align_x, curve.align_y = text, size, "CENTER", "CENTER"
    obj = bpy.data.objects.new(text, curve)
    bpy.context.collection.objects.link(obj)
    height = camera.data.ortho_scale
    width = height * 512 / 560
    obj.location = camera.location + camera.rotation_euler.to_quaternion() @ Vector(
        ((x - .5) * width, (.5 - y) * height, -2))
    obj.rotation_euler = camera.rotation_euler
    obj.data.materials.append(material)
    obj.visible_shadow = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if args.output.exists():
        parser.error("Output exists; choose a new file")
    sys.argv = [sys.argv[0], "--", "--render-request", str(args.render_request)]
    cfg = parse_blender_args()
    cfg.PROJECTOR_IMAGE = str(Path(cfg.PROJECTOR_IMAGE) / "proj_00000.bmp")
    cfg.SAVE_DEPTH = cfg.SAVE_POINTCLOUD = cfg.SAVE_RENDER_FLOAT_EXR = False
    cfg.USE_OVERVIEW_CAMERA = False
    cfg.USE_GPU, cfg.RENDER_THREADS, cfg.SAMPLES = False, 2, 64
    build_scene(cfg)
    setup_render(cfg)
    scene = bpy.context.scene
    scene.use_nodes = False
    scene.render.resolution_x, scene.render.resolution_y = 512, 560
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format, scene.render.image_settings.color_mode = "PNG", "RGB"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure, scene.view_settings.gamma = 0, 1
    scene.world.use_nodes = True
    world_nodes = scene.world.node_tree.nodes
    world_nodes.clear()
    ambient = world_nodes.new("ShaderNodeBackground")
    ambient.inputs["Color"].default_value = (.35, .4, .5, 1)
    ambient.inputs["Strength"].default_value = .12
    black = world_nodes.new("ShaderNodeBackground")
    black.inputs["Strength"].default_value = 0
    mix = world_nodes.new("ShaderNodeMixShader")
    paths = world_nodes.new("ShaderNodeLightPath")
    links = scene.world.node_tree.links
    links.new(paths.outputs["Is Camera Ray"], mix.inputs[0])
    links.new(ambient.outputs[0], mix.inputs[1])
    links.new(black.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], world_nodes.new("ShaderNodeOutputWorld").inputs[0])

    # These area lights illuminate the housings; ambient fill reveals the sphere silhouette.
    receivers = bpy.data.collections.new("Housing lighting")
    scene.collection.children.link(receivers)
    for obj in list(scene.objects):
        if obj.type == "MESH" and obj.parent:
            receivers.objects.link(obj)
    for name, position, power, size in (("Key", (.4, .5, .4), 8, .5),
                                        ("Fill", (-.4, .2, .7), 5, .4)):
        light = bpy.data.lights.new(name, "AREA")
        light.energy, light.shape, light.size = power, "DISK", size
        obj = bpy.data.objects.new(name, light)
        scene.collection.objects.link(obj)
        obj.location = position
        obj.rotation_euler = (Vector((0, 0, .65)) - obj.location).to_track_quat("-Z", "Y").to_euler()
        obj.light_linking.receiver_collection = receivers

    camera_position = Vector(cfg.APP_CAMERA_CENTER_WORLD_M)
    projector_position = Vector(cfg.APP_PROJECTOR_CENTER_WORLD_M)
    gold = emission("Projection rays", (1, .55, .08))
    blue = emission("Camera view rays", (.12, .62, 1))
    white = emission("Labels", (.8, .85, .9))
    # Illustrative rays connect the optical centres to the visible sphere surface.
    for x, y in ((-.065, -.045), (-.065, .045), (.065, -.045), (.065, .045)):
        point = Vector((x, y, math.sqrt(.1 ** 2 - x*x - y*y)))
        line("Projection envelope", [projector_position, point], gold, .0006)
    arrow("Projected fringes", projector_position, (-.035, .02, .0915), gold)
    for y in (-.055, .055):
        line("Camera field of view", [(.055, y, .0628), camera_position], blue, .0006)
    arrow("Captured light", (.045, 0, .0893), camera_position, blue)

    bpy.ops.object.camera_add(location=(.65, .85, .60))
    overview = bpy.context.object
    overview.name = "Setup overview"
    overview.rotation_euler = (Vector((.02, 0, .30)) - overview.location).to_track_quat("-Z", "Y").to_euler()
    overview.data.type, overview.data.ortho_scale = "ORTHO", .98
    scene.camera = overview
    bpy.context.view_layer.update()
    for text, point, color, dy in (("Projector", projector_position, gold, .10),
                                   ("Camera", camera_position, blue, .10),
                                   ("Sphere target", Vector((0, 0, 0)), white, .16)):
        uv = world_to_camera_view(scene, overview, point)
        label(overview, text, uv.x, 1 - uv.y + dy, color)
    label(overview, "Simulation setup", .5, .045, white, .031)
    label(overview, "Fringe projection", .77, .49, gold, .024)
    label(overview, "Image capture", .21, .53, blue, .024)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(args.output.resolve())
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
