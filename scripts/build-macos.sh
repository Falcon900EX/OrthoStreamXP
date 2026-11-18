#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "Build in a native Apple Silicon terminal, not Rosetta." >&2
  exit 1
fi
command -v dotnet >/dev/null || { echo "Install the native .NET 10 SDK first." >&2; exit 1; }
[[ "$(dotnet --version)" == 10.* ]] || { echo "This source uses the .NET 10 SDK and its test runner." >&2; exit 1; }
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import platform,sys; assert platform.machine()=="arm64", "Use ARM64 Python"; assert sys.version_info >= (3,10), "Use Python 3.10 or later"'
if [[ ! -x .venv-macos/bin/python ]]; then "$PYTHON" -m venv .venv-macos; fi
.venv-macos/bin/python -c 'import platform; assert platform.machine()=="arm64", "Remove/recreate the Rosetta venv"'
.venv-macos/bin/python -m pip install --require-hashes -r platform/macos/requirements.txt
export PYTHONDONTWRITEBYTECODE=1
.venv-macos/bin/python -m unittest discover -s platform/macos/tests -v
dotnet restore OrthoStreamXP.Mac.slnx
dotnet build OrthoStreamXP.Mac.slnx -c Release --no-restore
dotnet test --solution OrthoStreamXP.Mac.slnx --configuration Release --no-build
dotnet publish src/OrthoStreamXP.Mac/OrthoStreamXP.Mac.csproj -c Release -r osx-arm64 --self-contained true -o artifacts/macos-arm64
cp LICENSE THIRD_PARTY_NOTICES.md README-macOS.md artifacts/macos-arm64/
printf '\nBuilt: %s/artifacts/macos-arm64/OrthoStreamXP.Mac\n' "$ROOT"
echo 'Next: bash scripts/run-macos.sh doctor'
echo 'Then: bash scripts/smoke-macos.sh  (synthetic data; no X-Plane needed)'
