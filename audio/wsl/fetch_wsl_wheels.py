#!/usr/bin/env python
"""Download manylinux cp314 wheels needed by the WSL MELODIA environment.

WSL networking is currently broken on this machine (mirrored mode fails to
start and falls back to a no-network mode), so we fetch the Linux wheels from
the Windows side — where the network works — and install them offline inside
WSL (see setup_wsl_offline.sh).

Usage:  python audio/wsl/fetch_wsl_wheels.py [target_dir]
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

PYTHON_TAG = "cp314"
PLATFORM_PAT = re.compile(r"manylinux.*x86_64")

# package -> extra pip-style requirement constraint (version pin if needed)
# six: essentia's python wrapper imports it at runtime
PACKAGES = ["essentia", "numpy", "six"]


def pick_wheel(urls: list[dict], package: str) -> dict | None:
    """Prefer cp314 manylinux x86_64 wheel; fall back to py3-none-any."""
    candidates = [u for u in urls if u["filename"].endswith(".whl")]
    for u in candidates:
        fn = u["filename"]
        if PYTHON_TAG in fn and PLATFORM_PAT.search(fn):
            return u
    for u in candidates:
        fn = u["filename"]
        if "py3-none-any" in fn:
            return u
    return None


def fetch(package: str, target_dir: Path) -> None:
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/{package}/json", timeout=60
    ) as r:
        data = json.load(r)
    version = data["info"]["version"]
    files = data["releases"].get(version, [])
    wheel = pick_wheel(files, package)
    if wheel is None:
        raise SystemExit(
            f"No suitable linux/cp314 wheel for {package} {version}; "
            f"available: {[f['filename'] for f in files][:10]}"
        )
    out = target_dir / wheel["filename"]
    if out.exists() and out.stat().st_size == wheel["size"]:
        print(f"already downloaded: {out.name}")
        return
    print(f"downloading {wheel['filename']} "
          f"({wheel['size'] / 1e6:.1f} MB) ...")
    urllib.request.urlretrieve(wheel["url"], out)
    print(f"  -> {out}")


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("wsl_wheels")
    target.mkdir(parents=True, exist_ok=True)
    for pkg in PACKAGES:
        fetch(pkg, target)
    print("\nDone. Wheels in", target.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
