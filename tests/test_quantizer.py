"""Tests for F0 contour -> NoteEvent quantization."""
import math

import pytest

from music.quantizer import (
    contour_to_notes,
    hz_contour_to_midi_float,
    median_filter,
)
from music.pitch_utils import midi_to_hz

HOP = 0.01  # 10 ms frames


def segments_contour(segments):
    """segments: list of (freq_hz or None, n_frames, confidence)."""
    freqs, confs = [], []
    for f, n, c in segments:
        freqs.extend([f if f is not None else 0.0] * n)
        confs.extend([c] * n)
    return freqs, confs


A4 = midi_to_hz(69)   # 440
C5 = midi_to_hz(72)
D5 = midi_to_hz(74)
G4 = midi_to_hz(67)


class TestHzToMidiContour:
    def test_voiced_and_unvoiced(self):
        out = hz_contour_to_midi_float(
            [440.0, 0.0, -440.0, 440.0], [1.0, 1.0, 1.0, 0.1], 0.5
        )
        assert out[0] == pytest.approx(69.0)
        assert out[1] is None        # freq 0 = unvoiced
        assert out[2] is None        # negative freq = unvoiced
        assert out[3] is None        # confidence below threshold


class TestMedianFilter:
    def test_removes_single_outlier(self):
        vals = [69.0] * 10 + [81.0] + [69.0] * 10
        out = median_filter(vals, 5)
        assert out[10] == 69.0

    def test_handles_none(self):
        vals = [69.0, None, 69.0, None, None]
        out = median_filter(vals, 3)
        assert out[0] == 69.0
        assert out[1] == 69.0        # median of neighbours
        assert out[4] is None        # window has no values? (69 @2 out of range)

    def test_window_1_passthrough(self):
        vals = [1.0, None, 3.0]
        assert median_filter(vals, 1) == vals


class TestTwoNotes:
    def test_pure_step(self):
        freqs, confs = segments_contour([
            (A4, 50, 0.9),     # 0.0 - 0.5
            (C5, 50, 0.9),     # 0.5 - 1.0
        ])
        notes = contour_to_notes(freqs, confs, HOP)
        assert len(notes) == 2
        assert notes[0].pitch == 69
        assert notes[0].start == pytest.approx(0.0)
        assert notes[0].duration == pytest.approx(0.5, abs=HOP * 3)
        assert notes[1].pitch == 72
        assert notes[1].start == pytest.approx(0.5, abs=HOP * 3)

    def test_three_note_melody(self):
        freqs, confs = segments_contour([
            (A4, 40, 0.9), (C5, 40, 0.9), (D5, 60, 0.9),
        ])
        notes = contour_to_notes(freqs, confs, HOP)
        assert [n.pitch for n in notes] == [69, 72, 74]
        assert notes[2].duration == pytest.approx(0.6, abs=HOP * 3)


class TestVibratoAndJitter:
    def test_vibrato_stays_one_note(self):
        # 5.5 Hz vibrato, +-30 cents around A4 -> must not split
        n = 200
        freqs, confs = [], []
        for i in range(n):
            t = i * HOP
            dev = 0.3 * math.sin(2 * math.pi * 5.5 * t)   # semitones
            freqs.append(midi_to_hz(69 + dev))
            confs.append(0.9)
        notes = contour_to_notes(freqs, confs, HOP)
        assert len(notes) == 1
        assert notes[0].pitch == 69
        assert notes[0].duration == pytest.approx(n * HOP, abs=HOP * 3)

    def test_jitter_frames_smoothed(self):
        # a few frames 40 cents off should not create notes
        freqs, confs = segments_contour([(A4, 60, 0.9)])
        for i in (10, 25, 41):
            freqs[i] = midi_to_hz(69.4)
        notes = contour_to_notes(freqs, confs, HOP)
        assert len(notes) == 1 and notes[0].pitch == 69


class TestSpikesAndGaps:
    def test_short_wrong_pitch_removed_and_hole_merged(self):
        # A4, 3-frame C5 spike (< min_note_duration), A4 -> one A4 note
        freqs, confs = segments_contour([
            (A4, 40, 0.9), (C5, 3, 0.9), (A4, 40, 0.9),
        ])
        notes = contour_to_notes(
            freqs, confs, HOP, min_note_duration=0.08, merge_gap=0.05
        )
        assert len(notes) == 1
        assert notes[0].pitch == 69
        assert notes[0].duration == pytest.approx(0.83, abs=HOP * 4)

    def test_short_unvoiced_gap_bridged(self):
        freqs, confs = segments_contour([
            (A4, 30, 0.9), (None, 3, 0.0), (A4, 30, 0.9),
        ])
        notes = contour_to_notes(freqs, confs, HOP, merge_gap=0.05)
        assert len(notes) == 1
        assert notes[0].duration == pytest.approx(0.63, abs=HOP * 4)

    def test_long_silence_splits_notes(self):
        freqs, confs = segments_contour([
            (A4, 30, 0.9), (None, 20, 0.0), (A4, 30, 0.9),
        ])
        notes = contour_to_notes(freqs, confs, HOP, merge_gap=0.05)
        assert len(notes) == 2
        assert all(n.pitch == 69 for n in notes)
        # the median filter bleeds voiced frames half a window into the
        # silence, so allow +-3 frames of slack on the boundary
        assert notes[1].start == pytest.approx(0.5, abs=HOP * 3)

    def test_low_confidence_regions_are_unvoiced(self):
        freqs, confs = segments_contour([
            (A4, 30, 0.9), (C5, 30, 0.1), (None, 0, 0.0), (D5, 30, 0.9),
        ])
        notes = contour_to_notes(freqs, confs, HOP, confidence_threshold=0.5)
        # the low-confidence C5 region is treated as silence (gap > merge_gap)
        assert [n.pitch for n in notes] == [69, 74]


class TestGlissando:
    def test_sweep_produces_ascending_chain(self):
        n = 120
        freqs, confs = [], []
        for i in range(n):
            f = 69.0 + 12.0 * (i / (n - 1))     # 69 -> 81 over 1.2 s
            freqs.append(midi_to_hz(f))
            confs.append(0.9)
        # min_note_duration lowered: glissando fragments are ~60 ms; with
        # the default 0.08 s the very first fragment is legitimately filtered
        notes = contour_to_notes(freqs, confs, HOP, min_note_duration=0.03)
        assert len(notes) > 3
        pitches = [x.pitch for x in notes]
        assert pitches == sorted(pitches)        # monotonic ascent
        assert pitches[0] == 69
        assert pitches[-1] == 81


class TestEdges:
    def test_empty_contour(self):
        assert contour_to_notes([], [], HOP) == []

    def test_all_unvoiced(self):
        freqs, confs = segments_contour([(None, 50, 0.0)])
        assert contour_to_notes(freqs, confs, HOP) == []

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            contour_to_notes([440.0], [], HOP)

    def test_bad_hop_raises(self):
        with pytest.raises(ValueError):
            contour_to_notes([], [], 0.0)

    def test_velocity_from_confidence(self):
        freqs, confs = segments_contour([(A4, 30, 0.5)])
        notes = contour_to_notes(freqs, confs, HOP)
        assert notes[0].velocity == int(0.5 * 127)
        assert notes[0].confidence == pytest.approx(0.5)
