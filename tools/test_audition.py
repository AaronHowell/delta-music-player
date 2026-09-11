#!/usr/bin/env python
"""Manual test for the MCI MIDI auditioner — you should HEAR piano notes.

Plays samples/twinkle.mid for ~3 seconds (polling the position), then stops.

Usage:  python tools/test_audition.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playback.audition import AuditionError, MidiAuditioner  # noqa: E402


def main() -> int:
    midi = ROOT / "samples" / "twinkle.mid"
    if not midi.exists():
        print(f"missing {midi} — run tools/make_demo_midi.py first")
        return 1

    aud = MidiAuditioner()
    try:
        print(f"playing {midi.name} for ~3 s (you should hear piano)...")
        aud.play(midi)
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 3.0:
            pos = aud.position_ms()
            print(f"  playing={aud.is_playing()}  position={pos} ms")
            time.sleep(0.75)
        assert aud.position_ms() and aud.position_ms() > 500, \
            "position did not advance — sequencer may be unavailable"
    except AuditionError as e:
        print(f"AUDITION FAILED: {e}")
        print("(needs the built-in Microsoft GS Wavetable Synth)")
        return 2
    finally:
        aud.stop()
    print(f"stopped. is_playing={aud.is_playing()}, "
          f"position={aud.position_ms()}")
    print("audition OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
