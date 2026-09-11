"""Tests for time-region clipping."""
import pytest

from music.clip import clip_notes, parse_clip_spec
from music.note_event import NoteEvent


def n(pitch, start, dur):
    return NoteEvent(pitch=pitch, start=start, duration=dur)


class TestParseClipSpec:
    def test_range(self):
        assert parse_clip_spec("12.5-30") == (12.5, 30.0)

    def test_open_end(self):
        assert parse_clip_spec("12.5") == (12.5, None)

    def test_mmss(self):
        assert parse_clip_spec("1:05-1:30") == (65.0, 90.0)

    def test_spaces(self):
        assert parse_clip_spec(" 5 - 10 ") == (5.0, 10.0)


class TestClipNotes:
    SONG = [n(60, 0.0, 1.0), n(62, 1.0, 1.0), n(64, 2.0, 1.0),
            n(65, 3.0, 1.0)]

    def test_inside_only(self):
        out = clip_notes(self.SONG, 1.0, 3.0)
        assert [x.pitch for x in out] == [62, 64]

    def test_rebase_to_zero(self):
        out = clip_notes(self.SONG, 1.0, 3.0, rebase=True)
        assert out[0].start == pytest.approx(0.0)
        assert out[1].start == pytest.approx(1.0)

    def test_no_rebase_keeps_absolute(self):
        out = clip_notes(self.SONG, 1.0, 3.0, rebase=False)
        assert out[0].start == pytest.approx(1.0)

    def test_partial_overlap_trimmed(self):
        out = clip_notes(self.SONG, 0.5, 2.5, rebase=False)
        assert out[0].start == pytest.approx(0.5)   # trimmed at region start
        assert out[0].duration == pytest.approx(0.5)
        assert out[-1].end == pytest.approx(2.5)    # trimmed at region end
        assert out[-1].duration == pytest.approx(0.5)

    def test_open_end(self):
        out = clip_notes(self.SONG, 2.5)
        assert [x.pitch for x in out] == [64, 65]
        assert out[0].duration == pytest.approx(0.5)

    def test_zero_length_dropped(self):
        # region touches a note boundary exactly -> no zero-length artifacts
        out = clip_notes(self.SONG, 1.0, 2.0, rebase=False)
        assert [x.pitch for x in out] == [62]
        assert all(x.duration > 0 for x in out)

    def test_empty_region_between_notes(self):
        song = [n(60, 0.0, 0.5), n(62, 2.0, 0.5)]
        assert clip_notes(song, 0.6, 1.9) == []

    def test_empty_input(self):
        assert clip_notes([], 0.0, 1.0) == []

    def test_invalid_region(self):
        with pytest.raises(ValueError):
            clip_notes(self.SONG, 5.0, 5.0)
        with pytest.raises(ValueError):
            clip_notes(self.SONG, -1.0)

    def test_input_not_mutated(self):
        clip_notes(self.SONG, 0.5, 2.5)
        assert self.SONG[0].start == 0.0 and self.SONG[0].duration == 1.0
