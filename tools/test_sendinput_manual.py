#!/usr/bin/env python
"""Manual smoke test for the Win32 SendInput backend (Phase 3).

Open Notepad (or keep this console focused), then run:

    python tools/test_sendinput_manual.py            # keyboard only
    python tools/test_sendinput_manual.py --mouse    # also click mouse buttons
                                                   # (park the cursor somewhere
                                                   #  harmless first!)

After the countdown you should see  zxcvbnm,  typed (with a 0.3 s hold on
each key), then F13 presses (harmless no-op key), and with --mouse the three
mouse buttons clicked at the current cursor position.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playback.win32_sendinput import (  # noqa: E402
    input_down,
    input_up,
    key_down,
    key_up,
)


def countdown(sec=3):
    for i in range(sec, 0, -1):
        print(f"starting in {i} ...", flush=True)
        time.sleep(1.0)


def main() -> int:
    do_mouse = "--mouse" in sys.argv
    print("Focus Notepad or this console NOW.")
    countdown(3)

    print("typing z x c v b n m ,  (0.3 s hold each)")
    for ch in "zxcvbnm,":
        key_down(ch)
        time.sleep(0.3)
        key_up(ch)
        time.sleep(0.15)

    print("pressing F13 twice (harmless)")
    for _ in range(2):
        key_down("f13")
        time.sleep(0.05)
        key_up("f13")
        time.sleep(0.1)

    if do_mouse:
        for button in ("mouse_left", "mouse_middle", "mouse_right"):
            print(f"clicking {button} at current cursor position")
            input_down(button)
            time.sleep(0.05)
            input_up(button)
            time.sleep(0.3)

    print("done — verify the characters/clicks arrived in the focused window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
