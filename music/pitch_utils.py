"""Pitch math: Hz <-> MIDI note number, MIDI number <-> note name.

Convention: MIDI 60 = C4, MIDI 69 = A4 = 440 Hz.
"""
from __future__ import annotations

import math
import re

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# flat name -> semitone within octave
_FLAT_MAP = {
    "DB": 1, "EB": 3, "GB": 6, "AB": 8, "BB": 10,
    "CB": 11, "FB": 4,
}

_NAME_RE = re.compile(r"^([A-Ga-g])([#b]?)(-?\d+)$")


def hz_to_midi(freq: float) -> float:
    """440 Hz -> 69.0.  freq <= 0 is voiced-out-of-range in MELODIA output;
    callers must filter those before calling."""
    if freq <= 0:
        raise ValueError(f"frequency must be > 0, got {freq}")
    return 69.0 + 12.0 * math.log2(freq / 440.0)


def midi_to_hz(midi: float) -> float:
    return 440.0 * math.pow(2.0, (midi - 69.0) / 12.0)


def midi_to_name(midi: int) -> str:
    """60 -> 'C4', 61 -> 'C#4'."""
    if not 0 <= midi <= 127:
        raise ValueError(f"MIDI note out of range: {midi}")
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def name_to_midi(name: str) -> int:
    """'C4' -> 60, 'c#4'/'Db4' -> 61.  Sharps and flats both accepted."""
    m = _NAME_RE.match(name.strip())
    if not m:
        raise ValueError(f"invalid note name: {name!r}")
    letter, accidental, octave = m.group(1).upper(), m.group(2), int(m.group(3))
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[letter]
    if accidental == "#":
        base += 1
    elif accidental == "b":
        base -= 1
    midi = (octave + 1) * 12 + base
    if not 0 <= midi <= 127:
        raise ValueError(f"note name out of MIDI range: {name!r}")
    return midi
