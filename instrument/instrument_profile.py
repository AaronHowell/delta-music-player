"""Instrument profile: loads config/instrument.json and builds the
MIDI-pitch -> InputCombination table.

Two mapping sources (explicit note_map wins per pitch):

1. ``note_map``: explicit ``{"60": ["z"], "61": ["sharp", "z"], ...}``
   (keys may be MIDI numbers as strings or note names like "C4").
   Last element of the list is the note key, everything before it names a
   modifier from ``modifiers``.

2. Auto-generation from ``base_notes`` x modifier subsets: every modifier
   declares the semitone offset it produces in-game (``semitones``), so the
   resulting pitch of a combination is base + sum(offsets).  Nothing about
   "+12 / -12 / +1" is hardcoded — it all comes from the JSON, so the mapping
   can be recalibrated after testing in game.  Conflicts (two combinations
   producing the same pitch) resolve to the one with the fewest modifiers,
   then by modifier declaration order.

Input identifiers understood by the playback backend:
  * single characters ``"z"``, ``","`` ...    -> keyboard key (scancode)
  * ``"mouse_left" | "mouse_right" | "mouse_middle"`` -> mouse buttons
  * other names (e.g. ``"space"``, ``"lshift"``)      -> keyboard key names
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from music.pitch_utils import midi_to_name, name_to_midi


@dataclass(frozen=True)
class InputCombination:
    """How to play one pitch: modifier names (press order) + the note key."""

    modifiers: Tuple[str, ...]
    key: str

    def display(self) -> str:
        parts = [m.capitalize() for m in self.modifiers] + [self.key.upper()]
        return " + ".join(parts)


@dataclass(frozen=True)
class Modifier:
    name: str
    input: str          # what to physically press, e.g. "mouse_middle"
    semitones: int      # pitch offset this modifier produces in game


class InstrumentProfile:
    def __init__(
        self,
        name: str,
        modifiers: Dict[str, Modifier],
        note_map: Dict[int, InputCombination],
        source_path: Optional[Path] = None,
    ):
        self.name = name
        self.modifiers = modifiers
        self.note_map = note_map
        self.source_path = source_path

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "InstrumentProfile":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"instrument config not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data, source_path=path)

    @classmethod
    def from_dict(cls, data: dict, source_path: Optional[Path] = None) -> "InstrumentProfile":
        modifiers: Dict[str, Modifier] = {}
        for name, spec in (data.get("modifiers") or {}).items():
            if isinstance(spec, str):        # shorthand: {"sharp": "mouse_middle"}
                spec = {"input": spec, "semitones": None}
            semitones = spec.get("semitones")
            if semitones is None:
                raise ValueError(
                    f"modifier {name!r} needs a 'semitones' value so the "
                    f"mapper can compute which pitches it reaches"
                )
            modifiers[name] = Modifier(
                name=name, input=spec["input"], semitones=int(semitones)
            )

        note_map: Dict[int, InputCombination] = {}

        # 1) auto-generate from base_notes x modifier subsets
        base_notes = data.get("base_notes") or {}
        if base_notes:
            auto = cls._generate(base_notes, modifiers)
            note_map.update(auto)

        # 2) explicit overrides win
        for key, combo in (data.get("note_map") or {}).items():
            pitch = cls._parse_pitch_key(key)
            note_map[pitch] = cls._parse_combo(combo, modifiers, key)

        if not note_map:
            raise ValueError(
                "instrument config produced an empty note map "
                "(need base_notes and/or note_map entries)"
            )
        return cls(
            name=data.get("name", "unnamed"),
            modifiers=modifiers,
            note_map=note_map,
            source_path=source_path,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_pitch_key(key) -> int:
        if isinstance(key, int):
            return key
        s = str(key).strip()
        if s.lstrip("-").isdigit():
            return int(s)
        return name_to_midi(s)

    @staticmethod
    def _parse_combo(combo, modifiers: Dict[str, Modifier], ctx) -> InputCombination:
        if isinstance(combo, str):
            combo = [combo]
        if not isinstance(combo, list) or not combo:
            raise ValueError(f"invalid key combination for {ctx!r}: {combo!r}")
        key = str(combo[-1])
        mods: List[str] = []
        for m in combo[:-1]:
            if m not in modifiers:
                raise ValueError(
                    f"unknown modifier {m!r} in mapping for {ctx!r}; "
                    f"declared modifiers: {sorted(modifiers)}"
                )
            mods.append(m)
        return InputCombination(modifiers=tuple(mods), key=key)

    @staticmethod
    def _generate(
        base_notes: dict, modifiers: Dict[str, Modifier]
    ) -> Dict[int, InputCombination]:
        """base_notes x all modifier subsets -> pitch table."""
        result: Dict[int, InputCombination] = {}
        mod_names = list(modifiers)  # declaration order = press order
        for note_name, key in base_notes.items():
            base_pitch = InstrumentProfile._parse_pitch_key(note_name)
            subsets: List[Tuple[str, ...]] = [()]
            for r in range(1, len(mod_names) + 1):
                subsets.extend(combinations(mod_names, r))
            for subset in subsets:
                offset = sum(modifiers[m].semitones for m in subset)
                pitch = base_pitch + offset
                if not 0 <= pitch <= 127:
                    continue
                combo = InputCombination(modifiers=subset, key=str(key))
                existing = result.get(pitch)
                # fewest modifiers wins; earlier declaration order breaks ties
                if existing is None or len(subset) < len(existing.modifiers):
                    result[pitch] = combo
        return result

    # ------------------------------------------------------------------
    def combination_for(self, pitch: int) -> Optional[InputCombination]:
        return self.note_map.get(pitch)

    def playable_pitches(self) -> List[int]:
        return sorted(self.note_map)

    def range(self) -> Tuple[int, int]:
        pitches = self.playable_pitches()
        return pitches[0], pitches[-1]

    def describe(self) -> str:
        lo, hi = self.range()
        lines = [
            f"Instrument: {self.name}",
            f"  playable: {midi_to_name(lo)}-{midi_to_name(hi)} "
            f"(MIDI {lo}-{hi}, {len(self.note_map)} pitches)",
            f"  modifiers: "
            + ", ".join(
                f"{m.name}({m.input}, {m.semitones:+d}st)"
                for m in self.modifiers.values()
            ),
        ]
        return "\n".join(lines)
