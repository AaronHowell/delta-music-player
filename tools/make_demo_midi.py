#!/usr/bin/env python
"""Generate a small demo MIDI file for testing: 'Twinkle Twinkle Little Star'
as a monophonic melody track + a chordal accompaniment track + a tempo track.

Usage:  python tools/make_demo_midi.py [output.mid]
"""
import sys
from pathlib import Path

import mido

# Twinkle Twinkle (C major), quarter=0.5s at 120 BPM. (pitch, beats)
MELODY = [
    (60, 1), (60, 1), (67, 1), (67, 1), (69, 1), (69, 1), (67, 2),
    (65, 1), (65, 1), (64, 1), (64, 1), (62, 1), (62, 1), (60, 2),
    (67, 1), (67, 1), (65, 1), (65, 1), (64, 1), (64, 1), (62, 2),
    (67, 1), (67, 1), (65, 1), (65, 1), (64, 1), (64, 1), (62, 2),
    (60, 1), (60, 1), (67, 1), (67, 1), (69, 1), (69, 1), (67, 2),
    (65, 1), (65, 1), (64, 1), (64, 1), (62, 1), (62, 1), (60, 2),
]

TPB = 480


def build(path: Path) -> None:
    mid = mido.MidiFile(type=1, ticks_per_beat=TPB)
    mid.tracks.clear()

    t0 = mido.MidiTrack()  # tempo track
    t0.append(mido.MetaMessage("track_name", name="tempo", time=0))
    t0.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    t0.append(mido.MetaMessage("end_of_track", time=0))

    t1 = mido.MidiTrack()  # melody
    t1.append(mido.MetaMessage("track_name", name="melody", time=0))
    t1.append(mido.Message("program_change", program=73, time=0, channel=0))
    for pitch, beats in MELODY:
        ticks = int(beats * TPB)
        t1.append(mido.Message("note_on", note=pitch, velocity=96,
                               time=0, channel=0))
        t1.append(mido.Message("note_off", note=pitch, velocity=0,
                               time=ticks, channel=0))
    t1.append(mido.MetaMessage("end_of_track", time=0))

    t2 = mido.MidiTrack()  # simple chord accompaniment (lower, polyphonic)
    t2.append(mido.MetaMessage("track_name", name="chords", time=0))
    t2.append(mido.Message("program_change", program=0, time=0, channel=1))
    chords = [(48, 52, 55), (48, 52, 55), (45, 48, 52), (47, 50, 55)]
    for i in range(16):
        ch = chords[i % len(chords)]
        for p in ch:
            t2.append(mido.Message("note_on", note=p, velocity=64,
                                   time=0, channel=1))
        for p in ch:
            t2.append(mido.Message("note_off", note=p, velocity=0,
                                   time=TPB * 2 // len(ch) if p != ch[-1]
                                   else TPB * 2 - (TPB * 2 // len(ch)) * (len(ch) - 1),
                                   channel=1))
    t2.append(mido.MetaMessage("end_of_track", time=0))

    mid.tracks.extend([t0, t1, t2])
    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(path))
    print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/twinkle.mid")
    build(out)
