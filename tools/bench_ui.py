# -*- coding: utf-8 -*-
"""Benchmark the GUI hot paths: piano-roll redraw and the event->UI path.

    .venv\\Scripts\\python.exe tools\\bench_ui.py

Reports per-call milliseconds for:
  * PianoRoll repaint at several note counts   (the 卷帘 drag path)
  * PianoRoll.set_playhead()                   (once per playback frame)
  * MidiPlayerApp event handling               (lights + transport)
"""
from __future__ import annotations

import random
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from music.note_event import NoteEvent          # noqa: E402
import gui_app                                   # noqa: E402


def make_notes(n: int, span: float = 200.0) -> list[NoteEvent]:
    random.seed(7)
    p = 60
    out = []
    for i in range(n):
        p = max(48, min(84, p + random.choice((-2, -1, 0, 0, 1, 2))))
        s = span * i / n
        out.append(NoteEvent(pitch=p, start=s, duration=random.uniform(0.1, 0.6),
                             velocity=90, confidence=1.0))
    return out


def bench(fn, repeat: int = 5) -> float:
    fn()                                  # warm up
    t0 = time.perf_counter()
    for _ in range(repeat):
        fn()
    return (time.perf_counter() - t0) / repeat * 1000.0


def main() -> int:
    root = tk.Tk()
    root.geometry("900x300")
    roll = gui_app.PianoRoll(root, height=170)
    roll.pack(fill="x")
    root.update()

    def repaint():
        roll._draw()
        root.update_idletasks()

    print(f"{'notes':>7}  {'items':>7}  {'repaint':>9}  {'playhead':>9}")
    for n in (200, 1000, 3000, 8000):
        notes = make_notes(n)
        roll.set_notes(notes, 200.0)
        roll._draw()
        items = len(roll.find_all())
        ms = bench(repaint, repeat=3)
        head = bench(lambda: roll.set_playhead(100.0), repeat=200)
        print(f"{n:>7}  {items:>7}  {ms:>7.1f}ms  {head:>7.3f}ms")

    # the interactions that dominate real use
    notes = make_notes(8000)
    roll.set_notes(notes, 200.0)
    roll._draw()
    root.update_idletasks()
    print("\n8000-note song, whole-song view:")
    roll._drag = ("select", 400, 40.0)          # mid rubber-band
    roll.sel = (40.0, 60.0)
    print(f"  selection drag frame : {bench(repaint, repeat=3):7.1f} ms")
    roll._drag = None
    print(f"  selection on release : {bench(repaint, repeat=3):7.1f} ms")
    roll.view_start, roll.view_end = 40.0, 45.0
    roll.sel = None
    print(f"  zoomed to 5s         : {bench(repaint, repeat=3):7.1f} ms")
    roll.view_start, roll.view_end = 0.0, 204.0
    print(f"  zoomed back out      : {bench(repaint, repeat=3):7.1f} ms")

    # --- event -> UI path -------------------------------------------------
    app = gui_app.MidiPlayerApp(root)
    root.update()

    class Combo:
        def display(self):
            return "Z"

    class Mapped:
        def __init__(self, i):
            self.note = NoteEvent(pitch=60 + i % 12, start=i * 0.05,
                                  duration=0.2, velocity=90, confidence=1.0)
            self.combo = Combo()

    from playback.scheduler import InputEvent
    app.mapped = [Mapped(i) for i in range(3000)]
    app.notes = [m.note for m in app.mapped]
    app.piano.set_notes(app.notes, 200.0)
    app.events_total_time = 200.0
    root.update()

    down = InputEvent(time=10.0, action="down", identifier="z", kind="key",
                      note_index=5)
    up = InputEvent(time=10.2, action="up", identifier="z", kind="key",
                    note_index=5)

    def poll_with(n):
        for i in range(n):
            app.q.put(("event", down if i % 2 else up))
        t0 = time.perf_counter()
        app._poll_queue()
        return (time.perf_counter() - t0) * 1000

    print("\nevent -> UI (lights + playhead + progress):")
    for n in (1, 10, 50, 200):
        print(f"  poll tick with {n:4d} queued events: {poll_with(n):7.1f} ms")
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
