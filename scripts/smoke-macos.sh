#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/.venv-macos/bin/python" "$ROOT/platform/macos/live_smoke.py" \
  --app "$ROOT/artifacts/macos-arm64/OrthoStreamXP.Mac" "$@"
