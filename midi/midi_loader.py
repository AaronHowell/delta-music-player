"""MIDI file loading helpers."""
from __future__ import annotations

from pathlib import Path

import mido

MIDI_EXTENSIONS = {".mid", ".midi"}


def is_midi(path: str | Path) -> bool:
    """True if the path looks like a MIDI file (extension or 'MThd' header)."""
    path = Path(path)
    if path.suffix.lower() in MIDI_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"MThd"
    except OSError:
        return False


def load_midi(path: str | Path) -> mido.MidiFile:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"MIDI file not found: {path}")
    if not is_midi(path):
        raise ValueError(f"not a MIDI file: {path}")
    mid = mido.MidiFile(str(path))
    if mid.type == 2:
        raise ValueError(
            f"MIDI type 2 (multi-song files) is not supported: {path}"
        )
    return mid
