#!/usr/bin/env python3
"""Map configurable Diamond-DMD arrays to Blender carrier textures.

For W logical columns and H rows, staggered micromirror centres use native
coordinates U=2*col+(row mod 2), V=row in a 2W by H domain. Blender consumes a
45-degree carrier raster and applies the matching projector roll. The mapping
manifest records all sizes and transforms; the carrier is not a hardware upload
format. DLP4500 dimensions remain a default for direct helper calls.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


DEFAULT_LOGICAL_SIZE = (912, 1140)

DEFAULT_BATCH_OUTPUT_DIRNAME = "diamond_preprojected_textures"
DEFAULT_BATCH_GLOB = "proj_*.bmp"
DEFAULT_SUFFIX = "_diamond_preprojected"
DEFAULT_MAPPING_FILENAME = "carrier_mapping.json"
DEFAULT_ORIENTATION = "plus45"
BACKGROUND_GRAY = 0
CARRIER_MAPPING_SCHEMA = "dmd-sim.diamond-carrier.v1"
ORIENTATION_TO_PROJECTOR_ROLL_DEG = {
    # SceneGeometry defines image-right/image-down as board -X/-Y before
    # applying the roll.  The carrier orientation therefore pairs with the
    # opposite signed projector roll: plus45 + (-45 deg) restores native U to board
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


def _logical_size(size) -> tuple[int, int]:
    if not isinstance(size, (tuple, list)) or len(size) != 2 or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in size
    ):
        raise ValueError("logical_size must contain two positive integers (width, height)")
    return tuple(size)


def logical_to_native(
    row: int, col: int, logical_size=DEFAULT_LOGICAL_SIZE,
) -> tuple[int, int]:
    """Map one logical DMD pixel to its parity-staggered native coordinate."""

    cols, rows = _logical_size(logical_size)
    if not 0 <= row < rows or not 0 <= col < cols:
        raise ValueError(
            f"logical pixel is outside {cols}x{rows}: row={row}, col={col}"
        )
    return 2 * col + (row & 1), row


def native_to_logical(
    native_u: int, native_v: int, logical_size=DEFAULT_LOGICAL_SIZE,
) -> tuple[int, int]:
    """Invert :func:`logical_to_native` for a valid Diamond micromirror center."""

    cols, rows = _logical_size(logical_size)
    if not 0 <= native_u < 2 * cols or not 0 <= native_v < rows:
        raise ValueError(
            "native coordinate is outside "
            f"{2 * cols}x{rows}: "
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
    logical_size=DEFAULT_LOGICAL_SIZE,
) -> tuple[int, int]:
    """Map a native Diamond center to the unshifted carrier lattice."""

    native_to_logical(native_u, native_v, logical_size)
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
    cols, rows = _logical_size((cols, rows))
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
    *,
    logical_size=DEFAULT_LOGICAL_SIZE,
) -> dict[str, object]:
    """Describe the three coordinate domains without exposing Blender intrinsics."""

    cols, rows = _logical_size(logical_size)
    layout = compute_layout(cols, rows, orientation, padding)
    clamp_gray(background_gray, "background gray")
    mapping: dict[str, object] = {
        "schema": CARRIER_MAPPING_SCHEMA,
        "logical_dmd_size_wh": [cols, rows],
        "carrier_texture_size_wh": list(layout.size),
        "diamond_native_size_wh": [2 * cols, rows],
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
    *,
    logical_size=DEFAULT_LOGICAL_SIZE,
) -> Path:
    """Write a reusable carrier/native transform manifest next to render inputs."""

    contract = build_carrier_mapping_contract(
        orientation,
        padding,
        background_gray,
        logical_size=logical_size,
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
    *,
    logical_size=DEFAULT_LOGICAL_SIZE,
) -> tuple[int, int]:
    """Map a logical DMD pixel to its final carrier image pixel."""

    cols, rows = _logical_size(logical_size)
    native_u, native_v = logical_to_native(row, col, logical_size)
    texture_u, texture_v = map_native_to_texture(native_u, native_v, orientation, logical_size)
    layout = compute_layout(cols, rows, orientation, padding)
    return (
        texture_u - layout.u_min + layout.padding,
        texture_v - layout.v_min + layout.padding,
    )


def load_dmd_input_image(input_path: Path, logical_size=None) -> Image.Image:
    with Image.open(input_path) as source:
        image = source.convert("L")
    if logical_size is not None and image.size != _logical_size(logical_size):
        raise ValueError(
            f"input image must have logical size {tuple(logical_size)}; "
            f"got {image.size[0]}x{image.size[1]}: {input_path}"
        )
    return image


def generate_diamond_blender_texture(
    source: Image.Image,
    *,
    orientation: str = DEFAULT_ORIENTATION,
    padding: int = 0,
    background_gray: int = BACKGROUND_GRAY,
) -> Image.Image:
    """Generate a carrier from the source image's logical width and height."""

    cols, rows = _logical_size(source.size)
    nonnegative_int(padding, "padding")
    clamp_gray(background_gray, "background gray")
    projector_roll_deg(orientation)

    source = source.convert("L")
    pixels = source.load()
    layout = compute_layout(cols, rows, orientation, padding)

    output = Image.new("L", layout.size, background_gray)
    out_pixels = output.load()

    for row in range(rows):
        for col in range(cols):
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

    return generate_diamond_blender_texture(
        source,
        orientation=orientation,
        padding=padding,
        background_gray=background_gray,
    )
