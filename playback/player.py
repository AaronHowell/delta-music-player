"""Player: countdown, console control (Space=pause, Esc=stop), execution.

The precise timing itself lives in scheduler.execute(); this module wires it
to a real clock, a real input backend and the keyboard controls.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import List, Optional

from instrument.instrument_profile import InstrumentProfile
from instrument.mapper import MappedNote
from playback.input_backend import InputBackend
from playback.scheduler import (
    BuildInfo,
    InputEvent,
    PlaybackControl,
    TimingStats,
    build_events,
    execute,
)


class ConsoleControl(PlaybackControl):
    """Space = pause/resume, Esc = stop.  Polls the console via msvcrt in a
    daemon thread (Windows only; degrades to a no-op control elsewhere or
    when stdin is not a console)."""

    def __init__(self):
        self._paused = False
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        self._msvcrt = None
        if sys.platform == "win32":
            try:
                import msvcrt
                if sys.stdin.isatty():
                    self._msvcrt = msvcrt
            except (ImportError, ValueError):
                self._msvcrt = None

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if self._msvcrt is None or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._poll, name="dmp-console-control", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        t = self._thread
        if t is not None:
            t.join(timeout=0.5)
        self._thread = None

    def _poll(self) -> None:
        assert self._msvcrt is not None
        while not self._stop:
            try:
                if self._msvcrt.kbhit():
                    ch = self._msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):     # arrow-key prefix: drain
                        self._msvcrt.getwch()
                        continue
                    if ch == " ":
                        self._paused = not self._paused
                        print("\n[paused — Space to resume, Esc to stop]"
                              if self._paused else "\n[resumed]", flush=True)
                    elif ch == "\x1b":             # Esc
                        self._stop = True
                        print("\n[stopping...]", flush=True)
            except (OSError, ValueError):
                pass                               # console went away
            time.sleep(0.01)

    # -- PlaybackControl ----------------------------------------------
    def is_paused(self) -> bool:
        return self._paused

    def should_stop(self) -> bool:
        return self._stop


def countdown(seconds: float, control: Optional[PlaybackControl] = None) -> bool:
    """Print the countdown; returns False if cancelled (Esc) mid-count."""
    print("\nPlayback starts in:")
    t_end = time.perf_counter() + seconds
    n_left = int(round(seconds))
    print(f"  {n_left}", flush=True)
    while True:
        if control and control.should_stop():
            return False
        now = time.perf_counter()
        if now >= t_end:
            return True
        n = int(round(t_end - now))
        if 0 < n < n_left:
            n_left = n
            print(f"  {n}", flush=True)
        time.sleep(0.02)


class Player:
    def __init__(self, backend: InputBackend, settings: Optional[dict] = None):
        self.backend = backend
        s = (settings or {}).get("playback", {})
        self.min_key_hold_ms = float(s.get("min_key_hold_ms", 8))
        self.min_retrigger_gap_ms = float(s.get("min_retrigger_gap_ms", 15))
        self.batch_inter_event_ms = float(s.get("batch_inter_event_ms", 1))
        self.busy_wait_threshold_ms = float(s.get("busy_wait_threshold_ms", 3))
        self.max_sleep_chunk_ms = float(s.get("max_sleep_chunk_ms", 5))
        self.latency_compensation_ms = float(
            s.get("latency_compensation_ms", 0)
        )
        self.start_delay_sec = float(s.get("start_delay_sec", 5))

    # ------------------------------------------------------------------
    def build(self, mapped: List[MappedNote],
              profile: InstrumentProfile) -> tuple[List[InputEvent], BuildInfo]:
        return build_events(
            mapped,
            profile,
            min_key_hold_ms=self.min_key_hold_ms,
            min_retrigger_gap_ms=self.min_retrigger_gap_ms,
        )

    def play(
        self,
        mapped: List[MappedNote],
        profile: InstrumentProfile,
        start_delay: Optional[float] = None,
        control: Optional[PlaybackControl] = None,
        show_progress: bool = True,
        target_hwnd: Optional[int] = None,
        auto_foreground: bool = True,
    ) -> TimingStats:
        events, info = self.build(mapped, profile)
        print(f"\nInput events: {info.events} "
              f"(suppressed overlaps: {info.suppressed_overlaps}, "
              f"modifier hand-offs: {info.handoffs}, "
              f"retrigger-shifted notes: {info.shifted_notes}, "
              f"min-hold-extended notes: {info.extended_notes})")

        own_control = False
        if control is None:
            control = ConsoleControl()
            own_control = True
        if isinstance(control, ConsoleControl):
            control.start()

        delay = self.start_delay_sec if start_delay is None else start_delay
        try:
            if delay > 0 and not countdown(delay, control):
                print("Cancelled.")
                return TimingStats()

            if target_hwnd and auto_foreground:
                from playback.window_target import activate_window
                if activate_window(target_hwnd):
                    print("[window] game window brought to front")
                else:
                    print("[window] WARNING: could not activate the game "
                          "window — click it manually NOW")
            print("Playing...   (Space = pause, Esc = stop)\n")

            last_report = [0.0]
            total = events[-1].time if events else 0.0

            def progress(ev: InputEvent, _actual: float) -> None:
                if not show_progress or ev.action != "down" or ev.kind != "key":
                    return
                if ev.time - last_report[0] >= 2.0:
                    last_report[0] = ev.time
                    print(f"  ♪ {ev.time:7.1f}s / {total:.1f}s", flush=True)

            stats = execute(
                events,
                self.backend,
                control,
                busy_wait_threshold_ms=self.busy_wait_threshold_ms,
                max_sleep_chunk_ms=self.max_sleep_chunk_ms,
                batch_inter_event_ms=self.batch_inter_event_ms,
                latency_compensation_ms=self.latency_compensation_ms,
                progress_cb=progress,
            )
        finally:
            self.backend.close()      # never leave keys stuck (MeowField)
            if own_control and isinstance(control, ConsoleControl):
                control.stop()
        return stats
