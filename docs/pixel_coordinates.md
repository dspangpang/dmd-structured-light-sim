# Pixel coordinates at the Blender boundary

All imported and exported K/P matrices use OpenCV coordinates: (0,0) is the
center of the first pixel, u points right and v down. Fractional points remain
valid. Image support is [-0.5,W-0.5] x [-0.5,H-0.5]. Blender NDC/texture UV
coordinates address image edges: u=W*s-0.5, v=H*(1-t)-0.5.

`src/pixel_coordinates.py` owns this conversion and the square-pixel lens-shift
adapters. The renderer retains the imported K, converts only at the Blender
boundary, and validates the effective projection in OpenCV coordinates.
Centered principal points require zero camera and projector lens shift.
Do not translate images, depth arrays, camera ray indices or exported K again.
The vertical image-buffer flip is independent of the half-pixel conversion.

`gwcam-calib.json` is the only machine-readable calibration artifact. It uses
schema `core-sim.gwcam-calib.v1`, OpenCV integer centers and metre translations.
It records every device by name, world-to-device transforms, and relative
transforms `X_target = R X_source + t`. The top-level camera/projector fields
match gwCamSDK (including its existing `dlp_tanslation_matrix` field spelling).
They refer to the explicitly named `reference_camera`. All matrices retain full
JSON floating-point precision. Projector coordinates address the rendered raster;
Diamond domain conversions belong to the downstream application.

`params.txt` is now a human-readable Blender native state snapshot: object
world/local matrices, camera lens/sensor/shift, render dimensions and projector
texture nodes. It is written only after a scene exists. Metadata-only runs export
validated input calibration JSON but do not claim to snapshot native objects.
Neither artifact is parsed from the other. The old text matrix format and parser
are removed; old data must be regenerated. Both outputs identify their scene hash.

Use `save_gwcam_calibration(cfg, output_dir, projector_resolution)` from
`src/gwcam_calibration.py`; it does not require bpy. Derived viewpoints supply
explicit transformed device parameters via `parameters=`.

Run unit tests with `python -m unittest discover -s tests`. Verify actual Blender
projection and Cycles pixels with `blender -b --factory-startup -t 2 --python
tests/blender_pixel_coordinates.py` (use an absolute script path when a launcher
changes the working directory). This test writes only to a temporary folder.

## Reading calibration without Blender

`src/gwcam_calibration_io.py` owns the schema constants, matrix validation and
`read_gwcam_calibration(path)`. It uses only Python's standard library and never
imports bpy, NumPy, configuration or variant modules. The returned dictionary has
`devices`, `reference_camera`, `camera_projectors`, per-device image sizes, and
`raw` for additional serialized fields. World-to-device matrices may have either
orthonormal basis handedness; relative poses are checked against the devices.
Multi-projector artifacts must explicitly map each camera to its projector.

`load_reconstruction_calibrations(path)` in `gwcam_calibration.py` delegates all
reading and validation to this module, then constructs the NumPy matrices needed
by reconstruction. It requires camera/projector pairs; the basic reader also
supports camera-only stereo data. No parser for old text output is provided.

External pipelines load this file from their selected simulator checkout by
absolute module path (not a global `sys.path` insertion). This allows separate
simulator versions in one Python process. Selecting a simulator without this API
is an error; readers never silently fall back to a different checkout.
