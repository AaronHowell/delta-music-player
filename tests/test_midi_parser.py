"""Tests for MIDI -> NoteEvent parsing: tick/tempo math, note_off variants,
multi-track handling, auto melody selection."""
import mido
import pytest

from midi.midi_loader import is_midi, load_midi
from midi.midi_parser import (
    DEFAULT_TEMPO,
    build_tempo_map,
    choose_melody_track,
    parse_merged,
    parse_midi,
    parse_track,
    tick_to_seconds,
)
from music.note_event import notes_from_json, notes_to_json


def make_midi(path, tracks, ticks_per_beat=480, type=1):
    """tracks: list of lists of mido Message/MetaMessage (delta in .delta)."""
    mid = mido.MidiFile(type=type, ticks_per_beat=ticks_per_beat)
    mid.tracks.clear()
    for track_msgs in tracks:
        t = mido.MidiTrack()
        for m in track_msgs:
            t.append(m)
        mid.tracks.append(t)
    mid.save(str(path))
    return mido.MidiFile(str(path))


# NB: mido 1.3 messages carry their tick delta in the `time` attribute when
# saved to / read from a file (there is no `delta` kwarg).  Helpers keep the
# `delta=` name for readability but map it onto `time=`.
def tempo(t, delta=0):
    return mido.MetaMessage("set_tempo", tempo=t, time=delta)


def on(note, delta=0, velocity=100, channel=0):
    return mido.Message("note_on", note=note, velocity=velocity,
                        time=delta, channel=channel)


def off(note, delta=0, channel=0):
    return mido.Message("note_off", note=note, time=delta, channel=channel)


class TestTempoMath:
    def test_default_tempo_half_second_per_480_ticks(self, tmp_path):
        # 120 BPM, 480 tpq => one quarter note = 0.5 s
        mid = make_midi(tmp_path / "a.mid", [[tempo(DEFAULT_TEMPO),
                                               on(60), off(60, delta=480)]])
        result = parse_track(mid, 0)
        assert len(result.notes) == 1
        n = result.notes[0]
        assert n.pitch == 60
        assert n.start == pytest.approx(0.0)
        assert n.duration == pytest.approx(0.5)

    def test_tempo_change_mid_song(self, tmp_path):
        # tempo 500000 until tick 960, then 1000000 (60 BPM).
        # note 1: ticks 0-480   => 0.5 s
        # note 2: ticks 960-1440 => 1.0 s (slower tempo)
        track0 = [tempo(500_000), tempo(1_000_000, delta=960)]
        track1 = [on(60), off(60, delta=480),
                  on(62, delta=480), off(62, delta=480)]
        mid = make_midi(tmp_path / "b.mid", [track0, track1])
        result = parse_track(mid, 1)
        assert len(result.notes) == 2
        n1, n2 = result.notes
        assert n1.start == pytest.approx(0.0)
        assert n1.duration == pytest.approx(0.5)
        assert n2.start == pytest.approx(1.0)
        assert n2.duration == pytest.approx(1.0)

    def test_merged_matches_track_parse(self, tmp_path):
        track0 = [tempo(500_000), tempo(1_000_000, delta=960)]
        track1 = [on(60), off(60, delta=480),
                  on(62, delta=480), off(62, delta=480)]
        mid = make_midi(tmp_path / "c.mid", [track0, track1])
        merged = parse_merged(mid)
        per_track = parse_track(mid, 1)
        assert len(merged.notes) == len(per_track.notes) == 2
        for a, b in zip(merged.notes, per_track.notes):
            assert a.pitch == b.pitch
            assert a.start == pytest.approx(b.start, abs=1e-9)
            assert a.duration == pytest.approx(b.duration, abs=1e-9)

    def test_tick_to_seconds_piecewise(self, tmp_path):
        mid = make_midi(tmp_path / "d.mid",
                        [[tempo(500_000), tempo(250_000, delta=480),
                          on(60), off(60, delta=960)]])
        tmap = build_tempo_map(mid)
        assert tick_to_seconds(0, tmap) == pytest.approx(0.0)
        assert tick_to_seconds(480, tmap) == pytest.approx(0.5)
        # after tick 480 tempo is 250000 => 480 ticks = 0.25 s
        assert tick_to_seconds(960, tmap) == pytest.approx(0.75)


