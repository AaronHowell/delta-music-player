#!/usr/bin/env bash
# One-time setup of the MELODIA extraction environment inside WSL.
# MUST be run as root:  wsl -d <distro> -u root bash < this file
# Creates ~<user>/dmp-melodia venv, installs essentia + numpy, chowns to user.
set -e

TARGET_USER="${TARGET_USER:-$(getent passwd 1000 | cut -d: -f1)}"
if [ -z "$TARGET_USER" ]; then
    echo "ERROR: could not determine the default WSL user (UID 1000)" >&2
    exit 1
fi
echo "== python3: $(python3 --version) | target user: $TARGET_USER =="

apt-get update -qq
apt-get install -y -qq python3-venv python3.14-venv 2>/dev/null \
    || apt-get install -y -qq python3-venv

VENV="/home/$TARGET_USER/dmp-melodia"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
echo "== installing essentia + numpy (large wheels, may take minutes) =="
"$VENV/bin/pip" install --quiet essentia numpy
chown -R "$TARGET_USER:$TARGET_USER" "$VENV"

"$VENV/bin/python" - <<'EOF'
import essentia
import essentia.standard
import numpy
print("OK essentia", essentia.__version__, "numpy", numpy.__version__)
print("PredominantPitchMelodia available:",
      hasattr(essentia.standard, "PredominantPitchMelodia"))
EOF
