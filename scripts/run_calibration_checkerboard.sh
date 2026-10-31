#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/blender_env.sh"
BLENDER_EXECUTABLE="$(resolve_blender_bin)"

: "${SCENE_CONFIG:?Set SCENE_CONFIG to the scene contract JSON}"
: "${CALIBRATION_BOARD_PLAN:?Set CALIBRATION_BOARD_PLAN to the external board-plan JSON}"

exec "$BLENDER_EXECUTABLE" -b -noaudio --python-exit-code 1 \
  -P "$ROOT/blender/render_calibration_checkerboard.py" \
  -- \
  --scene-config "$SCENE_CONFIG" \
  --calibration-board-plan "$CALIBRATION_BOARD_PLAN" \
  --out "${OUTPUT_DIR:-output}" \
  "$@"
