"""NoteEvent -> game input mapping.

Thin layer over InstrumentProfile: turns notes into (note, InputCombination)
pairs and reports anything the instrument cannot play (should be empty after
transposition — this is the safety net).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from instrument.instrument_profile import InputCombination, InstrumentProfile
from music.note_event import NoteEvent


@dataclass
class MappedNote:
    note: NoteEvent
    combo: InputCombination


class InstrumentMapper:
    def __init__(self, profile: InstrumentProfile):
        self.profile = profile

    def map_pitch(self, pitch: int) -> Optional[InputCombination]:
        return self.profile.combination_for(pitch)

    def map_notes(
        self, notes: List[NoteEvent]
    ) -> Tuple[List[MappedNote], List[NoteEvent]]:
        mapped: List[MappedNote] = []
        unmapped: List[NoteEvent] = []
        for n in notes:
            combo = self.profile.combination_for(n.pitch)
            if combo is None:
                unmapped.append(n)
            else:
                mapped.append(MappedNote(note=n, combo=combo))
        return mapped, unmapped
