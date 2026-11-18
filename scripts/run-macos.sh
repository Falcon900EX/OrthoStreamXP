#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/artifacts/macos-arm64/OrthoStreamXP.Mac"
export ORTHOSTREAMXP_PYTHON="${ORTHOSTREAMXP_PYTHON:-$ROOT/.venv-macos/bin/python}"
[[ -x "$APP" ]] || { echo 'Run: bash scripts/build-macos.sh' >&2; exit 1; }
[[ -x "$ORTHOSTREAMXP_PYTHON" ]] || { echo 'Python venv missing; run the build script.' >&2; exit 1; }
exec "$APP" "$@"
