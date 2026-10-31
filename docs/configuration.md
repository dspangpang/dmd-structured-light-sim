# Standalone configuration

The public input schema is `dmd-sim.scene.v1`. Start with either bundled
`examples/*/scene.json`. Unknown fields are rejected. The generated renderer
contract retains the `gwcam_structured_light_scene` name for compatibility;
it does not require gwCamSDK or StructuredLightViewer.

## Devices and units

Both `camera` and `projector` contain:

| Field | Meaning |
|---|---|
| `resolution` | `[width, height]`; projector dimensions are the **logical input** |
| `pixel_pitch_um` | Square pixel pitch in micrometres; for Diamond this is the carrier/mirror-edge lattice pitch |
| `focal_length_mm` | Pinhole focal length in millimetres |
| `position_m` | Device optical center in world metres |
| `look_at_m` | World point on the optical axis |
| `up` | Optional world upward direction, default `[0, 1, 0]`; cannot be parallel to the optical axis |

`projector.type` must be `orthogonal` or `diamond`. Diamond uses the fixed
DLP4500 `[912, 1140]` logical size in this release. The generator builds the
carrier raster, selects its matched −45° roll, and derives centered K matrices
with `fx = fy = focal_length_mm / (pixel_pitch_um / 1000)`.

The optical basis has X to image right, Y down and Z forward. It is derived
from the look-at direction and up vector. Device-to-world bases and projection
matrices are generated together, then checked again by the existing renderer.
No footprint/common-view analysis is performed by this minimal generator;
the emitted preview metadata explicitly says `not_computed`. This does not
disable camera/projector matrix checks.

## Patterns

Generated patterns are 8-bit grayscale sinusoids:

```json
"patterns": {"kind": "sinusoidal", "period_px": 64.0, "phase_steps": 4, "axis": "x"}
```

Coordinates and period are in **logical input pixels**, not Diamond native
coordinates. The deterministic intensity is rounded from
`127.5 + 100*cos(2*pi*(coordinate/period_px + step/phase_steps))`.

To supply your own patterns:

```json
"patterns": {"kind": "files", "files": ["patterns/frame_b.bmp", "patterns/frame_a.bmp"]}
```

Paths are relative to the configuration file, or absolute. The list is ordered;
directory sorting does not define the sequence. Inputs must be grayscale and
match the logical size. They are copied to consecutively numbered logical files,
then converted to projector textures. RGB and mismatched sizes are rejected.
The preparation manifest records each source hash and both generated hashes.

## Targets

Use a `primitive` plane, box or cylinder, or an `asset` (mutually exclusive).

```json
"target": {
  "primitive": {"shape": "plane", "size_m": [0.5, 0.38, 0.001]},
  "center_world_m": [0, 0, 0],
  "material": {"albedo": 0.8, "roughness": 1.0, "metallic": 0.0, "use_ply_brdf": false}
}
```

Plane `size_m` uses its first two dimensions; the third must be positive for
schema consistency but does not create thickness. Box uses all three.
Cylinder uses `radius_m`, `height_m`, and `axis` (`x`, `y`, or `z`). Primitive and
custom targets support the material override block. Primitive `rotation_deg`
is an additional world-axis XYZ Euler rotation of the base primitive.
For model import, replace `primitive` with
`"asset": "models/target.obj"` (or a supported PLY) and optionally set `scale`
and `rotation_deg`. Custom assets and any companion material files remain
user-provided inputs. Their paths must remain accessible for rendering.

## Rendering and capture

`render` accepts the existing renderer options. The examples show CPU rendering
with `samples`, `threads`, `denoising`, `projector_energy`,
`projector_texture_interpolation`, `bounce_level`, and `save_float_exr`.
Change camera dimensions in `camera.resolution`; the public generator rejects
a conflicting `render.resolution` override.

`Closest` interpolation retains hard input pixel support; `Linear` interpolates
between texture samples. This is a numerical sampling choice, not a calibrated
optical blur model. `use_gpu: true` requests automatic GPU selection with CPU
fallback. Optional `cycles_device` and `cycles_device_name` restrict discovery.

`capture` supports `projector_image_format` (`PNG`/`BMP`), `save_depth`, and
`save_pointcloud`. Point clouds are derived from camera depth using the exported
geometry; they are ground-truth geometry outputs, not phase-based reconstruction.
The exported floating image follows the existing compositor's scalar intensity
pipeline and may be clipped at high illumination; reduce energy if needed.

## Output lifecycle

The public runner creates a new directory, prepares all inputs, starts Blender,
and verifies its manifest before writing `run.json`. Preparation alone does not
create a successful render receipt. A failed or interrupted run is retained for
inspection; choose a new output directory for the next attempt.

`inputs/render_request.json` can also be passed directly to the lower-level
renderer. Its scene/image paths are relative to that request; output paths and
runtime provenance refer to the actual execution location. Generated runs are
not installation templates: relocate/recreate inputs from the source config
when moving to a different machine.
