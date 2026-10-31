"""Shared OpenEXR readers for simulation artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np


DEFAULT_SCALAR_CHANNEL_PREFERENCE = ("V", "Y", "R")


class EXRReadError(RuntimeError):
    """Raised when the OpenEXR Python reader is unavailable or cannot open a file."""


def _openexr_file_api():
    try:
        import OpenEXR
    except ImportError as exc:
        raise EXRReadError(
            "The OpenEXR Python package is not installed in this environment"
        ) from exc

    file_api = getattr(OpenEXR, "File", None)
    if file_api is None:
        raise EXRReadError(
            "The installed OpenEXR package does not provide the modern File API; "
            "install a current official OpenEXR Python binding"
        )
    return file_api


def _as_scalar_pixels(pixels, channel_name: str, path: Path) -> np.ndarray:
    array = np.asarray(pixels, dtype=np.float32)
    if array.ndim == 3 and array.shape[-1] == 1:
        array = array[..., 0]
    if array.ndim != 2:
        raise ValueError(
            f"Expected a two-dimensional OpenEXR channel {channel_name!r}, "
            f"got shape {array.shape}: {path}"
        )
    if array.size == 0:
        raise ValueError(
            f"OpenEXR channel {channel_name!r} is empty: {path}"
        )
    return np.ascontiguousarray(array, dtype=np.float32)


def read_scalar_float_exr(
    path: str | Path,
    *,
    channel: str | None = None,
    channel_preference: Sequence[str] = DEFAULT_SCALAR_CHANNEL_PREFERENCE,
) -> np.ndarray:
    """Read a single-part scalar OpenEXR image as a contiguous float32 array.

    When ``channel`` is omitted, the first available channel from
    ``channel_preference`` is selected.  The default matches the shared Blender
    BW float-output contract: ``V`` first, then ``Y``.  ``R`` is retained as a
    compatibility fallback only when the available RGB channels are identical.

    OpenEXR returns pixels in display-row order.  The shared simulation image
    contract treats that order as top-left image order, so this helper does not
    apply an implicit vertical flip.
    """

    image_path = Path(path)
    selected_channel = str(channel) if channel is not None else None
    preference = tuple(str(name) for name in channel_preference)
    if selected_channel is None and not preference:
        raise ValueError("channel_preference must contain at least one channel")

    file_api = _openexr_file_api()
    try:
        with file_api(str(image_path), separate_channels=True) as infile:
            parts = getattr(infile, "parts", None)
            if parts is not None and len(parts) != 1:
                raise ValueError(
                    "Expected a single-part OpenEXR image, "
                    f"got {len(parts)} parts: {image_path}"
                )

            channels = infile.channels()
            channel_map = {
                str(name): channel_data
                for name, channel_data in channels.items()
            }

            if selected_channel is None:
                selected_channel = next(
                    (
                        candidate
                        for candidate in preference
                        if candidate in channel_map
                    ),
                    None,
                )
            if selected_channel is None or selected_channel not in channel_map:
                available = ", ".join(sorted(channel_map)) or "<none>"
                requested = (
                    (selected_channel,)
                    if channel is not None
                    else preference
                )
                raise ValueError(
                    "OpenEXR has no requested scalar channel "
                    f"{requested}; available channels: {available}: {image_path}"
                )

            if selected_channel == "R" and {"G", "B"}.issubset(channel_map):
                red = _as_scalar_pixels(
                    channel_map["R"].pixels, "R", image_path
                )
                green = _as_scalar_pixels(
                    channel_map["G"].pixels, "G", image_path
                )
                blue = _as_scalar_pixels(
                    channel_map["B"].pixels, "B", image_path
                )
                if not (
                    np.array_equal(red, green)
                    and np.array_equal(red, blue)
                ):
                    raise ValueError(
                        "Refusing to treat non-identical RGB channels as a "
                        f"scalar image: {image_path}"
                    )
                pixels = red
            else:
                pixels = _as_scalar_pixels(
                    channel_map[selected_channel].pixels,
                    selected_channel,
                    image_path,
                )
    except (EXRReadError, ValueError):
        raise
    except Exception as exc:  # noqa: BLE001 - add the input path to decoder errors
        raise EXRReadError(
            f"Cannot decode OpenEXR file {image_path}: {exc}"
        ) from exc

    return pixels
