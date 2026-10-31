# DMD Structured-Light Simulator

A Blender/Cycles environment for single-camera structured-light simulation with
**orthogonal DMD** and **Diamond-DMD (DLP4500)** projectors. Configure a camera,
projector, target and image sequence, then render camera images, float images,
depth and world-space point clouds. No gwCamSDK installation, NAS data or
downloaded target models are needed for the bundled examples.

中文使用说明：[README.zh-CN.md](README.zh-CN.md)。

## Quick start

Tested on Linux with Blender **4.5.3**, Python **3.10** and CPU rendering.
Install Blender separately. Blender's bundled Python already includes NumPy;
the following dependencies are for the external preparation tools.

```bash
git clone https://github.com/dspangpang/dmd-structured-light-sim.git
cd dmd-structured-light-sim
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export BLENDER_BIN=/path/to/blender

python scripts/run_simulation.py --config examples/orthogonal/scene.json --output output/orthogonal
python scripts/run_simulation.py --config examples/diamond/scene.json --output output/diamond
```

The examples generate four phase-shifted sinusoidal images and a planar target.
They use CPU rendering, 320×240 camera images, and 8 samples per pixel.
These settings are for checking the workflow, not an accuracy benchmark.
Output directories must be new: previous inputs and results are never overwritten.

Use `--prepare-only` to create inputs without Blender, or `--validate-only` to
build and check the Blender scene without capturing images. `--blender-bin`
overrides `BLENDER_BIN`, the compatibility variable `BLENDER`, and `PATH`.

| Orthogonal example | Diamond example |
|---|---|
| ![Orthogonal DMD render](docs/images/orthogonal.png) | ![Diamond-DMD render](docs/images/diamond.png) |

These are newly rendered procedural examples, not experimental captures.

## Two projector types

| Type | Logical input | Blender texture | Geometry |
|---|---|---|---|
| `orthogonal` | Configurable width × height | Same raster | Orthogonal square pixels |
| `diamond` | DLP4500: 912 × 1140 | 1481 × 1482 carrier raster | Staggered logical mapping and matched −45° projector roll |

Diamond inputs are **not resized** to the carrier raster. Each logical value is
assigned to its mapped carrier pixel. The generated `dmd_mapping.json` records
the logical, native and carrier coordinate domains and inverse transforms.
See [DMD coordinates](docs/dmd_coordinates.md).

This is a geometric, pinhole, texture-based image-formation model. It does not
model micromirror switching dynamics, diffraction or a measured device PSF.
Both camera and carrier raster use square pixels and centered principal points.
The initial release supports one camera and one projector.

## Configuration and outputs

Copy an example JSON and edit camera/projector position, look-at target, focal
length, pixel pitch, material or render settings. Dimensions are always `[width,
height]`; positions are metres, focal lengths millimetres and pixel pitches
micrometres. An explicit ordered image list can replace generated patterns.
Custom OBJ/PLY targets are optional. See [configuration](docs/configuration.md).

Each run contains:

- `inputs/`: configuration snapshot, ordered logical/projector images, mapping,
  scene and render request, and input hashes.
- `render/*/`: camera PNG/BMP images, requested EXR/depth/PLY files,
  `gwcam-calib.json`, human-readable `params.txt`, and `render_manifest.json`.
- `run.json`: `COMPLETE` after a real render, or `VALIDATED` for scene-only checks.

Calibration matrices use OpenCV pixel centers and metre translations. The
projector K/P matrices describe the **rendered carrier raster**; use the supplied
mapping when consuming Diamond native coordinates. See [pixel coordinates](docs/pixel_coordinates.md).

## Validation

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python scripts/validate_examples.py --output output/validation
"$BLENDER_BIN" -b --factory-startup -t 2 --python-exit-code 1 --python tests/blender_pixel_coordinates.py
```

The example validator checks four distinct, nonblank, nonsaturated frames per
type, the declared artifacts, and planar world-space point clouds. Unit tests
also check the complete DLP4500 pixel permutation and coordinate inverse.
GitHub Actions runs the Python tests and both real CPU render examples.
See the [release validation record](docs/validation.md) and
`requirements-tested.txt` for the exact environment used locally.

## Advanced use

The low-level scene/render request interfaces, calibration-board renderers,
GUI launcher and custom-asset batch tools remain available. Board captures
require an explicit board plan; the simulator does not choose board poses.
See [advanced interfaces](docs/advanced.md). Optional EXR blur/noise processing
uses `requirements-postprocess.txt`. The optional gwCamSDK reconstruction adapter
is in `integrations/gwcamsdk/` and is not needed by the simulator.

## License and provenance

Project code is **GPL-3.0-or-later**: GNU GPL version 3 or, at your option, any
later version. Copyright © 2026 Jia Zhizhou and contributors. See [LICENSE](LICENSE).
Bundled third-party code retains its own copyright and license notices; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [source provenance](SOURCE_PROVENANCE.md).
