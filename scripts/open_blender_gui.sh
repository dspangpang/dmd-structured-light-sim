#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/blender_env.sh"
BLENDER_EXECUTABLE="$(resolve_blender_bin)"
SCENE_CONFIG="${SCENE_CONFIG:-${DMD_SCENE_CONFIG:-}}"
PROJECTOR_IMAGES="${PROJECTOR_IMAGES:-}"
HAS_RENDER_REQUEST=false
if [[ -n "${SIM_RENDER_REQUEST:-}" ]]; then
  HAS_RENDER_REQUEST=true
fi
for arg in "$@"; do
  if [[ "$arg" == "--render-request" ]]; then
    HAS_RENDER_REQUEST=true
    break
  fi
done

if [[ -z "$SCENE_CONFIG" && "$HAS_RENDER_REQUEST" != true ]]; then
  echo "Set SCENE_CONFIG or pass a --render-request containing scene_config." >&2
  exit 2
fi
if [[ -z "$PROJECTOR_IMAGES" && "$HAS_RENDER_REQUEST" != true ]]; then
  echo "Set PROJECTOR_IMAGES or pass a --render-request containing projector_images." >&2
  exit 2
fi
if [[ -z "${DISPLAY:-}" ]]; then
  echo "Warning: DISPLAY is not set; Blender GUI may not start." >&2
fi

ARGS=(-- --no-render)
if [[ -n "$SCENE_CONFIG" ]]; then
  ARGS+=(--scene-config "$SCENE_CONFIG")
fi
if [[ -n "$PROJECTOR_IMAGES" ]]; then
  ARGS+=(--img "$PROJECTOR_IMAGES")
fi

exec "$BLENDER_EXECUTABLE" -noaudio \
  -P "$ROOT/blender/stereo_with_projector_parented.py" \
  "${ARGS[@]}" \
  "$@"
