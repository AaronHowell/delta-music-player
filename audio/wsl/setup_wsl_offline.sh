#!/usr/bin/env bash
# Offline setup of the MELODIA extraction environment inside WSL.
# Run as root FROM THE PROJECT DIRECTORY (WSL inherits the Windows cwd):
#   ((Get-Content audio\wsl\setup_wsl_offline.sh -Raw) -replace "`r","") | wsl -d Ubuntu-26.04 -u root bash
# Expects Linux wheels (essentia, numpy, six) pre-downloaded by the Windows
# side into <project>/wsl_wheels (audio/wsl/fetch_wsl_wheels.py).
# No WSL network needed. Override WHEELS_WIN if your layout differs.
set -e

TARGET_USER="${TARGET_USER:-$(getent passwd 1000 | cut -d: -f1)}"
WHEELS_WIN="${WHEELS_WIN:-$PWD/wsl_wheels}"

if [ -z "$TARGET_USER" ]; then
    echo "ERROR: could not determine the default WSL user (UID 1000)" >&2
    exit 1
fi
if [ ! -d "$WHEELS_WIN" ]; then
    echo "ERROR: wheel directory not found: $WHEELS_WIN" >&2
    echo "Run audio/wsl/fetch_wsl_wheels.py on the Windows side first." >&2
    exit 1
fi
echo "== python3: $(python3 --version) | user: $TARGET_USER | wheels: $WHEELS_WIN =="

PY_VER=$(python3 -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')
VENV="/home/$TARGET_USER/dmp-melodia"
SITE="$VENV/lib/$PY_VER/site-packages"

# venv without pip (ensurepip/python3-venv may be missing; we don't need pip)
rm -rf "$VENV"
python3 -m venv --without-pip "$VENV"
mkdir -p "$SITE"

# wheels are zip archives: extracting them into site-packages is exactly what
# pip does for pure-binary packages like these
for whl in "$WHEELS_WIN"/*.whl; do
    echo "== installing $(basename "$whl") =="
    python3 -m zipfile -e "$whl" "$SITE/"
done

chown -R "$TARGET_USER:$TARGET_USER" "$VENV"

echo "== verifying as $TARGET_USER =="
su "$TARGET_USER" -c "$VENV/bin/python - <<'EOF'
import essentia
import essentia.standard
import numpy
print('OK essentia', essentia.__version__, 'numpy', numpy.__version__)
print('PredominantPitchMelodia available:',
      hasattr(essentia.standard, 'PredominantPitchMelodia'))
EOF"