class TestNoteVariants:
    def test_note_on_velocity_zero_is_note_off(self, tmp_path):
        mid = make_midi(tmp_path / "e.mid",
                        [[tempo(DEFAULT_TEMPO),
                          on(64), on(64, delta=480, velocity=0)]])
        result = parse_track(mid, 0)
        assert len(result.notes) == 1
        assert result.notes[0].duration == pytest.approx(0.5)

    def test_retrigger_closes_previous(self, tmp_path):
        # same note pressed again before release: first closes at retrigger
        mid = make_midi(tmp_path / "f.mid",
                        [[tempo(DEFAULT_TEMPO),
                          on(60), on(60, delta=480), off(60, delta=480)]])
        result = parse_track(mid, 0)
        assert len(result.notes) == 2
        assert result.notes[0].duration == pytest.approx(0.5)
        assert result.notes[1].start == pytest.approx(0.5)
        assert result.notes[1].duration == pytest.approx(0.5)

    def test_unclosed_note_ends_at_track_end(self, tmp_path):
        mid = make_midi(tmp_path / "g.mid",
                        [[tempo(DEFAULT_TEMPO), on(60)]])  # no note_off
        result = parse_track(mid, 0)
        assert len(result.notes) == 0 or result.notes[0].duration > 0
        # with zero track length after the note_on, the note has no duration
        # and is dropped; add explicit length:
        mid = make_midi(tmp_path / "g2.mid",
                        [[tempo(DEFAULT_TEMPO), on(60),
                          mido.MetaMessage("end_of_track", time=960)]])
        result = parse_track(mid, 0)
        assert len(result.notes) == 1
        assert result.notes[0].duration == pytest.approx(1.0)

    def test_stray_note_off_ignored(self, tmp_path):
        mid = make_midi(tmp_path / "h.mid",
                        [[tempo(DEFAULT_TEMPO), off(60), on(62), off(62, delta=480)]])
        result = parse_track(mid, 0)
        assert [n.pitch for n in result.notes] == [62]

    def test_velocity_preserved(self, tmp_path):
        mid = make_midi(tmp_path / "i.mid",
                        [[tempo(DEFAULT_TEMPO),
                          on(60, velocity=42), off(60, delta=480)]])
        assert parse_track(mid, 0).notes[0].velocity == 42


class TestMultiTrack:
    def _song(self, tmp_path, name="m.mid"):
        # track 0: tempo only
        # track 1: low chordal accompaniment (polyphonic, low pitch)
        # track 2: high monophonic melody
        t0 = [tempo(DEFAULT_TEMPO)]
        t1 = [on(48), on(52, delta=0), on(55, delta=0),
              off(48, delta=960), off(52, delta=0), off(55, delta=0)]
        t2 = [on(72), off(72, delta=240),
              on(74, delta=0), off(74, delta=240),
              on(76, delta=0), off(76, delta=240),
              on(77, delta=0), off(77, delta=240)]
        return make_midi(tmp_path / name, [t0, t1, t2])

    def test_auto_select_melody_track(self, tmp_path):
        mid = self._song(tmp_path)
        result = choose_melody_track(mid)
        assert result.track_index == 2
        assert result.track_auto_selected
        assert all(n.pitch >= 72 for n in result.notes)
        assert len(result.notes) == 4

    def test_explicit_track(self, tmp_path):
        mid = self._song(tmp_path)
        result = parse_midi(mid, track=1)
        assert result.track_index == 1
        assert sorted(n.pitch for n in result.notes) == [48, 52, 55]

    def test_track_out_of_range(self, tmp_path):
        mid = self._song(tmp_path)
        with pytest.raises(ValueError):
            parse_track(mid, 99)

    def test_drum_track_excluded(self, tmp_path):
        t0 = [tempo(DEFAULT_TEMPO)]
        drums = [on(36, channel=9), off(36, delta=120, channel=9),
                 on(38, channel=9, delta=120), off(38, delta=120, channel=9),
                 on(36, channel=9, delta=120), off(36, delta=120, channel=9),
                 on(38, channel=9, delta=120), off(38, delta=120, channel=9)]
        melody = [on(76), off(76, delta=240),
                  on(77, delta=0), off(77, delta=240),
                  on(79, delta=0), off(79, delta=240),
                  on(81, delta=0), off(81, delta=240)]
        mid = make_midi(tmp_path / "drum.mid", [t0, drums, melody])
        result = choose_melody_track(mid)
        assert result.track_index == 2


class TestLoader:
    def test_is_midi_by_extension(self, tmp_path):
        p = tmp_path / "x.mid"
        p.write_bytes(b"MThd" + b"\x00" * 10)
        assert is_midi(p)

    def test_is_midi_by_header(self, tmp_path):
        p = tmp_path / "x.dat"
        p.write_bytes(b"MThd" + b"\x00" * 10)
        assert is_midi(p)

    def test_load_missing(self):
        with pytest.raises(FileNotFoundError):
            load_midi("no_such_file.mid")


class TestRoundTrip:
    def test_notes_json_roundtrip(self, tmp_path):
        mid = make_midi(tmp_path / "r.mid",
                        [[tempo(DEFAULT_TEMPO),
                          on(60), off(60, delta=480),
                          on(64, delta=0), off(64, delta=240)]])
        notes = parse_track(mid, 0).notes
        path = notes_to_json(notes, tmp_path / "out" / "r_notes.json")
        loaded = notes_from_json(path)
        assert len(loaded) == len(notes)
        for a, b in zip(notes, loaded):
            assert a.pitch == b.pitch
            assert a.start == pytest.approx(b.start, abs=1e-6)
            assert a.duration == pytest.approx(b.duration, abs=1e-6)
