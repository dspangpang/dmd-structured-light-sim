#!/usr/bin/env bash

# Resolve Blender without embedding a developer-specific installation path.
# BLENDER_BIN is the public configuration variable; BLENDER remains supported
# for compatibility with older local commands.
resolve_blender_bin() {
  local candidate="${BLENDER_BIN:-${BLENDER:-}}"
  local resolved=""

  if [[ -z "$candidate" ]]; then
    candidate="blender"
  fi

  if [[ "$candidate" == */* ]]; then
    if [[ ! -x "$candidate" ]]; then
      echo "Blender executable is not executable: $candidate" >&2
      echo "Set BLENDER_BIN to the Blender executable or add blender to PATH." >&2
      return 127
    fi
    resolved="$candidate"
  else
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    if [[ -z "$resolved" ]]; then
      echo "Blender executable not found: $candidate" >&2
      echo "Set BLENDER_BIN to the Blender executable or add blender to PATH." >&2
      return 127
    fi
  fi

  printf '%s\n' "$resolved"
}
