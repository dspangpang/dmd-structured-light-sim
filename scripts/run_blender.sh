#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/blender_env.sh"
BLENDER_EXECUTABLE="$(resolve_blender_bin)"

exec "$BLENDER_EXECUTABLE" -b -noaudio --python-exit-code 1 \
  -P "$ROOT/blender/stereo_with_projector_parented.py" \
  "$@"
