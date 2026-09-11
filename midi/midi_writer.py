"""NoteEvent[] -> standard MIDI file (for checking extracted melodies in a
DAW / MIDI editor — the spec's key debug capability)."""
from __future__ import annotations

from pathlib import Path
from typing import List

import mido

from music.note_event import NoteEvent


def write_midi(
    notes: List[NoteEvent],
    path: str | Path,
    *,
    tempo_bpm: float = 120.0,
    ticks_per_beat: int = 480,
    program: int = 73,     # flute-ish lead voice
    channel: int = 0,
    track_name: str = "extracted melody",
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tempo_us = int(mido.bpm2tempo(tempo_bpm))
    sec_per_tick = tempo_us / ticks_per_beat / 1_000_000.0

    mid = mido.MidiFile(type=0, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name=track_name, time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
    track.append(mido.Message("program_change", program=program,
                              channel=channel, time=0))

    # (second, rank, msg): note-off before note-on at the same instant
    events = []
    for n in notes:
        events.append((
            n.start, 1,
            mido.Message("note_on", note=n.pitch, velocity=n.velocity,
                         channel=channel, time=0),
        ))
        events.append((
            n.end, 0,
            mido.Message("note_off", note=n.pitch, velocity=0,
                         channel=channel, time=0),
        ))
    events.sort(key=lambda e: (e[0], e[1]))

    last_tick = 0
    for sec, _rank, msg in events:
        tick = max(0, round(sec / sec_per_tick))
        msg.time = max(0, tick - last_tick)
        last_tick = tick
        track.append(msg)
    track.append(mido.MetaMessage("end_of_track", time=0))

    mid.save(str(path))
    return path
