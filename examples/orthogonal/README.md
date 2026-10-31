# Orthogonal DMD example

From the repository root:

```bash
python scripts/run_simulation.py --config examples/orthogonal/scene.json --output output/orthogonal
```

The 640×480 logical pattern raster is projected unchanged. Four sinusoidal
phase steps illuminate a procedural plane. The camera records 320×240 images;
the example also requests scalar float EXR, depth and world-space PLY output.
No external models or image files are used. Use a new output directory per run.
