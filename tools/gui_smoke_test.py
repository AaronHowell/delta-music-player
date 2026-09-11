#!/usr/bin/env python
"""GUI smoke test: builds the window, loads samples/twinkle.mid, runs the
preview + event-build pipeline (NO real playback), then closes automatically.

Usage:  python tools/gui_smoke_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402

from gui_app import MidiPlayerApp  # noqa: E402
from playback.scheduler import build_events  # noqa: E402


def main() -> int:
    midi = ROOT / "samples" / "twinkle.mid"
    if not midi.exists():
        print(f"missing {midi} — run tools/make_demo_midi.py first")
        return 1

    root = tk.Tk()
    app = MidiPlayerApp(root)
    app.load_file(midi)
    assert app.notes, "parse produced no notes"
    print(f"parsed: {len(app.notes)} notes")

    app.do_preview()
    report, mapped, unmapped, mono_line = app.compute_mapped()
    assert mapped and not unmapped, "mapping failed"
    print(f"mapped: {len(mapped)} notes, transpose={report.semitones:+d}, "
          f"unmapped={len(unmapped)}")
    print(f"mono: {mono_line}")

    events, info = build_events(mapped, app.profile)
    assert events, "no input events built"
    print(f"events: {info.events} (shifted={info.shifted_notes}, "
          f"handoffs={info.handoffs})")

    # verify the key panel knows every identifier used by the events
    missing = {e.identifier for e in events} - set(app.key_labels)
    assert not missing, f"key panel missing widgets for: {missing}"
    print(f"key panel covers all {len(set(e.identifier for e in events))} "
          f"physical inputs")

    # ---- piano roll + region clipping ----
    app.piano.set_notes(app.notes, 24.0)
    app.update_ui = None  # not used; keep attribute namespace clean
    app.root.update_idletasks()          # force a real redraw of the canvas
    app.piano.sel = (5.0, 12.0)          # simulate a drag selection
    app._on_selection(app.piano.sel)
    clipped = app.current_notes()
    assert clipped, "clip produced no notes"
    assert clipped[0].start >= 0.0
    assert all(n.end <= 7.0 + 1e-9 for n in clipped)   # region is 7 s long
    print(f"selection 5.0-12.0s -> {len(clipped)} notes "
          f"(first at {clipped[0].start:.2f}s, rebased)")
    report2, mapped2, unmapped2, _ = app.compute_mapped()
    assert mapped2 and not unmapped2
    print(f"selection maps to {len(mapped2)} playable notes, "
          f"transpose={report2.semitones:+d}")
    app.piano.set_playhead(7.5)          # playhead inside the view
    app.root.update_idletasks()

    root.after(600, root.destroy)
    root.mainloop()
    print("GUI smoke test OK — window opened and closed cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
