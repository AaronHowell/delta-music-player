#!/usr/bin/env python
"""Phase 3/4 manual in-game test: a handful of hand-written NoteEvents played
through the real SendInput backend.

Covers exactly the acceptance items:
  * plain keys        Z X C V B N M ,
  * sharp modifier    (mouse_middle by default)
  * upper / lower     (mouse_right / mouse_left)
  * multi-modifier    lower + sharp
  * long note         (2 s hold)
  * repeated note     (same key twice -> retrigger gap)

Usage:
    python tools/manual_play_test.py                 # 5 s countdown, then play
    python tools/manual_play_test.py --dry-run       # print only
    python tools/manual_play_test.py --instrument config/instrument.json

Open the game, equip the instrument, and watch/Listen.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from instrument.instrument_profile import InstrumentProfile
from instrument.mapper import InstrumentMapper, MappedNote
from music.note_event import NoteEvent
from playback.input_backend import DryRunBackend, Win32SendInputBackend
from playback.player import Player


def demo_notes() -> list[NoteEvent]:
    """A tiny hand-written phrase exercising every feature."""
    return [
        # plain scale C4 D4 E4 F4 G4 A4 B4 C5
        NoteEvent(pitch=60, start=0.0, duration=0.35),
        NoteEvent(pitch=62, start=0.4, duration=0.35),
        NoteEvent(pitch=64, start=0.8, duration=0.35),
        NoteEvent(pitch=65, start=1.2, duration=0.35),
        NoteEvent(pitch=67, start=1.6, duration=0.35),
        NoteEvent(pitch=69, start=2.0, duration=0.35),
        NoteEvent(pitch=71, start=2.4, duration=0.35),
        NoteEvent(pitch=72, start=2.8, duration=0.7),
        # sharp: C#4 D#4
        NoteEvent(pitch=61, start=3.8, duration=0.35),
        NoteEvent(pitch=63, start=4.2, duration=0.35),
        # upper octave via modifier: C5 D5 (if your calibration uses upper)
        NoteEvent(pitch=74, start=4.8, duration=0.35),
        # lower octave: C3 D3
        NoteEvent(pitch=48, start=5.4, duration=0.35),
        NoteEvent(pitch=50, start=5.8, duration=0.35),
        # multi-modifier: C#3 = lower + sharp + z
        NoteEvent(pitch=49, start=6.4, duration=0.5),
        # long note: G4 held 2 s
        NoteEvent(pitch=67, start=7.2, duration=2.0),
        # repeated same key: E4 x3 (retrigger gap matters here)
        NoteEvent(pitch=64, start=9.5, duration=0.15),
        NoteEvent(pitch=64, start=9.7, duration=0.15),
        NoteEvent(pitch=64, start=9.9, duration=0.4),
    ]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--instrument", default="config/instrument.json")
    p.add_argument("--settings", default="config/settings.json")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--start-delay", type=float, default=5.0)
    args = p.parse_args()

    profile = InstrumentProfile.load(args.instrument)
    print(profile.describe())

    mapper = InstrumentMapper(profile)
    notes = demo_notes()
    mapped, unmapped = mapper.map_notes(notes)
    for n in unmapped:
        print(f"  !! pitch {n.pitch} ({n.name}) not playable with this "
              f"profile — check your calibration")
    if not mapped:
        return 1

    print("\nPhrase:")
    for m in mapped:
        print(f"  {m.note.start:6.2f}  {m.note.name:4s} -> "
              f"{m.combo.display()}  (dur={m.note.duration:.2f})")

    if args.dry_run:
        return 0

    import json
    settings = {}
    sp = Path(args.settings)
    if sp.exists():
        settings = json.loads(sp.read_text(encoding="utf-8"))

    backend = Win32SendInputBackend()
    player = Player(backend, settings)
    print("\nFocus the GAME now (instrument equipped)!")
    stats = player.play(mapped, profile, start_delay=args.start_delay)
    print()
    print(stats.format())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
