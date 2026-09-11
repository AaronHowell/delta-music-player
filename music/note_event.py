"""Unified note representation.

Every source (MIDI file, MELODIA contour, basic-pitch, ...) is converted to a
flat list of :class:`NoteEvent` on an absolute float-seconds timeline.  All
downstream modules (transposer, mapper, scheduler) only ever see NoteEvents and
never need to know where the notes came from.

Design borrowed from MeowField_AutoPiano (GPL-3.0, ideas only): the parsing
layer fully absorbs tempo maps / ticks so the domain layer stays trivially
testable.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List

from music.pitch_utils import midi_to_name


@dataclass
class NoteEvent:
    """One discrete note.

    pitch:      MIDI note number (60 = C4)
    start:      seconds from song start
    duration:   seconds (> 0)
    velocity:   0..127 (informational for the game instrument)
    confidence: 0..1 (1.0 for MIDI sources, extractor confidence for audio)
    """

    pitch: int
    start: float
    duration: float
    velocity: int = 100
    confidence: float = 1.0

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def name(self) -> str:
        return midi_to_name(self.pitch)


def sort_notes(notes: Iterable[NoteEvent]) -> List[NoteEvent]:
    return sorted(notes, key=lambda n: (n.start, n.pitch))


def notes_to_dicts(notes: Iterable[NoteEvent]) -> List[dict]:
    """Serialize to the output/<stem>_notes.json format (mapping fields are
    added later by the app once the instrument mapper has run)."""
    out = []
    for n in notes:
        d = {
            "time": round(n.start, 6),
            "duration": round(n.duration, 6),
            "pitch": n.pitch,
            "note": n.name,
            "velocity": n.velocity,
            "confidence": round(n.confidence, 4),
        }
        out.append(d)
    return out


def notes_to_json(notes: Iterable[NoteEvent], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(notes_to_dicts(notes), f, ensure_ascii=False, indent=1)
    return path


def notes_from_json(path: str | Path) -> List[NoteEvent]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [
        NoteEvent(
            pitch=int(d["pitch"]),
            start=float(d["time"]),
            duration=float(d["duration"]),
            velocity=int(d.get("velocity", 100)),
            confidence=float(d.get("confidence", 1.0)),
        )
        for d in data
    ]


def summarize(notes: List[NoteEvent]) -> dict:
    if not notes:
        return {"count": 0}
    pitches = [n.pitch for n in notes]
    lo, hi = min(pitches), max(pitches)
    return {
        "count": len(notes),
        "lowest_pitch": lo,
        "highest_pitch": hi,
        "lowest_note": midi_to_name(lo),
        "highest_note": midi_to_name(hi),
        "total_duration": max(n.end for n in notes),
    }
