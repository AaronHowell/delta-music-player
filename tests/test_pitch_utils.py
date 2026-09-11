import math

import pytest

from music.pitch_utils import (
    hz_to_midi,
    midi_to_hz,
    midi_to_name,
    name_to_midi,
)


class TestHzToMidi:
    def test_a4_440(self):
        assert hz_to_midi(440.0) == pytest.approx(69.0)

    def test_c4(self):
        # C4 = 261.6256 Hz
        assert hz_to_midi(261.6256) == pytest.approx(60.0, abs=1e-3)

    def test_octave_doubling_adds_12(self):
        assert hz_to_midi(880.0) - hz_to_midi(440.0) == pytest.approx(12.0)

    def test_semitone_ratio(self):
        ratio = 2 ** (1 / 12)
        assert hz_to_midi(440.0 * ratio) == pytest.approx(70.0)

    def test_invalid_frequency(self):
        with pytest.raises(ValueError):
            hz_to_midi(0.0)
        with pytest.raises(ValueError):
            hz_to_midi(-3.0)


class TestMidiToHz:
    def test_roundtrip(self):
        for m in (36, 48, 60, 69, 72, 96):
            assert hz_to_midi(midi_to_hz(m)) == pytest.approx(float(m))

    def test_a4(self):
        assert midi_to_hz(69) == pytest.approx(440.0)


class TestNames:
    def test_midi_to_name(self):
        assert midi_to_name(60) == "C4"
        assert midi_to_name(61) == "C#4"
        assert midi_to_name(69) == "A4"
        assert midi_to_name(72) == "C5"
        assert midi_to_name(0) == "C-1"
        assert midi_to_name(127) == "G9"

    def test_name_to_midi_sharp(self):
        assert name_to_midi("C4") == 60
        assert name_to_midi("C#4") == 61
        assert name_to_midi("c#4") == 61
        assert name_to_midi("A4") == 69
        assert name_to_midi("C5") == 72

    def test_name_to_midi_flat(self):
        assert name_to_midi("Db4") == 61
        assert name_to_midi("Bb4") == 70
        assert name_to_midi("Eb3") == 51

    def test_roundtrip(self):
        for m in range(0, 128):
            assert name_to_midi(midi_to_name(m)) == m

    def test_invalid(self):
        with pytest.raises(ValueError):
            name_to_midi("H4")
        with pytest.raises(ValueError):
            name_to_midi("C")
        with pytest.raises(ValueError):
            midi_to_name(128)
