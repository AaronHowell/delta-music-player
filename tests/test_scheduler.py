"""Tests for the event scheduler: build-stage event ordering/timing rules and
the absolute-timeline execution loop (fake clock => deterministic)."""
import pytest

from instrument.instrument_profile import InstrumentProfile
from instrument.mapper import InstrumentMapper, MappedNote
from music.note_event import NoteEvent
from playback.input_backend import DryRunBackend
from playback.scheduler import (
    PlaybackControl,
    TimingStats,
    build_events,
    execute,
)

CONFIG = {
    "name": "test",
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
    return InstrumentProfile.from_dict(CONFIG)


def mapped(profile, pitch, start, dur):
    combo = profile.combination_for(pitch)
    assert combo is not None, f"pitch {pitch} not playable in test config"
    return MappedNote(NoteEvent(pitch=pitch, start=start, duration=dur), combo)


def simple_view(events):
    """(time, action, identifier) triples for readable assertions."""
    return [(round(e.time, 6), e.action, e.identifier) for e in events]


# ==========================================================================
# build stage
# ==========================================================================

class TestBuildEvents:
    def test_single_plain_note(self, profile):
        events, info = build_events([mapped(profile, 60, 0.0, 0.5)], profile)
        assert simple_view(events) == [
            (0.0, "down", "z"),
            (0.5, "up", "z"),
        ]
        assert info.events == 2

    def test_modifier_press_order_and_release_order(self, profile):
        # C#4 = sharp + z: modifier down BEFORE key; key up BEFORE modifier
        events, _ = build_events([mapped(profile, 61, 0.0, 0.5)], profile)
        assert simple_view(events) == [
            (0.0, "down", "mouse_middle"),
            (0.0, "down", "z"),
            (0.5, "up", "z"),
            (0.5, "up", "mouse_middle"),
        ]

    def test_two_modifiers(self, profile):
        # C#3 (49) = lower + sharp + z
        events, _ = build_events([mapped(profile, 49, 0.0, 0.4)], profile)
        view = simple_view(events)
        assert view[0] == (0.0, "down", "mouse_left")
        assert view[1] == (0.0, "down", "mouse_middle")
        assert view[2] == (0.0, "down", "z")
        assert view[3] == (0.4, "up", "z")
        assert view[4] == (0.4, "up", "mouse_middle")
        assert view[5] == (0.4, "up", "mouse_left")

    def test_repeated_same_key_needs_retrigger_gap(self, profile):
        # C4 0-0.5 then C4 0.5-1.0: up, gap, down — never one long note
        events, info = build_events(
            [mapped(profile, 60, 0.0, 0.5), mapped(profile, 60, 0.5, 0.5)],
            profile,
            min_retrigger_gap_ms=15,
        )
        assert simple_view(events) == [
            (0.0, "down", "z"),
            (0.5, "up", "z"),
            (0.515, "down", "z"),     # shifted by the retrigger gap
            (1.0, "up", "z"),
        ]
        assert info.shifted_notes == 1

    def test_different_keys_no_shift(self, profile):
        events, info = build_events(
            [mapped(profile, 60, 0.0, 0.5), mapped(profile, 62, 0.5, 0.5)],
            profile,
        )
        assert simple_view(events) == [
            (0.0, "down", "z"),
            (0.5, "up", "z"),          # ups before downs at same instant
            (0.5, "down", "x"),
            (1.0, "up", "x"),
        ]
        assert info.shifted_notes == 0

    def test_legato_modifier_handoff(self, profile):
        # C#4 (sharp+z) 0-0.5 then D#4 (sharp+x) 0.5-1.0:
        # mouse_middle stays down across the boundary; x is NOT shifted
        events, info = build_events(
            [mapped(profile, 61, 0.0, 0.5), mapped(profile, 63, 0.5, 0.5)],
            profile,
        )
        mm = [(t, a) for t, a, i in simple_view(events) if i == "mouse_middle"]
        assert mm == [(0.0, "down"), (1.0, "up")]
        assert info.handoffs == 1
        assert info.shifted_notes == 0
        assert (0.5, "down", "x") in simple_view(events)

    def test_repeated_combo_keeps_modifier_but_retriggers_key(self, profile):
        # C#4 twice: sharp stays held, z physically retriggers after the gap
        events, info = build_events(
            [mapped(profile, 61, 0.0, 0.5), mapped(profile, 61, 0.5, 0.5)],
            profile,
        )
        view = simple_view(events)
        assert (0.0, "down", "mouse_middle") in view
        assert (1.0, "up", "mouse_middle") in view
        assert sum(1 for t, a, i in view if i == "mouse_middle") == 2
        assert (0.5, "up", "z") in view
        assert (0.515, "down", "z") in view
        assert info.handoffs == 1 and info.shifted_notes == 1

    def test_overlapping_same_key_merges_hold(self, profile):
        # z 0-1 and z 0.5-1.5: one physical hold 0 -> 1.5
        events, info = build_events(
            [mapped(profile, 60, 0.0, 1.0), mapped(profile, 60, 0.5, 1.0)],
            profile,
        )
        assert simple_view(events) == [
            (0.0, "down", "z"),
            (1.5, "up", "z"),
        ]
        assert info.suppressed_overlaps >= 2

    def test_overlapping_notes_share_modifier(self, profile):
        # sharp+z 0-1 and sharp+x 0.5-1.5: modifier down once, up at 1.5
        events, _ = build_events(
            [mapped(profile, 61, 0.0, 1.0), mapped(profile, 63, 0.5, 1.0)],
            profile,
        )
        mm = [(t, a) for t, a, i in simple_view(events) if i == "mouse_middle"]
        assert mm == [(0.0, "down"), (1.5, "up")]

    def test_min_key_hold(self, profile):
        events, info = build_events(
            [mapped(profile, 60, 0.0, 0.003)], profile, min_key_hold_ms=8
        )
        assert simple_view(events) == [
            (0.0, "down", "z"),
            (0.008, "up", "z"),
        ]
        assert info.extended_notes == 1

    def test_unsorted_input_is_ordered(self, profile):
        events, _ = build_events(
            [mapped(profile, 62, 0.5, 0.5), mapped(profile, 60, 0.0, 0.5)],
            profile,
        )
        assert [e.note_index for e in events if e.action == "down"] == [1, 0]


# ==========================================================================
# execution stage (fake clock => deterministic)
# ==========================================================================

class FakeClock:
    """Every read advances time slightly (so busy-wait loops terminate);
    sleep advances it exactly."""

    def __init__(self, read_step=0.0001):
        self.t = 0.0
        self.read_step = read_step

    def __call__(self):
        self.t += self.read_step
        return self.t


class FakeSleep:
    def __init__(self, clock, on_sleep=None):
        self.clock = clock
        self.calls = []
        self.on_sleep = on_sleep

    def __call__(self, dt):
        self.calls.append(dt)
        self.clock.t += dt
        if self.on_sleep:
            self.on_sleep(dt)


class ScriptedControl(PlaybackControl):
    def __init__(self):
        self.paused = False
        self.stopped = False
        self.fires = 0
        self.pause_after_fires = None
        self.stop_after_fires = None

    def on_fire(self):
        self.fires += 1
        if self.pause_after_fires == self.fires:
            self.paused = True
        if self.stop_after_fires == self.fires:
            self.stopped = True

    def is_paused(self):
        return self.paused

    def should_stop(self):
        return self.stopped


class RecordingBackend:
    def __init__(self):
        self.events = []   # (action, identifier) in fire order

    def down(self, identifier):
        self.events.append(("down", identifier))

    def up(self, identifier):
        self.events.append(("up", identifier))


def run(events, *, control=None, latency_ms=0.0, batch_gap_ms=1.0,
        clock=None):
    clock = clock or FakeClock()
    sleep = FakeSleep(clock, on_sleep=None)
    backend = RecordingBackend()
    fired = []

    def progress(ev, actual):
        fired.append((ev, actual))
        if control is not None and hasattr(control, "on_fire"):
            control.on_fire()

    stats = execute(
        events, backend, control,
        clock=clock, sleep=sleep,
        latency_compensation_ms=latency_ms,
        batch_inter_event_ms=batch_gap_ms,
        progress_cb=progress,
    )
    return stats, backend, fired, clock, sleep


class TestExecute:
    def test_absolute_timeline_no_drift(self, profile):
        # 10 notes 0.1 s apart: each event must land near its own target,
        # with errors NOT growing along the song (no accumulation)
        notes = [mapped(profile, 60 + i, i * 0.1, 0.08) for i in range(10)]
        events, _ = build_events(notes, profile)
        origin_margin = 0.0
        stats, backend, fired, clock, _ = run(events)
        assert stats.fired == len(events)
        errors = stats.errors_ms
        assert all(0 <= e < 3.0 for e in errors), errors
        # drift check: last event's error is not systematically larger
        assert errors[-1] - errors[0] < 1.0

    def test_event_order_matches_schedule(self, profile):
        events, _ = build_events(
            [mapped(profile, 61, 0.0, 0.3), mapped(profile, 60, 0.3, 0.3)],
            profile,
        )
        _, backend, _, _, _ = run(events)
        assert backend.events == [
            ("down", "mouse_middle"), ("down", "z"),
            ("up", "z"), ("up", "mouse_middle"),
            ("down", "z"), ("up", "z"),
        ]

    def test_batch_members_1ms_apart(self, profile):
        events, _ = build_events([mapped(profile, 61, 0.0, 0.3)], profile)
        _, backend, fired, _, sleep = run(events, batch_gap_ms=1.0)
        # mouse_middle down and z down fire in the same batch
        t_mm = next(a for (e, a) in fired if e.identifier == "mouse_middle")
        t_z = next(a for (e, a) in fired
                   if e.identifier == "z" and e.action == "down")
        assert t_z - t_mm == pytest.approx(0.001, abs=0.0006)
        assert 0.001 in [round(c, 6) for c in sleep.calls]

    def test_long_note_held_for_duration(self, profile):
        events, _ = build_events([mapped(profile, 60, 0.0, 2.0)], profile)
        _, backend, fired, _, _ = run(events)
        t_down = next(a for (e, a) in fired if e.action == "down")
        t_up = next(a for (e, a) in fired if e.action == "up")
        assert t_up - t_down == pytest.approx(2.0, abs=0.004)

    def test_latency_compensation_fires_earlier(self, profile):
        events, _ = build_events([mapped(profile, 60, 1.0, 0.5)], profile)
        _, _, fired, _, _ = run(events, latency_ms=50)
        t_down = next(a for (e, a) in fired if e.action == "down")
        # origin is ~0 in fake clock: fires near 1.0 - 0.05
        assert t_down == pytest.approx(0.95, abs=0.004)

    def test_pause_releases_keys_and_shifts_timeline(self, profile):
        events, _ = build_events(
            [mapped(profile, 60, 0.0, 1.0), mapped(profile, 62, 1.5, 0.5)],
            profile,
        )
        control = ScriptedControl()
        control.pause_after_fires = 1      # pause right after z down

        clock = FakeClock()
        sleep_calls = {"pause_sleeps": 0}

        def on_sleep(dt):
            if control.paused:
                sleep_calls["pause_sleeps"] += 1
                if sleep_calls["pause_sleeps"] >= 30:   # ~0.3 s paused
                    control.paused = False

        sleep = FakeSleep(clock, on_sleep=on_sleep)
        backend = RecordingBackend()
        fired = []

        def progress(ev, actual):
            fired.append((ev, actual))
            control.on_fire()

        stats = execute(events, backend, control, clock=clock, sleep=sleep,
                        progress_cb=progress)

        # z was released when pausing
        assert ("up", "z") in backend.events
        # the x note still fires 1.5 s after the FIRST note on the song
        # timeline, i.e. the pause duration was added to its wall-clock time
        t_z_down = next(a for (e, a) in fired
                        if e.identifier == "z" and e.action == "down")
        t_x_down = next(a for (e, a) in fired
                        if e.identifier == "x" and e.action == "down")
        paused = stats.paused_seconds
        assert paused >= 0.25
        assert (t_x_down - t_z_down) == pytest.approx(1.5 + paused, abs=0.01)
        assert stats.fired == len(events)   # pause-release bypasses fire()

    def test_stop_releases_held_keys(self, profile):
        events, _ = build_events(
            [mapped(profile, 60, 0.0, 1.0), mapped(profile, 62, 1.5, 0.5)],
            profile,
        )
        control = ScriptedControl()
        control.stop_after_fires = 1        # stop right after z down
        stats, backend, fired, _, _ = run(events, control=control)
        assert stats.stopped_early
        assert backend.events[-1] == ("up", "z")   # held key released
        assert len(fired) == 1                      # nothing else played

    def test_clean_run_leaves_nothing_held(self, profile):
        events, _ = build_events(
            [mapped(profile, 61, 0.0, 0.3), mapped(profile, 63, 0.4, 0.3)],
            profile,
        )
        _, backend, _, _, _ = run(events)
        downs = sum(1 for a, _ in backend.events if a == "down")
        ups = sum(1 for a, _ in backend.events if a == "up")
        assert downs == ups

    def test_empty_event_list(self):
        stats, backend, fired, _, _ = run([])
        assert stats.fired == 0
        assert backend.events == []


class TestTimingStats:
    def test_percentiles(self):
        s = TimingStats()
        for e in [1.0] * 90 + [10.0] * 10:
            s.record(e / 1000.0)
        assert s.mean_ms == pytest.approx(1.9)
        assert s.p95_ms == pytest.approx(10.0)
        assert s.max_ms == pytest.approx(10.0)

    def test_format_contents(self):
        s = TimingStats()
        s.record(0.001)
        text = s.format()
        assert "mean=" in text and "P95=" in text and "max=" in text

    def test_empty(self):
        s = TimingStats()
        assert s.mean_ms == 0.0 and s.p95_ms == 0.0 and s.max_ms == 0.0


class TestDryRunIntegration:
    def test_dry_backend_through_execute(self, profile):
        events, _ = build_events([mapped(profile, 60, 0.0, 0.2)], profile)
        backend = DryRunBackend(verbose=False)
        clock = FakeClock()
        stats = execute(events, backend, clock=clock, sleep=FakeSleep(clock))
        assert stats.fired == 2
        assert [e[1:] for e in backend.events] == [("down", "z"), ("up", "z")]
