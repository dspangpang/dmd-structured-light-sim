"""OpenCV integer-center coordinates at the Blender raster/UV boundary.

Internal K/P and exported parameters use the first pixel center as (0, 0).
Blender NDC and image texture UVs instead address the image boundaries.
The current scene adapter accepts only square pixels and full-frame renders.
"""

from gwcam_calibration_io import PIXEL_COORDINATE_CONTRACT


def ndc_to_opencv(x, y, width, height):
    """Blender bottom-left NDC -> OpenCV top-left pixel centers."""
    return float(x) * width - 0.5, (1.0 - float(y)) * height - 0.5


def camera_lens_shift(cx, cy, width, height):
    """OpenCV principal point -> Blender shift for the explicit sensor fit.

    scene.py selects HORIZONTAL for landscape, VERTICAL for portrait.
    Convert the principal point to edge coordinates before computing shift.
    """
    fit_dimension = float(max(width, height))
    return ((0.5 * width - (cx + 0.5)) / fit_dimension,
            ((cy + 0.5) - 0.5 * height) / fit_dimension)


def projector_lens_shift(cx, cy, width, height):
    """OpenCV principal point -> vendored Projectors h_shift/v_shift ratios.

    The TEXTURE mapping inversely applies its translation and scale, then
    adds (0.5, 0.5). v_shift is measured relative to image height.
    """
    return (0.5 - (cx + 0.5) / width,
            (cy + 0.5) / height - 0.5)
