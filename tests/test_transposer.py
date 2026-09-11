"""Tests for global transposition + octave folding."""
import pytest

from music.note_event import NoteEvent
from music.transposer import (
    fold_into_range,
    find_best_transpose,
    nearest_playable,
    transpose_notes,
)


def notes(pitches, start_step=0.5):
    return [
        NoteEvent(pitch=p, start=i * start_step, duration=start_step)
        for i, p in enumerate(pitches)
    ]


CHROMATIC = list(range(48, 86))        # C3..C#6, like the default profile
WHITE_ONLY = [p for p in CHROMATIC if p % 12 in (0, 2, 4, 5, 7, 9, 11)]


class TestFolding:
    def test_fold_down(self):
        assert fold_into_range(96, 48, 85) == 84    # one octave down lands in range
        assert fold_into_range(100, 48, 85) == 76   # two octaves down

    def test_fold_up(self):
        assert fold_into_range(40, 48, 85) == 52
        assert fold_into_range(30, 48, 85) == 54

    def test_in_range_unchanged(self):
        assert fold_into_range(60, 48, 85) == 60

    def test_narrow_range_clamps(self):
        # range < 12 semitones: folding cannot converge, clamp instead
        assert fold_into_range(90, 60, 64) == 64
        assert fold_into_range(30, 60, 64) == 60


class TestNearestPlayable:
    def test_snap(self):
        assert nearest_playable(61, WHITE_ONLY)[0] == 60  # tie -> lower
        assert nearest_playable(63, WHITE_ONLY)[0] == 62
        assert nearest_playable(66, WHITE_ONLY)[0] in (65, 67)
        assert nearest_playable(66, WHITE_ONLY)[1] == 1


class TestBestTranspose:
    def test_already_in_range(self):
        t = find_best_transpose(notes([60, 62, 64]), CHROMATIC)
        assert t == 0

    def test_transpose_down_for_high_song(self):
        # song sits at C6..E6 (84..88): best is -3 so 81..85 fits (85 = top)
        song = notes([84, 85, 86, 87, 88])
        t = find_best_transpose(song, CHROMATIC)
        shifted = [p + t for p in (84, 85, 86, 87, 88)]
        assert all(48 <= p <= 85 for p in shifted)

    def test_octave_equivalent_prefers_zero(self):
        # song at 72..76 fits directly; +12/-12 would also 'work' via folding
        # but |t| minimal wins
        t = find_best_transpose(notes([72, 73, 74, 75, 76]), CHROMATIC)
        assert t == 0

    def test_prefers_smaller_t_on_tie(self):
        # pitches centered so that -1 and +1 give identical direct counts
        song = notes([60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72,
                      73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 84,
                      83, 82, 81])
        t = find_best_transpose(song, CHROMATIC)
        assert t == 0  # everything already fits

    def test_maximizes_direct_over_minimal_shift(self):
        # song mostly too low: 40..47 (below 48) plus one in-range note.
        # t=+8 puts all into 48..55 directly (8 notes); t=+1 only helps one.
        song = notes([40, 41, 42, 43, 44, 45, 46, 47])
        t = find_best_transpose(song, CHROMATIC)
        assert t == 8


class TestTransposeNotes:
    def test_report_fields(self):
        song = notes([60, 62, 96, 30])
        out, rep = transpose_notes(song, CHROMATIC, semitones="none")
        assert rep.total == 4
        assert rep.direct == 2                # 60, 62
        assert rep.folded == 2                # 96 -> 84, 30 -> 54
        assert rep.dropped == 0
        assert len(out) == 4
        assert {n.pitch for n in out} == {60, 62, 84, 54}
        assert rep.original_range == (30, 96)
        assert rep.instrument_range == (48, 85)

    def test_explicit_semitones(self):
        song = notes([60, 61])
        out, rep = transpose_notes(song, CHROMATIC, semitones=-12)
        assert rep.semitones == -12
        assert [n.pitch for n in out] == [48, 49]

    def test_timing_preserved(self):
        song = notes([60, 62, 64])
        out, _ = transpose_notes(song, CHROMATIC, semitones=2)
        for a, b in zip(song, out):
            assert a.start == b.start
            assert a.duration == b.duration
            assert a.velocity == b.velocity

    def test_dropped_when_snap_disabled(self):
        # black-key note on a white-key instrument, snapping off -> dropped
        song = notes([61])
        out, rep = transpose_notes(
            song, WHITE_ONLY, semitones="none",
            allow_folding=False, allow_snap=False,
        )
        assert out == []
        assert rep.dropped == 1
        assert rep.dropped_notes[0].pitch == 61

    def test_snapped_when_enabled(self):
        song = notes([61])
        out, rep = transpose_notes(
            song, WHITE_ONLY, semitones="none", allow_snap=True, max_snap=1
        )
        assert len(out) == 1
        assert out[0].pitch in (60, 62)
        assert rep.snapped == 1

    def test_snap_distance_limit(self):
        # nearest white key is 1 away; with max_snap=0 it must drop
        song = notes([61])
        out, rep = transpose_notes(
            song, WHITE_ONLY, semitones="none", allow_snap=True, max_snap=0
        )
        assert out == [] and rep.dropped == 1

    def test_auto_end_to_end(self):
        # song reaches 1 semitone past the top (86 > 85): the minimal shift
        # that makes everything directly playable is -1 (spec: same direct
        # count -> smallest |t| wins, NOT the "musically nice" -7)
        song = notes([p + 7 for p in (72, 74, 76, 77, 79)])
        out, rep = transpose_notes(song, CHROMATIC, semitones="auto")
        assert rep.semitones == -1
        assert rep.direct == 5
        assert [n.pitch for n in out] == [78, 80, 82, 83, 85]

    def test_report_format_lines(self):
        song = notes([60, 96])
        _, rep = transpose_notes(song, CHROMATIC, semitones="none")
        text = rep.format()
        assert "Original range:" in text
        assert "Instrument range:" in text
        assert "Global transpose:" in text
        assert "Playable notes:" in text
        assert "Dropped:" in text
