"""Human-readable snapshot of actual Blender objects, not OpenCV matrices."""
import json
from pathlib import Path


def save_blender_parameters(cfg, output_dir):
    import bpy
    scene = bpy.context.scene
    bpy.context.view_layer.update()
    records = {}
    for obj in scene.objects:
        if obj.type not in {"CAMERA", "LIGHT"} and "projector_opencv_intrinsic" not in obj:
            continue
        entry = {"type": obj.type, "parent": obj.parent.name if obj.parent else None,
                 "matrix_world": [list(row) for row in obj.matrix_world],
                 "matrix_local": [list(row) for row in obj.matrix_local]}
        if obj.type == "CAMERA":
            entry["camera"] = {key: getattr(obj.data, key) for key in
                               ("type", "lens", "sensor_width", "sensor_height", "sensor_fit",
                                "shift_x", "shift_y", "clip_start", "clip_end")}
        if obj.type == "LIGHT":
            entry["light"] = {"type": obj.data.type, "energy": obj.data.energy,
                              "color": list(obj.data.color)}
            if obj.data.type == "SPOT":
                entry["light"].update(spot_size=obj.data.spot_size, spot_blend=obj.data.spot_blend)
        if obj.type == "LIGHT" and obj.data.use_nodes:
            def nodes(tree):
                result = {}
                for node in tree.nodes:
                    if node.type == "GROUP" and node.node_tree:
                        result[node.name] = nodes(node.node_tree)
                    elif node.type == "MAPPING":
                        result[node.name] = {"vector_type": node.vector_type, **{
                            name: list(node.inputs[name].default_value)
                            for name in ("Location", "Rotation", "Scale")}}
                    elif node.type == "TEX_IMAGE" and node.image:
                        result[node.name] = {"image": node.image.filepath,
                                            "size": list(node.image.size),
                                            "interpolation": node.interpolation}
                return result
            entry["nodes"] = nodes(obj.data.node_tree)
        records[obj.name] = entry
    from variant_api import get_variant
    payload = {"variant_metadata": get_variant().parameter_metadata(cfg),"schema": "core-sim.blender-native-parameters.v1",
               "blender_version": bpy.app.version_string,
               "scene_config_sha256": cfg.APP_SCENE_CONFIG_SHA256,
               "object_axes": "Blender camera local -Z forward, +Y up",
               "world_length_unit": "meter", "lens_sensor_unit": "millimeter",
               "render": {key: getattr(scene.render, key) for key in
                          ("resolution_x", "resolution_y", "resolution_percentage",
                           "pixel_aspect_x", "pixel_aspect_y", "use_border", "use_crop_to_border")},
               "objects": records}
    Path(output_dir, "params.txt").write_text(
        "Blender native parameters (OpenCV calibration is in calibration.json)\n" +
        json.dumps(payload, indent=2) + "\n", encoding="utf-8")
