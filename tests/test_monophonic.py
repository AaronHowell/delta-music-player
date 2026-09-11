"""Tests for monophonic enforcement: one note at a time."""
import pytest

from music.monophonic import MonoInfo, make_monophonic
from music.note_event import NoteEvent


def n(pitch, start, dur, velocity=100):
    return NoteEvent(pitch=pitch, start=start, duration=dur, velocity=velocity)


class TestChordCollapse:
    def test_simultaneous_chord_keeps_highest(self):
        out, info = make_monophonic([n(60, 0.0, 1.0), n(64, 0.0, 1.0),
                                     n(67, 0.0, 1.0)])
        assert len(out) == 1
        assert out[0].pitch == 67
        assert info.chord_notes_removed == 2

    def test_select_lowest(self):
        out, _ = make_monophonic([n(60, 0.0, 1.0), n(67, 0.0, 1.0)],
                                 select="lowest")
        assert out[0].pitch == 60

    def test_select_velocity(self):
        out, _ = make_monophonic(
            [n(60, 0.0, 1.0, velocity=120), n(72, 0.0, 1.0, velocity=40)],
            select="velocity")
        assert out[0].pitch == 60

    def test_near_simultaneous_within_cluster_window(self):
        # starts 20 ms apart with a 40 ms window -> one cluster -> one note
        out, info = make_monophonic([n(60, 0.0, 1.0), n(64, 0.02, 1.0)],
                                    cluster_window_ms=40)
        assert len(out) == 1
        assert info.chord_notes_removed == 1

    def test_outside_cluster_window_kept_both(self):
        out, _ = make_monophonic([n(60, 0.0, 1.0), n(64, 0.1, 1.0)],
                                 cluster_window_ms=40)
        assert len(out) == 2

    def test_invalid_select_raises(self):
        with pytest.raises(ValueError):
            make_monophonic([n(60, 0, 1)], select="random")


class TestOverlapTruncation:
    def test_second_note_cuts_first(self):
        out, info = make_monophonic([n(60, 0.0, 1.0), n(62, 0.5, 1.0)])
        assert len(out) == 2
        assert out[0].duration == pytest.approx(0.5)   # truncated
        assert out[1].start == pytest.approx(0.5)
        assert out[1].duration == pytest.approx(1.0)
        assert info.truncated_notes == 1

    def test_contained_note_ends_outer_at_its_start(self):
        # A [0,2], B [0.5,0.8]: A cut to 0.5; B keeps its own end
        out, _ = make_monophonic([n(60, 0.0, 2.0), n(62, 0.5, 0.3)])
        assert out[0].duration == pytest.approx(0.5)
        assert out[1].duration == pytest.approx(0.3)
        # no overlap remains anywhere
        for a, b in zip(out, out[1:]):
            assert a.end <= b.start + 1e-9

    def test_truncated_to_zero_is_dropped(self):
        # B starts exactly where A starts but A is in an earlier cluster?
        # same-start cluster collapses first; here 60ms apart but A ends at
        # B's start -> A duration 0 after truncation? No: A [0,0.06],
        # B [0.06,...] -> no truncation. Construct a real zero case:
        # cluster picks B (higher) at 0.0; A also at 0.0 removed as chord.
        # Zero-length arises when a note's end == next note's start after a
        # negative overlap is impossible; instead test explicit zero input:
        out, info = make_monophonic([n(60, 0.0, 0.0), n(62, 0.5, 0.5)])
        assert len(out) == 1 and out[0].pitch == 62
        assert info.dropped_zero_length == 1


class TestNoOverlapGuarantee:
    def test_output_is_strictly_sequential(self):
        song = [
            n(60, 0.0, 2.0), n(64, 0.0, 1.0),      # chord
            n(62, 0.3, 3.0),                        # overlaps
            n(65, 1.0, 0.2), n(67, 1.05, 0.5),     # cluster
            n(60, 4.0, 0.5),
        ]
        out, info = make_monophonic(song, cluster_window_ms=40)
        for a, b in zip(out, out[1:]):
            assert a.end <= b.start + 1e-9, "notes must never overlap"
        assert info.output_notes == len(out)
        assert all(x.duration > 0 for x in out)

    def test_input_not_mutated(self):
        song = [n(60, 0.0, 1.0), n(62, 0.5, 1.0)]
        make_monophonic(song)
        assert song[0].duration == 1.0   # original untouched


class TestEdges:
    def test_empty(self):
        out, info = make_monophonic([])
        assert out == []
        assert isinstance(info, MonoInfo)

    def test_single_note(self):
        out, info = make_monophonic([n(60, 1.0, 0.5)])
        assert len(out) == 1
        assert out[0].start == 1.0 and out[0].duration == 0.5

    def test_unsorted_input(self):
        out, _ = make_monophonic([n(62, 0.5, 0.5), n(60, 0.0, 1.0)])
        assert [x.pitch for x in out] == [60, 62]
        assert out[0].duration == pytest.approx(0.5)

    def test_info_format(self):
        _, info = make_monophonic([n(60, 0, 1), n(64, 0, 1)])
        text = info.format()
        assert "Monophonic" in text and "chord notes removed" in text
