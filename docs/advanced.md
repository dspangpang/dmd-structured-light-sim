# Advanced interfaces

The standalone runner is the recommended entry point. The inherited renderer
also accepts an explicit scene contract and a render request:

```bash
scripts/run_blender.sh -- --render-request /path/to/inputs/render_request.json
scripts/open_blender_gui.sh --render-request /path/to/inputs/render_request.json
```

Create a separate output directory for a GUI validation request; a validated
scene and a completed render are different execution modes. GUI use requires
a display and is not part of the headless CPU example test.

## Calibration boards

`blender/render_calibration_checkerboard.py` and
`blender/render_calibration_circle_grid.py` consume externally supplied board
plans. The plan describes board dimensions, explicit poses, the scene contract
and the associated view metadata. They use `dmd-sim.calibration-board-plan.v1` (or `dmd-sim.circle-grid-plan.v1`) and
do not generate poses. A planned-board dataset is optional, not an installation
dependency. Shell wrappers accept `SCENE_CONFIG`, `CALIBRATION_BOARD_PLAN`,
and, for circle grids, `PROJECTOR_IMAGES`.

## Custom-asset batches

`scripts/batch_render.py --help` describes batch rendering with your own models,
fringe directories and low-level scene contracts. It is a compatibility helper;
the bundled standalone examples use `scripts/run_simulation.py`.

## Float EXR postprocessing

```bash
python scripts/blur_float_exr_gaussian.py --help
```

This optional tool applies a Gaussian image-space PSF, optional sensor noise and
quantization to scalar EXR data. Pass explicit input/output paths and set a noise
seed for reproducibility. Its historic no-argument paths are not demo datasets.
The external environment needs OpenCV, NumPy and the modern `OpenEXR.File` API;
the Blender environment does not need these extra packages for ordinary renders.
