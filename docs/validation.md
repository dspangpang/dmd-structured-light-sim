# Release preparation validation — 2026-10-31

Validation used a new Python 3.10 virtual environment installed from the
published dependency files, and Blender 4.5.3 LTS on Linux with CPU rendering.
Exact installed versions are in `requirements-tested.txt`.

- Python suite: 57 tests, 56 passed, 1 skipped because no implicit branch-default
  request is selected. Both public examples are tested explicitly.
- Both DMD examples: four distinct 320×240 camera frames, without clipping;
  float EXR, depth, world-space point clouds and complete manifests verified.
- Each planar example produced 76,800 points. The 99th-percentile absolute
  world Z residual was approximately 1.21e-7 m against the analytic plane.
- Actual Blender camera and projector pixel checks passed, including portrait
  projection. The largest rendered pixel check residual was about 0.00408 px.
- Primitive material/rotation parameters were checked on real Blender objects.
- The Diamond `--validate-only` route and repaired checkerboard scene-validation
  branch passed; those checks do not constitute full calibration datasets.
- DLP4500 mappings preserve all 1,039,680 input values with unique output pixels;
  the inverse mappings and paired roll are covered by unit tests.

These are synthetic functionality and geometry checks, not phase-reconstruction
accuracy, a comparison between DMD hardware, or a new experimental result.
Machine-readable results and preview hashes are in [validation.json](validation.json).

The two README previews are newly rendered procedural examples. Historical
research outputs and private datasets were not copied or regenerated.
