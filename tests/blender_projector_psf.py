"""Real Cycles conformance for the continuous 2-D projector PSF.

Run Blender --background --factory-startup --python this_file -- --output DIR.
Uses a tiny projected noninvariant field and reports numerical and switching QA.
"""
import argparse
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from blender_pixel_coordinates import make_camera, setup_cycles, plane, render_image
from projector import add_projector, update_projector_image, validate_projector_projection_state
from projector_psf import (apply_projector_psf, configure_recording, STATE_KEY,
                           validate_effective_psf, OWNED, NODE_NAME)
from pixel_coordinates import projector_lens_shift
from test_projector_psf import oracle


def image_file(root, name, pixels):
    h, w = pixels.shape
    image = bpy.data.images.new(name, width=w, height=h, alpha=False, float_buffer=True, is_data=True)
    image.colorspace_settings.name = "Non-Color"
    rgba = np.ones((h, w, 4), np.float32); rgba[..., :3] = pixels[..., None]
    image.pixels.foreach_set(rgba.ravel())
    image.filepath_raw = str(root / (name + ".exr")); image.file_format = "OPEN_EXR"; image.save()
    path = image.filepath_raw; bpy.data.images.remove(image)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-persistent", action="store_true")
    parser.add_argument("--roll-deg", type=float, default=-45.)
    parser.add_argument("--source-image", type=Path)
    parser.add_argument("--profile-axis", choices=("x", "y"))
    parser.add_argument("--boundary", choices=("constant", "repeat"), default="constant")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    width, height = 9, 7
    if args.source_image:
        source_image = bpy.data.images.load(str(args.source_image.resolve()), check_existing=False)
        source_image.colorspace_settings.name = "Non-Color"
        width, height = source_image.size
        source_pixels = np.empty(width*height*4, np.float32)
        source_image.pixels.foreach_get(source_pixels)
        pixels = source_pixels.reshape(height, width, 4)[..., 0].copy()
        bpy.data.images.remove(source_image)
        camera_width = camera_height = 96
    else:
        yy, xx = np.indices((height, width))
        pixels = (.5 + .3 * np.sin(.91 * xx + .63 * yy) + .13 * ((xx + yy) % 2)).astype(np.float32)
        camera_width, camera_height = width*6, height*6
    if args.profile_axis == "x":
        pixels = np.broadcast_to(pixels[0:1], pixels.shape).copy()
    elif args.profile_axis == "y":
        pixels = np.broadcast_to(pixels[:, 0:1], pixels.shape).copy()
    camera, cfg = make_camera(camera_width, camera_height)
    camera_fx = float(width * 6 if args.source_image else camera_width)
    camera.data.lens *= camera_fx / camera_width
    setup_cycles()
    scene = bpy.context.scene
    scene.cycles.samples = 128
    scene.cycles.filter_width = .01
    scene.render.use_persistent_data = not args.no_persistent
    material = plane(); bsdf = material.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (1, 1, 1, 1)
    bsdf.inputs["Roughness"].default_value = 1
    bsdf.inputs["Specular IOR Level"].default_value = 0
    pattern = image_file(root, "native_pattern", pixels)
    white = image_file(root, "native_white", np.ones_like(pixels))
    k = [[float(width), 0, (width-1)/2], [0, float(width), (height-1)/2], [0, 0, 1]]
    sx, sy = projector_lens_shift(k[0][2], k[1][2], width, height)
    pcfg = SimpleNamespace(PROJECTOR_THROW_RATIO=1., PROJECTOR_LENS_SHIFT_X=sx,
        PROJECTOR_LENS_SHIFT_Y=sy, PROJECTOR_ENERGY=100., PROJECTOR_IMAGE=white,
        PROJECTOR_RES_X=width, PROJECTOR_RES_Y=height, PROJECTOR_INTRINSIC_MATRIX_PX=k,
        PROJECTOR_TEXTURE_INTERPOLATION="Closest",
        PROJECTOR_PSF=dict(sigma_px=0, boundary="constant"))
    projector = add_projector(pcfg)
    projector.matrix_world = Matrix.Rotation(math.radians(args.roll_deg), 4, 'Z')
    bpy.context.view_layer.update()
    configure_recording(projector, root / "effective_updates.jsonl")
    reference = render_image(root / "unblurred_white.exr")[..., 0]
    camera_y, camera_x = np.indices(reference.shape)
    world_x = (camera_x-(camera_width-1)/2)/camera_fx
    world_y = -(camera_y-(camera_height-1)/2)/camera_fx
    theta = math.radians(args.roll_deg)
    native_x = width*(math.cos(theta)*world_x+math.sin(theta)*world_y)+(width-1)/2
    native_y = width*(-math.sin(theta)*world_x+math.cos(theta)*world_y)+(height-1)/2
    mask = ((native_x > .1) & (native_x < width-1.1) &
            (native_y > .1) & (native_y < height-1.1) & (reference > 1e-8))
    # The sigma=0 oracle is pointwise Closest. Rays whose finite camera sample
    # footprint crosses a source-cell discontinuity instead measure an area
    # average, so exclude them from this pointwise conformance population.
    margin = .15
    mask &= (np.abs((native_x+.5) % 1 - .5) < .5-margin)
    mask &= (np.abs((native_y+.5) % 1 - .5) < .5-margin)
    # The full-size smoke test checks a fixed spread of rays, without making
    # a multi-million-term Python oracle the dominant runtime.
    if args.source_image:
        sparse = np.zeros(mask.shape, bool); sparse[::5, ::5] = True; mask &= sparse
    assert mask.sum() > 20
    results = {}
    rendered = {}
    for sigma in (0, .5, 1, 0, 1):
        index = len(rendered)
        apply_projector_psf(projector, dict(sigma_px=sigma, boundary=args.boundary))
        update_projector_image(projector, pattern)
        state = validate_projector_projection_state(projector, pattern)
        actual = render_image(root / f"pattern_{index}_s{sigma:g}.exr")[..., 0] / np.maximum(reference, 1e-20)
        if sigma:
            expected = oracle(pixels, native_x[mask], native_y[mask], sigma, repeat=args.boundary=="repeat")
        else:
            expected = pixels[np.floor(native_y[mask]+.5).astype(int), np.floor(native_x[mask]+.5).astype(int)]
        error = np.abs(actual[mask] - expected)
        maximum, rms = float(error.max()), float(np.sqrt(np.mean(error**2)))
        print("PSF_RENDER_ERROR", sigma, maximum, rms, flush=True)
        assert maximum < .012, (sigma, maximum, rms)
        rendered[index] = actual
        results[str(index)] = dict(sigma_px=sigma, maximum_error=maximum, rms_error=rms,
                                  effective_state=state["projector_psf"],
                                  resident_psf_images=sum(bool(i.get(OWNED)) for i in bpy.data.images))
        assert results[str(index)]["resident_psf_images"] <= (2 if sigma else 0)
    assert float(np.max(np.abs(rendered[0][mask]-rendered[3][mask]))) < 1e-6
    assert float(np.max(np.abs(rendered[2][mask]-rendered[4][mask]))) < 1e-6
    # Same path, different file bytes must invalidate the immutable source/table.
    before = json.loads(projector[STATE_KEY])["source_sha256"]
    image_file(root, "native_pattern", 1-pixels)
    update_projector_image(projector, pattern)
    assert json.loads(projector[STATE_KEY])["source_sha256"] != before
    # Internal edits must fail effective-state validation.
    spot = next(child for child in projector.children if child.type == "LIGHT")
    group = spot.data.node_tree.nodes[NODE_NAME].node_tree
    if args.profile_axis and args.boundary == "repeat":
        assert json.loads(projector[STATE_KEY])["backend"] == "periodic_invariant_cdf_profile"
    math_node = next((node for node in group.nodes if node.type == "MATH"), None)
    added_test_node = math_node is None
    if added_test_node:
        math_node = group.nodes.new("ShaderNodeMath")
    old_operation = math_node.operation; math_node.operation = "SINE"
    try:
        validate_effective_psf(projector)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Modified PSF shader was accepted")
    math_node.operation = old_operation
    if added_test_node:
        group.nodes.remove(math_node)
    validate_projector_projection_state(projector, pattern)
    apply_projector_psf(projector, dict(sigma_px=1, boundary="repeat"))
    update_projector_image(projector, pattern)
    repeated = render_image(root / "repeat_boundary.exr")[..., 0] / np.maximum(reference, 1e-20)
    expected_repeat = oracle(1-pixels, native_x[mask], native_y[mask], 1, repeat=True)
    repeat_boundary_error = float(np.max(np.abs(repeated[mask]-expected_repeat)))
    assert repeat_boundary_error < .012, repeat_boundary_error
    phase_results = {}
    if not args.source_image:
        yy, xx = np.indices((height, width))
        for sigma in (0., .5, 1.):
            apply_projector_psf(projector, dict(sigma_px=sigma, boundary=args.boundary))
            observed, predicted = [], []
            for step in range(4):
                values = (.5 + .4*np.cos(.91*xx+.63*yy+step*math.pi/2)).astype(np.float32)
                phase_path = image_file(root, f"phase_s{sigma:g}_{step}", values)
                update_projector_image(projector, phase_path)
                output = render_image(root/f"phase_s{sigma:g}_{step}_render.exr")[..., 0]
                observed.append((output/np.maximum(reference, 1e-20))[mask])
                if sigma:
                    predicted.append(oracle(values, native_x[mask], native_y[mask], sigma, repeat=args.boundary=="repeat"))
                else:
                    predicted.append(values[np.floor(native_y[mask]+.5).astype(int),
                                            np.floor(native_x[mask]+.5).astype(int)])
            actual, expected = np.array(observed), np.array(predicted)
            def demodulate(stack):
                phasor = (stack[0]-stack[2]) + 1j*(stack[3]-stack[1])
                return stack.mean(axis=0), .5*np.abs(phasor), np.angle(phasor)
            dc, modulation, phase = demodulate(actual)
            dc_ref, modulation_ref, phase_ref = demodulate(expected)
            phase_error = np.angle(np.exp(1j*(phase-phase_ref)))
            metrics = dict(mean_intensity=float(dc.mean()), mean_modulation=float(modulation.mean()),
                mean_intensity_max_error=float(np.max(np.abs(dc-dc_ref))),
                modulation_max_error=float(np.max(np.abs(modulation-modulation_ref))),
                phase_rmse_rad=float(np.sqrt(np.mean(phase_error**2))),
                phase_max_error_rad=float(np.max(np.abs(phase_error))))
            assert metrics["mean_intensity_max_error"] < .004, metrics
            assert metrics["modulation_max_error"] < .004, metrics
            assert metrics["phase_max_error_rad"] < .012, metrics
            phase_results[str(sigma)] = metrics
    report = dict(status="PASS", physical_resolution=[width, height],
        camera_resolution=[camera_width, camera_height], device=scene.cycles.device,
        projector_roll_deg=args.roll_deg, checked_rays=int(mask.sum()),
        source_image=str(args.source_image.resolve()) if args.source_image else "synthetic noninvariant 2D field",
        repeat_boundary_max_error=repeat_boundary_error,
        four_step_phase_validation=phase_results,
        persistent_data=scene.render.use_persistent_data, sigma_sequence=[0,.5,1,0,1],
        repeat_zero_max_error=float(np.max(np.abs(rendered[0][mask]-rendered[3][mask]))),
        repeat_one_max_error=float(np.max(np.abs(rendered[2][mask]-rendered[4][mask]))), results=results)
    (root / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PROJECTOR_PSF_CHECK_PASSED " + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
