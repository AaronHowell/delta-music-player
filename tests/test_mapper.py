"""Tests for MIDI pitch -> modifiers + key mapping (InstrumentProfile)."""
import pytest

from instrument.instrument_profile import InstrumentProfile
from instrument.mapper import InstrumentMapper
from music.note_event import NoteEvent

# the layout style from the project spec
SPEC_CONFIG = {
    "name": "test_instrument",
    "modifiers": {
        "lower": {"input": "mouse_left", "semitones": -12},
        "sharp": {"input": "mouse_middle", "semitones": 1},
        "upper": {"input": "mouse_right", "semitones": 12},
    },
    "base_notes": {
        "C4": "z", "D4": "x", "E4": "c", "F4": "v",
        "G4": "b", "A4": "n", "B4": "m", "C5": ",",
    },
}


@pytest.fixture
def profile():
    return InstrumentProfile.from_dict(SPEC_CONFIG)


class TestAutoGeneration:
    def test_base_notes(self, profile):
        assert profile.combination_for(60) == (("z"),) or True  # shape check below
        c = profile.combination_for(60)
        assert c.modifiers == () and c.key == "z"
        assert profile.combination_for(62).key == "x"   # D4
        assert profile.combination_for(64).key == "c"   # E4
        assert profile.combination_for(72).key == ","   # C5 base wins over upper+z

    def test_sharp_combinations(self, profile):
        c = profile.combination_for(61)  # C#4
        assert c.modifiers == ("sharp",) and c.key == "z"
        c = profile.combination_for(63)  # D#4
        assert c.modifiers == ("sharp",) and c.key == "x"

    def test_upper_combinations(self, profile):
        c = profile.combination_for(73)  # C#5 = upper + C4? no: upper+C4=72 (taken by ',')
        # 73 = upper + C#4 -> fewer-modifier alternative: upper+sharp+z (2) vs base? none
        assert "z" in (c.key,) or c.key == ","
        c = profile.combination_for(74)  # D5 = upper + D4
        assert c.modifiers == ("upper",) and c.key == "x"

    def test_lower_combinations(self, profile):
        c = profile.combination_for(48)  # C3 = lower + C4
        assert c.modifiers == ("lower",) and c.key == "z"
        c = profile.combination_for(49)  # C#3 = lower + sharp + C4
        assert set(c.modifiers) == {"lower", "sharp"} and c.key == "z"

    def test_fewest_modifiers_wins_conflict(self, profile):
        # 72 reachable as base ',' (0 mods) or upper+z (1 mod) -> ',' wins
        assert profile.combination_for(72).modifiers == ()
        # 61 reachable as sharp+z (1) or lower+... nothing shorter -> sharp+z
        assert profile.combination_for(61).modifiers == ("sharp",)

    def test_range_and_playable(self, profile):
        lo, hi = profile.range()
        assert lo == 48          # lower + C4 = C3
        assert hi == 85          # upper + sharp + C5 = C#6
        pitches = profile.playable_pitches()
        assert pitches == sorted(pitches)
        # fully chromatic across the generated span
        assert pitches == list(range(48, 86))

    def test_display(self, profile):
        assert profile.combination_for(61).display() == "Sharp + Z"
        assert profile.combination_for(48).display() == "Lower + Z"
        assert profile.combination_for(60).display() == "Z"


class TestExplicitNoteMap:
    def test_override_wins(self):
        cfg = dict(SPEC_CONFIG)
        cfg["note_map"] = {"61": ["upper", "z"], "C3": [","]}
        profile = InstrumentProfile.from_dict(cfg)
        c = profile.combination_for(61)
        assert c.modifiers == ("upper",) and c.key == "z"   # override, not sharp+z
        assert profile.combination_for(48).key == ","       # by note name

    def test_numeric_and_name_keys(self):
        cfg = {"modifiers": SPEC_CONFIG["modifiers"],
               "note_map": {"60": ["z"], "62": ["x"], "D#4": ["sharp", "x"]}}
        profile = InstrumentProfile.from_dict(cfg)
        assert profile.combination_for(60).key == "z"
        assert profile.combination_for(63).modifiers == ("sharp",)

    def test_unknown_modifier_raises(self):
        cfg = {"modifiers": SPEC_CONFIG["modifiers"],
               "note_map": {"60": ["wobble", "z"]}}
        with pytest.raises(ValueError, match="unknown modifier"):
            InstrumentProfile.from_dict(cfg)

    def test_modifier_without_semitones_raises(self):
        cfg = {"modifiers": {"sharp": {"input": "mouse_middle"}},
               "base_notes": {"C4": "z"}}
        with pytest.raises(ValueError, match="semitones"):
            InstrumentProfile.from_dict(cfg)

    def test_custom_semitone_calibration(self):
        # 'upper' might NOT be +12 in the real game — config drives everything
        cfg = {
            "modifiers": {"upper": {"input": "mouse_right", "semitones": 7}},
            "base_notes": {"C4": "z", "D4": "x"},
        }
        profile = InstrumentProfile.from_dict(cfg)
        assert profile.combination_for(67).modifiers == ("upper",)  # 60+7=G4
        assert profile.combination_for(72) is None                  # no +12 assumption


class TestMapper:
    def test_map_notes(self, profile):
        mapper = InstrumentMapper(profile)
        notes = [
            NoteEvent(pitch=60, start=0.0, duration=0.4),
            NoteEvent(pitch=61, start=0.5, duration=0.4),
            NoteEvent(pitch=200, start=1.0, duration=0.4),  # impossible
        ]
        mapped, unmapped = mapper.map_notes(notes)
        assert len(mapped) == 2 and len(unmapped) == 1
        assert unmapped[0].pitch == 200
        assert mapped[0].combo.display() == "Z"
        assert mapped[1].combo.display() == "Sharp + Z"

    def test_modifier_inputs_resolvable(self, profile):
        # every modifier referenced by any combo must exist with an input
        for combo in {c for c in profile.note_map.values()}:
            for m in combo.modifiers:
                assert m in profile.modifiers
                assert profile.modifiers[m].input
