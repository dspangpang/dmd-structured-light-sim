#!/usr/bin/env python3
"""Build the fixed DLP4500 carrier textures consumed by the Blender simulator.

The public input contract is always one logical DLP4500 frame with 912 columns
and 1140 rows.  The larger output is a simulator-only carrier raster; it is not
the Diamond-DMD image size, upload size, or calibration coordinate domain.
Blender consumes the carrier and applies the matching projector roll, while
the mapping manifest preserves the 1824x1140 native ``(U, V)`` coordinate
domain for downstream consumers. This module has no gwCamSDK dependency.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


DMD_COLS = 912
DMD_ROWS = 1140
LOGICAL_DMD_SIZE = (DMD_COLS, DMD_ROWS)
NATIVE_DMD_SIZE = (2 * DMD_COLS, DMD_ROWS)

DEFAULT_BATCH_OUTPUT_DIRNAME = "dlp4500_preprojected_textures"
DEFAULT_BATCH_GLOB = "proj_*.bmp"
DEFAULT_SUFFIX = "_dlp4500_preprojected"
DEFAULT_MAPPING_FILENAME = "carrier_mapping.json"
DEFAULT_ORIENTATION = "plus45"
BACKGROUND_GRAY = 0
CARRIER_MAPPING_SCHEMA = "gwcam_diamond_dmd_rotated45_carrier_v1"
ORIENTATION_TO_PROJECTOR_ROLL_DEG = {
    # SceneGeometry defines image-right/image-down as board -X/-Y before
    # applying the roll.  The carrier orientation therefore pairs with the
    # opposite signed App roll: plus45 + (-45 deg) restores native U to board
    # horizontal and native V to board vertical.
    "plus45": -45.0,
    "minus45": 45.0,
}


@dataclass(frozen=True)
class Rot45Layout:
    width: int
    height: int
    u_min: int
    v_min: int
    padding: int

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


def projector_roll_deg(orientation: str) -> float:
    """Return the Blender projector roll paired with one carrier orientation."""

    try:
        return ORIENTATION_TO_PROJECTOR_ROLL_DEG[orientation]
    except KeyError as exc:
        raise ValueError(f"unsupported orientation: {orientation}") from exc


def clamp_gray(value: int, name: str) -> int:
    if value < 0 or value > 255:
        raise ValueError(f"{name} must be in [0, 255], got {value}")
    return value


def nonnegative_int(value: int, name: str) -> int:
    if value < 0:
        raise ValueError(f"{name} must be nonnegative, got {value}")
    return value


def logical_to_native(row: int, col: int) -> tuple[int, int]:
    """Map one logical DMD pixel to its parity-staggered native coordinate."""

    if not 0 <= row < DMD_ROWS or not 0 <= col < DMD_COLS:
        raise ValueError(
            f"logical pixel is outside {DMD_COLS}x{DMD_ROWS}: row={row}, col={col}"
        )
    return 2 * col + (row & 1), row


def native_to_logical(native_u: int, native_v: int) -> tuple[int, int]:
    """Invert :func:`logical_to_native` for a valid Diamond micromirror center."""

    if not 0 <= native_u < NATIVE_DMD_SIZE[0] or not 0 <= native_v < NATIVE_DMD_SIZE[1]:
        raise ValueError(
            "native coordinate is outside "
            f"{NATIVE_DMD_SIZE[0]}x{NATIVE_DMD_SIZE[1]}: "
            f"U={native_u}, V={native_v}"
        )
    if (native_u - native_v) & 1:
        raise ValueError(
            f"native coordinate is not a Diamond micromirror center: U={native_u}, V={native_v}"
        )
    return native_v, (native_u - (native_v & 1)) // 2


def map_logical_to_texture(
    row: int,
    col: int,
    orientation: str,
) -> tuple[int, int]:
    if orientation == "plus45":
        u = col - row // 2
        v = col + (row + 1) // 2
        return u, v
    if orientation == "minus45":
        u = col + (row + 1) // 2
        v = col - row // 2
        return u, v
    raise ValueError(f"unsupported orientation: {orientation}")


def map_native_to_texture(
    native_u: int,
    native_v: int,
    orientation: str,
) -> tuple[int, int]:
    """Map a native Diamond center to the unshifted carrier lattice."""

    native_to_logical(native_u, native_v)
    if orientation == "plus45":
        return (native_u - native_v) // 2, (native_u + native_v) // 2
    if orientation == "minus45":
        return (native_u + native_v) // 2, (native_u - native_v) // 2
    raise ValueError(f"unsupported orientation: {orientation}")


def compute_layout(
    cols: int,
    rows: int,
    orientation: str,
    padding: int,
) -> Rot45Layout:
    if cols <= 0 or rows <= 0:
        raise ValueError(f"layout dimensions must be positive, got {cols}x{rows}")
    nonnegative_int(padding, "padding")
    projector_roll_deg(orientation)
    samples = []
    for row in range(rows):
        samples.append(map_logical_to_texture(row, 0, orientation))
        samples.append(map_logical_to_texture(row, cols - 1, orientation))

    u_values = [u for u, _v in samples]
    v_values = [v for _u, v in samples]
    u_min = min(u_values)
    u_max = max(u_values)
    v_min = min(v_values)
    v_max = max(v_values)

    return Rot45Layout(
        width=u_max - u_min + 1 + 2 * padding,
        height=v_max - v_min + 1 + 2 * padding,
        u_min=u_min,
        v_min=v_min,
        padding=padding,
    )


def native_to_carrier_homography(
    layout: Rot45Layout,
    orientation: str,
) -> tuple[tuple[float, float, float], ...]:
    """Return the affine native-``(U,V)`` to carrier-pixel transform."""

    projector_roll_deg(orientation)
    offset_x = float(layout.padding - layout.u_min)
    offset_y = float(layout.padding - layout.v_min)
    if orientation == "plus45":
        return (
            (0.5, -0.5, offset_x),
            (0.5, 0.5, offset_y),
            (0.0, 0.0, 1.0),
        )
    return (
        (0.5, 0.5, offset_x),
        (0.5, -0.5, offset_y),
        (0.0, 0.0, 1.0),
    )


def carrier_to_native_homography(
    layout: Rot45Layout,
    orientation: str,
) -> tuple[tuple[float, float, float], ...]:
    """Return the exact inverse of :func:`native_to_carrier_homography`."""

    projector_roll_deg(orientation)
    common_offset = float(layout.u_min + layout.v_min - 2 * layout.padding)
    if orientation == "plus45":
        return (
            (1.0, 1.0, common_offset),
            (-1.0, 1.0, float(-layout.u_min + layout.v_min)),
            (0.0, 0.0, 1.0),
        )
    return (
        (1.0, 1.0, common_offset),
        (1.0, -1.0, float(layout.u_min - layout.v_min)),
        (0.0, 0.0, 1.0),
    )


def build_carrier_mapping_contract(
    orientation: str = DEFAULT_ORIENTATION,
    padding: int = 0,
    background_gray: int = BACKGROUND_GRAY,
) -> dict[str, object]:
    """Describe the three coordinate domains without exposing Blender intrinsics."""

    layout = compute_layout(DMD_COLS, DMD_ROWS, orientation, padding)
    clamp_gray(background_gray, "background gray")
    mapping: dict[str, object] = {
        "schema": CARRIER_MAPPING_SCHEMA,
        "logical_dmd_size_wh": list(LOGICAL_DMD_SIZE),
        "carrier_texture_size_wh": list(layout.size),
        "diamond_native_size_wh": list(NATIVE_DMD_SIZE),
        "orientation": orientation,
        "projector_roll_deg": projector_roll_deg(orientation),
        "padding_px": padding,
        "background_gray": background_gray,
        "layout": {
            "u_min": layout.u_min,
            "v_min": layout.v_min,
        },
        "logical_to_native": {
            "U": "2*col+(row mod 2)",
            "V": "row",
        },
        "native_to_carrier_homography": [
            list(row) for row in native_to_carrier_homography(layout, orientation)
        ],
        "carrier_to_native_homography": [
            list(row) for row in carrier_to_native_homography(layout, orientation)
        ],
    }
    canonical = json.dumps(
        mapping,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    mapping["mapping_sha256"] = hashlib.sha256(canonical).hexdigest()
    return mapping


def write_carrier_mapping_contract(
    output_path: Path,
    orientation: str = DEFAULT_ORIENTATION,
    padding: int = 0,
    background_gray: int = BACKGROUND_GRAY,
) -> Path:
    """Write a reusable carrier/native transform manifest next to render inputs."""

    contract = build_carrier_mapping_contract(
        orientation,
        padding,
        background_gray,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def map_logical_to_carrier_pixel(
    row: int,
    col: int,
    orientation: str = DEFAULT_ORIENTATION,
    padding: int = 0,
) -> tuple[int, int]:
    """Map a logical DMD pixel to its final carrier image pixel."""

    native_u, native_v = logical_to_native(row, col)
    texture_u, texture_v = map_native_to_texture(native_u, native_v, orientation)
    layout = compute_layout(DMD_COLS, DMD_ROWS, orientation, padding)
    return (
        texture_u - layout.u_min + layout.padding,
        texture_v - layout.v_min + layout.padding,
    )


def load_dmd_input_image(input_path: Path) -> Image.Image:
    image = Image.open(input_path).convert("L")
    expected_size = (DMD_COLS, DMD_ROWS)
    if image.size != expected_size:
        raise ValueError(
            f"input image must be {DMD_COLS} columns x {DMD_ROWS} rows; "
            f"got {image.size[0]}x{image.size[1]}: {input_path}"
        )
    return image


def generate_dlp4500_blender_texture(
    source: Image.Image,
    *,
    orientation: str = DEFAULT_ORIENTATION,
    padding: int = 0,
    background_gray: int = BACKGROUND_GRAY,
) -> Image.Image:
    """Generate one Blender carrier texture from a logical 912x1140 image."""

    if source.size != LOGICAL_DMD_SIZE:
        raise ValueError(
            f"source image must be {DMD_COLS} columns x {DMD_ROWS} rows; "
            f"got {source.size[0]}x{source.size[1]}"
        )
    nonnegative_int(padding, "padding")
    clamp_gray(background_gray, "background gray")
    projector_roll_deg(orientation)

    source = source.convert("L")
    pixels = source.load()
    layout = compute_layout(DMD_COLS, DMD_ROWS, orientation, padding)

    output = Image.new("L", layout.size, background_gray)
    out_pixels = output.load()

    for row in range(DMD_ROWS):
        for col in range(DMD_COLS):
            texture_u, texture_v = map_logical_to_texture(row, col, orientation)
            x = texture_u - layout.u_min + layout.padding
            y = texture_v - layout.v_min + layout.padding
            out_pixels[x, y] = pixels[col, row]

    return output


def remap_to_preprojected_texture(
    source: Image.Image,
    orientation: str = DEFAULT_ORIENTATION,
    padding: int = 0,
    background_gray: int = BACKGROUND_GRAY,
) -> Image.Image:
    """Backward-compatible alias for the original Blender-side API."""

    return generate_dlp4500_blender_texture(
        source,
        orientation=orientation,
        padding=padding,
        background_gray=background_gray,
    )
