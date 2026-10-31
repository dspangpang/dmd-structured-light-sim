# Diamond-DMD example (DLP4500)

From the repository root:

```bash
python scripts/run_simulation.py --config examples/diamond/scene.json --output output/diamond
```

Four 912×1140 logical sinusoidal patterns are mapped to the 1481×1482 carrier
raster. The generated projector pose includes the matched −45° roll. Inputs
and the coordinate mapping are retained beside the render; the camera outputs
320×240 images, scalar float EXR, depth and world-space PLY.

This demonstrates the input/geometry/render pipeline, not a comparison of
reconstruction accuracy or different fringe-decoding methods.
