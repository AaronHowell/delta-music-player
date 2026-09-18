#!/usr/bin/env python
"""Simple GUI: pick a MIDI -> visualize -> select a region -> countdown ->
auto performance, with a live key panel and piano-roll playhead.

Pipeline is identical to the CLI (app.py):
    parse -> [clip selection] -> transpose -> monophonic -> map
          -> scheduler.execute(Win32 SendInput)

Piano roll controls:
    left drag          select a time region (click = clear selection)
    mouse wheel        zoom time axis (at cursor)
    shift+drag / middle drag   pan
    double click       fit whole song

Playback runs in a worker thread; all Tk updates flow through a queue.
Tkinter is stdlib — no new dependencies.

Performance notes (a redraw is on the hot path of every mouse move):
  * canvas items live in per-layer _ItemPool objects and are repositioned,
    never deleted and recreated;
  * the pools write to Tk only when a value actually changed, because Tk
    repaints an item on *any* coords()/itemconfigure() call (~25 us each),
    even a no-op one;
  * repaints are coalesced per idle cycle, so a burst of motion events costs
    one redraw;
  * a whole-song view of a big MIDI aggregates notes per pixel column
    (NOTE_BUDGET) instead of drawing one item per note;
  * a rubber-band drag moves two dashed edges and nothing else;
  * playback events collapse to one playhead/progress update per tick.

Usage:  python gui_app.py
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

from instrument.instrument_profile import InstrumentProfile
from instrument.mapper import InstrumentMapper
from midi.midi_loader import is_midi, load_midi
from midi.midi_parser import build_tempo_map, parse_midi, _is_drum_track
from midi.midi_writer import write_midi
from music.clip import clip_notes
from music.note_event import summarize
from music.pitch_utils import midi_to_name
from music.transposer import transpose_notes
from playback.audition import AuditionError, MidiAuditioner
from playback.input_backend import Win32SendInputBackend
from playback.scheduler import PlaybackControl, build_events, execute
from playback.window_target import (
    WindowInfo,
    activate_window,
    find_game_window,
    is_admin,
    list_top_windows,
    relaunch_as_admin,
    window_under_cursor,
)

MOD_CN = {"lower": "降调", "sharp": "半音", "upper": "升调"}
LIT_BG = "#ffb347"          # key-panel lamp while an input is held
MOUSE_CN = {"mouse_left": "鼠标左键", "mouse_right": "鼠标右键",
            "mouse_middle": "鼠标中键"}


# ==========================================================================
# piano roll
# ==========================================================================

class _ItemPool:
    """Reusable canvas items for one redraw layer.

    A redraw takes the items it needs, repositions them and hides the
    leftover tail — instead of ``delete("all")`` + recreate.

    It also remembers each item's geometry and options and only writes to Tk
    when something actually changed.  That matters more than the recycling:
    Tk marks an item for repaint on *any* ``coords()``/``itemconfigure()``
    call, even one that writes back the value it already had — measured at
    ~25 us of repaint per item, so a redraw that changed nothing used to cost
    a full canvas repaint (~26 ms for 900 items) and now costs nothing.
    """

    __slots__ = ("_canvas", "_make", "items", "geo", "opts", "used", "shown",
                 "grew")

    def __init__(self, canvas: tk.Canvas, make):
        self._canvas = canvas
        self._make = make
        self.items: list[int] = []
        self.geo: list = []    # last coords written per item (None = never)
        self.opts: list = []   # last options written per item
        self.used = 0          # items taken in the current redraw
        self.shown = 0         # items visible after the previous redraw
        self.grew = False      # created items this redraw (stacking changed)

    def begin(self) -> None:
        self.used = 0

    def precreate(self, n: int = 1) -> None:
        """Create and hide n items now, so they stack *below* anything the
        canvas creates later (items paint in creation order)."""
        canvas = self._canvas
        while len(self.items) < n:
            item = self._make()
            canvas.itemconfigure(item, state="hidden")
            self.items.append(item)
            self.geo.append(None)
            self.opts.append({})

    def place(self, *xy):
        """Take the next item and give it these coords -> its frame handle."""
        i = self.used
        self.used = i + 1
        items, geo = self.items, self.geo
        if i < len(items):
            if i >= self.shown:                # was hidden: bring it back
                self._canvas.itemconfigure(items[i], state="normal")
        else:
            items.append(self._make())
            geo.append(None)
            self.opts.append({})
            self.grew = True
        if geo[i] != xy:
            self._canvas.coords(items[i], *xy)
            geo[i] = xy
        return i

    def style(self, i: int, **opts) -> None:
        """Set item options, skipping the ones that already hold that value."""
        cur = self.opts[i]
        changed = {k: v for k, v in opts.items() if cur.get(k) != v}
        if changed:
            cur.update(changed)
            self._canvas.itemconfigure(self.items[i], **changed)

    def commit(self) -> None:
        if self.used < self.shown:
            for item in self.items[self.used:self.shown]:
                self._canvas.itemconfigure(item, state="hidden")
            self.geo[self.used:self.shown] = [None] * (self.shown - self.used)
        self.shown = self.used


class PianoRoll(tk.Canvas):
    """Time (x) x pitch (y) note view with rubber-band region selection."""

    MARGIN_L = 42
    MARGIN_T = 18
    BG = "#20222e"
    GRID = "#343750"
    BAND = "#262939"
    TEXT = "#aab0c8"
    NOTE = "#5b9bd5"
    NOTE_SEL = "#f0a35e"
    SEL_FILL = "#555a78"
    PLAYHEAD = "#ff5566"

    #: above this many visible notes the roll stops drawing one item per note
    #: and aggregates per pixel column (see _draw_notes_dense)
    NOTE_BUDGET = 1500

    def __init__(self, master, on_select=None, height=170):
        super().__init__(master, height=height, bg=self.BG,
                         highlightthickness=0)
        self.notes = []
        self.duration = 1.0
        self.view_start = 0.0
        self.view_end = 1.0
        self.sel = None                    # (start_s, end_s) or None
        self.playhead_t = None
        self.on_select = on_select
        self._drag = None                  # (mode, x0, data)
        self._pitch_lo, self._pitch_hi = 48, 84
        self._redraw_id = None             # pending after_idle repaint
        self._head = None                  # persistent playhead line item
        self._head_geo = None
        self._head_shown = False
        self._head_top = False

        # one item pool per layer: a redraw repositions instead of rebuilding
        self._bands = _ItemPool(self, lambda: self.create_rectangle(
            0, 0, 0, 0, fill=self.BAND, outline=""))
        self._grid = _ItemPool(self, lambda: self.create_line(
            0, 0, 0, 0, fill=self.GRID))
        self._labels = _ItemPool(self, lambda: self.create_text(
            0, 0, anchor="w", fill=self.TEXT, font=("", 7), text=""))
        self._rects = _ItemPool(self, lambda: self.create_rectangle(
            0, 0, 0, 0, fill=self.NOTE, outline="", width=0))
        self._rects_sel = _ItemPool(self, lambda: self.create_rectangle(
            0, 0, 0, 0, fill=self.NOTE_SEL, outline="", width=0))

        # precreated so the notes always paint over the selection band
        self._sel_rect = _ItemPool(self, lambda: self.create_rectangle(
            0, 0, 0, 0, fill=self.SEL_FILL, stipple="gray25", outline="",
            width=0))
        self._sel_edges = _ItemPool(self, lambda: self.create_line(
            0, 0, 0, 0, fill=self.NOTE_SEL, dash=(3, 2)))
        self._sel_rect.precreate(1)
        self._sel_edges.precreate(2)

        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Shift-ButtonPress-1>", lambda e: self._press(e, "pan"))
        self.bind("<ButtonPress-2>", lambda e: self._press(e, "pan"))
        self.bind("<B2-Motion>", self._motion)
        self.bind("<ButtonRelease-2>", self._release)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Double-Button-1>", lambda e: self.fit_view())
        self.bind("<Configure>", lambda e: self.redraw())

    # ---------------- data ----------------
    def set_notes(self, notes, duration):
        self.notes = list(notes)
        self.duration = max(float(duration), 0.1)
        self.sel = None
        self.playhead_t = None
        if self.notes:
            ps = [n.pitch for n in self.notes]
            self._pitch_lo = min(ps) - 1
            self._pitch_hi = max(ps) + 1
        else:
            self._pitch_lo, self._pitch_hi = 59, 73
        self.fit_view(notify=False)

    def get_selection(self):
        return self.sel

    def clear_selection(self):
        self.sel = None
        self.redraw()
        self._notify()

    def set_playhead(self, t):
        self.playhead_t = t
        self._move_playhead()

    def clear_playhead(self):
        self.playhead_t = None
        self._move_playhead()

    def _move_playhead(self):
        """Move the one persistent playhead item (no delete/create per frame).

        Writes to Tk only when the line actually moves, so a stationary
        playhead costs nothing — during playback this is the only item that
        changes, and it repaints in ~0.1 ms.
        """
        head = self._head
        alive = head is not None
        if alive:
            try:
                alive = self.type(head) == "line"
            except tk.TclError:
                alive = False
        t = self.playhead_t
        x = self._x(t) if t is not None else None
        if x is not None and not (self.MARGIN_L
                                  <= x <= self.MARGIN_L + self._plot_w()):
            x = None                        # scrolled out of view
        if x is None:
            if alive and self._head_shown:
                self.itemconfigure(head, state="hidden")
                self._head_shown = False
            return
        y0 = self.MARGIN_T
        geo = (x, y0, x, y0 + self._plot_h())
        if not alive:
            self._head = self.create_line(*geo, fill=self.PLAYHEAD, width=2,
                                          tags="playhead")
            self._head_geo, self._head_shown, self._head_top = geo, True, True
            return
        if self._head_geo != geo:
            self.coords(head, *geo)
            self._head_geo = geo
        if not self._head_shown:
            self.itemconfigure(head, state="normal")
            self._head_shown = True
        if not self._head_top:              # notes were added on top of it
            self.tag_raise(head)
            self._head_top = True

    # ---------------- coords ----------------
    # NB: tkinter widgets already own a `self._w` string attribute (the
    # widget path) — our helpers must not collide with it.
    def _plot_w(self):
        return max(50, self.winfo_width() - self.MARGIN_L - 6)

    def _plot_h(self):
        return max(30, self.winfo_height() - self.MARGIN_T - 4)

    def _x(self, t):
        span = self.view_end - self.view_start
        return self.MARGIN_L + (t - self.view_start) / span * self._plot_w()

    def _t(self, x):
        span = self.view_end - self.view_start
        return self.view_start + (x - self.MARGIN_L) / self._plot_w() * span

    # ---------------- view ----------------
    def fit_view(self, notify=True):
        self.view_start = 0.0
        self.view_end = max(self.duration * 1.02, 1.0)
        self.redraw()
        if notify:
            self._notify()

    def _clamp_view(self):
        span = self.view_end - self.view_start
        span = min(max(span, 0.2), max(self.duration * 2.0, 1.0))
        if self.view_start < -span * 0.2:
            self.view_start = -span * 0.2
        if self.view_start + span > self.duration + span * 0.2:
            self.view_start = self.duration + span * 0.2 - span
        self.view_end = self.view_start + span

    # ---------------- drawing ----------------
    def redraw(self):
        """Request a repaint.

        Repaints are coalesced into one per idle cycle, so a burst of
        <Motion>/<MouseWheel> events costs one redraw instead of one each —
        the previous code rebuilt every canvas item per event.
        """
        if self._redraw_id is None:
            self._redraw_id = self.after_idle(self._redraw_now)

    def _redraw_now(self):
        self._redraw_id = None
        try:
            self._draw()
        except tk.TclError:
            pass                            # window torn down mid-repaint

    def _draw(self):
        pw = max(50, self.winfo_width() - self.MARGIN_L - 6)
        ph = max(30, self.winfo_height() - self.MARGIN_T - 4)
        rows = max(1, self._pitch_hi - self._pitch_lo)
        sy = ph / rows                                  # px per semitone
        span = max(self.view_end - self.view_start, 1e-9)
        sx = pw / span                                  # px per second

        # While the user is dragging a selection the notes are not restyled
        # and the band is drawn as bare edges: Tk repaints ~30 us per canvas
        # item, so repainting a full-width stippled band plus every note it
        # covers is what made rubber-banding crawl.  The real band + the note
        # highlight land on release.
        dragging = self._drag is not None and self._drag[0] == "select"

        pools = (self._bands, self._grid, self._labels,
                 self._rects, self._rects_sel,
                 self._sel_rect, self._sel_edges)
        for pool in pools:
            pool.begin()
        self._draw_grid(pw, ph, sx)
        self._draw_sel_band(ph, sx, edges_only=dragging)
        self._draw_notes(pw, sx, sy, highlight=not dragging)
        self._draw_borders(pw, ph)
        for pool in pools:
            pool.commit()
        if any(pool.grew for pool in pools):
            self._head_top = False          # new items went above the playhead
            for pool in pools:
                pool.grew = False
        self._move_playhead()

    def _draw_grid(self, pw, ph, sx):
        """Octave bands, pitch labels, time grid + labels."""
        ml, mt = self.MARGIN_L, self.MARGIN_T
        top = self._pitch_hi
        sy = ph / max(1, top - self._pitch_lo)
        bands, labels, grid = self._bands, self._labels, self._grid
        for p in range(self._pitch_lo, top + 1):
            if p % 12:                      # only the Cs are worth labelling
                continue
            y = mt + (top - p) * sy
            bands.place(ml, y - sy, ml + pw, y)
            i = labels.place(4, y - sy / 2)
            labels.style(i, text=midi_to_name(max(0, min(127, p))),
                         anchor="w")

        step = self._nice_step(self.view_end - self.view_start)
        t = (int(self.view_start / step) + 1) * step
        while t < self.view_end:
            x = ml + (t - self.view_start) * sx
            grid.place(x, mt, x, mt + ph)
            i = labels.place(x + 2, 2)
            labels.style(i, text=f"{t:.2f}".rstrip("0").rstrip(".") + "s",
                         anchor="nw")
            t += step

    def _draw_sel_band(self, ph, sx, edges_only=False):
        edges = self._sel_edges
        if not self.sel:
            return                          # commit() hides both pools
        ml, mt = self.MARGIN_L, self.MARGIN_T
        a, b = self.sel
        x0 = ml + (a - self.view_start) * sx
        x1 = ml + (b - self.view_start) * sx
        if not edges_only:
            self._sel_rect.place(x0, mt, x1, mt + ph)
        edges.place(x0, mt, x0, mt + ph)
        edges.place(x1, mt, x1, mt + ph)

    def _draw_notes(self, pw, sx, sy, highlight=True):
        ml, mt = self.MARGIN_L, self.MARGIN_T
        top = self._pitch_hi
        t0, t1 = self.view_start, self.view_end
        sel = self.sel if highlight else None
        a, b = sel if sel else (0.0, 0.0)

        vis = [n for n in self.notes if n.end >= t0 and n.start <= t1]
        if not vis:
            return
        if len(vis) > self.NOTE_BUDGET:
            self._draw_notes_dense(vis, ml, mt, sx, sy, a, b)
            return

        right = ml + pw
        plain, selected = self._rects, self._rects_sel
        for n in vis:
            x0 = ml + (n.start - t0) * sx
            x1 = ml + (n.end - t0) * sx
            if x0 < ml:
                x0 = ml
            if x1 > right:
                x1 = right
            if x1 - x0 < 2.0:
                x1 = x0 + 2.0
            y0 = mt + (top - n.pitch - 1) * sy + 1.0
            y1 = mt + (top - n.pitch) * sy - 1.0
            if y1 - y0 < 2.0:
                y1 = y0 + 2.0
            pool = selected if (sel and a <= n.start < b) else plain
            pool.place(x0, y0, x1, y1)

    def _draw_notes_dense(self, vis, ml, mt, sx, sy, a, b):
        """Aggregate mode for whole-song views of a big MIDI.

        Above NOTE_BUDGET notes the individual rectangles are sub-pixel wide
        anyway, so the roll draws one item per (pixel column, run of adjacent
        pitch rows) instead.  Visually the same smear, but the canvas item
        count is bounded by the plot width instead of the note count.
        """
        cols: dict[int, list[int]] = {}
        for n in vis:
            cols.setdefault(int((n.start - self.view_start) * sx),
                            []).append(n.pitch)
        t0, marked = self.view_start, b > a
        plain, selected = self._rects, self._rects_sel
        for c in sorted(cols):
            pitches = sorted(set(cols[c]))
            pool = selected if marked and a <= t0 + c / sx < b else plain
            lo = hi = pitches[0]
            for p in pitches[1:]:
                if p <= hi + 2:             # a 2-row gap is invisible at 1 px
                    hi = p
                    continue
                self._dense_rect(pool, c, lo, hi, ml, mt, sy)
                lo = hi = p
            self._dense_rect(pool, c, lo, hi, ml, mt, sy)

    def _dense_rect(self, pool, c, lo, hi, ml, mt, sy):
        top = self._pitch_hi
        x = ml + c
        pool.place(x, mt + (top - hi - 1) * sy + 1.0,
                   x + 2.0, mt + (top - lo) * sy - 1.0)

    def _draw_borders(self, pw, ph):
        ml, mt = self.MARGIN_L, self.MARGIN_T
        self._grid.place(ml, mt, ml, mt + ph)
        self._grid.place(ml, mt, ml + pw, mt)

    @staticmethod
    def _nice_step(span, target=10):
        raw = span / target
        for s in (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300,
                  600, 1200):
            if s >= raw:
                return s
        return 3600

    # ---------------- interaction ----------------
    def _press(self, e, mode="select"):
        if mode == "pan" or e.state & 0x0001:      # shift held
            self._drag = ("pan", e.x, (self.view_start, self.view_end))
        else:
            self._drag = ("select", e.x, self._t(e.x))

    def _motion(self, e):
        if not self._drag:
            return
        mode = self._drag[0]
        if mode == "pan":
            x0, (vs, ve) = self._drag[1], self._drag[2]
            dt = (x0 - e.x) / self._plot_w() * (ve - vs)
            self.view_start, self.view_end = vs + dt, ve + dt
            self._clamp_view()
            self.redraw()
        else:
            t0 = self._drag[2]
            t1 = self._t(e.x)
            self.sel = (min(t0, t1), max(t0, t1))
            self.redraw()

    def _release(self, e):
        if not self._drag:
            return
        mode, x0 = self._drag[0], self._drag[1]
        self._drag = None
        if mode == "select":
            if abs(e.x - x0) < 4:            # plain click clears selection
                self.sel = None
            elif self.sel and self.sel[1] - self.sel[0] < 0.02:
                self.sel = None              # too small to be meaningful
            self.redraw()
            self._notify()

    def _wheel(self, e):
        span = self.view_end - self.view_start
        factor = 0.8 if e.delta > 0 else 1.25
        new_span = min(max(span * factor, 0.2), max(self.duration * 2, 1.0))
        anchor = self._t(e.x)
        ratio = (anchor - self.view_start) / span
        self.view_start = anchor - ratio * new_span
        self.view_end = self.view_start + new_span
        self._clamp_view()
        self.redraw()

    def _notify(self):
        if self.on_select:
            self.on_select(self.sel)


# ==========================================================================
# playback control
# ==========================================================================

class GuiControl(PlaybackControl):
    """Thread-safe pause/stop flags driven by GUI buttons."""

    def __init__(self):
        self._pause = threading.Event()
        self._stop = threading.Event()

    def toggle_pause(self):
        if self._pause.is_set():
            self._pause.clear()
        else:
            self._pause.set()

    def stop(self):
        self._stop.set()

    def reset(self):
        self._pause.clear()
        self._stop.clear()

    def is_paused(self) -> bool:
        return self._pause.is_set()

    def should_stop(self) -> bool:
        return self._stop.is_set()


# ==========================================================================
# app
# ==========================================================================

class MidiPlayerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("delta_music_player — MIDI 自动演奏")
        root.geometry("940x760")
        root.minsize(800, 620)

        self.q: queue.Queue = queue.Queue()
        self.control = GuiControl()
        self.worker: threading.Thread | None = None

        self.mid = None
        self.parse_result = None
        self.notes = []
        self.mapped = []
        self.events_total_time = 0.0
        self.clip_offset = 0.0

        # audition (listen via Windows built-in MIDI synth)
        self.auditioner = MidiAuditioner()
        self._aud_poll_id = None
        self.aud_clip_offset = 0.0
        self.audition_duration = 0.0

        # target window (game) binding
        self.target_window: WindowInfo | None = None
        self._window_choices: list[WindowInfo] = []

        base = Path(__file__).resolve().parent
        self.base_dir = base
        self.settings = self._load_json(base / "config" / "settings.json", {})
        try:
            self.profile = InstrumentProfile.load(
                base / "config" / "instrument.json")
        except Exception as e:
            messagebox.showerror("配置错误", f"instrument.json 加载失败:\n{e}")
            raise
        self.mapper = InstrumentMapper(self.profile)

        self.key_labels: dict[str, tuple[tk.Label, str]] = {}
        self._lit: dict[str, bool] = {}      # key-panel lamp state
        self._build_ui()
        self._poll_queue()

    @staticmethod
    def _load_json(path: Path, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    # ------------------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 6, "pady": 3}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Label(row, text="MIDI 文件:").pack(side="left")
        self.path_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.path_var).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="选择 MIDI...", command=self.choose_file).pack(
            side="left")

        row2 = ttk.Frame(top)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Label(row2, text="轨道:").pack(side="left")
        self.track_var = tk.StringVar(value="自动(主旋律)")
        self.track_cb = ttk.Combobox(
            row2, textvariable=self.track_var, state="readonly", width=34,
            values=["自动(主旋律)", "全部合并"])
        self.track_cb.pack(side="left", padx=(2, 12))
        self.track_cb.bind("<<ComboboxSelected>>", lambda e: self.reparse())

        ttk.Label(row2, text="移调:").pack(side="left")
        self.transpose_var = tk.StringVar(value="auto")
        ttk.Combobox(row2, textvariable=self.transpose_var, state="readonly",
                     width=8, values=["auto", "none", "custom"]).pack(
            side="left", padx=(2, 6))
        self.transpose_spin = ttk.Spinbox(row2, from_=-24, to=24, width=5,
                                          state="disabled")
        self.transpose_spin.pack(side="left", padx=(0, 12))
        self.transpose_var.trace_add(
            "write", lambda *a: self.transpose_spin.configure(
                state="normal" if self.transpose_var.get() == "custom"
                else "disabled"))

        ttk.Label(row2, text="倒计时(秒):").pack(side="left")
        self.delay_var = tk.DoubleVar(
            value=float(self.settings.get("playback", {}).get(
                "start_delay_sec", 5)))
        ttk.Spinbox(row2, from_=0, to=30, increment=1, width=5,
                    textvariable=self.delay_var).pack(side="left")

        self.info_var = tk.StringVar(value="请选择一个 MIDI 文件")
        ttk.Label(top, textvariable=self.info_var).pack(anchor="w", pady=(4, 0))

        # ---- target window row ----
        row3 = ttk.Frame(top)
        row3.pack(fill="x", pady=(4, 0))
        ttk.Label(row3, text="目标窗口:").pack(side="left")
        self.window_var = tk.StringVar()
        self.window_cb = ttk.Combobox(row3, textvariable=self.window_var,
                                      state="readonly", width=40)
        self.window_cb.pack(side="left", padx=(2, 6))
        self.window_cb.bind("<<ComboboxSelected>>", self._on_window_choice)
        ttk.Button(row3, text="刷新", command=self.refresh_windows).pack(
            side="left", padx=(0, 4))
        self.pick_btn = ttk.Button(row3, text="点选窗口(3秒内指向游戏)",
                                   command=self.pick_window)
        self.pick_btn.pack(side="left", padx=(0, 10))
        ttk.Label(row3, text="键盘模式:").pack(side="left")
        self.kbmode_var = tk.StringVar(
            value=str(self.settings.get("playback", {}).get(
                "keyboard_mode", "scancode")))
        ttk.Combobox(row3, textvariable=self.kbmode_var, state="readonly",
                     width=9, values=["scancode", "vk"]).pack(
            side="left", padx=(2, 10))

        # ---- admin warning row ----
        if not is_admin():
            warn = tk.Label(
                top, anchor="w", fg="#9c0006", bg="#ffeb9c",
                text="⚠ 当前不是管理员权限 — 若游戏以管理员运行，输入会被系统"
                     "静默丢弃（UIPI）！",
                font=("", 9, "bold"), padx=6, pady=2)
            warn.pack(fill="x", pady=(4, 0))
            ttk.Button(top, text="以管理员身份重启",
                       command=self._relaunch_admin).pack(anchor="w",
                                                          pady=(2, 0))

        # ---- key panel ----
        panel = ttk.LabelFrame(
            self.root, text="演奏按键（来自 config/instrument.json，可校准）")
        panel.pack(fill="x", **pad)
        self._build_key_panel(panel)

        # ---- piano roll ----
        roll_frame = ttk.LabelFrame(
            self.root,
            text="钢琴卷帘 — 拖拽框选演奏区间 | 滚轮缩放 | Shift/中键拖拽平移 | 双击全曲")
        roll_frame.pack(fill="x", **pad)
        self.piano = PianoRoll(roll_frame, on_select=self._on_selection)
        self.piano.pack(fill="x", padx=4, pady=2)

        sel_row = ttk.Frame(roll_frame)
        sel_row.pack(fill="x", padx=4, pady=(0, 4))
        self.sel_var = tk.StringVar(value="未选择区间 — 将演奏整曲")
        ttk.Label(sel_row, textvariable=self.sel_var,
                  font=("", 9, "bold")).pack(side="left")
        ttk.Button(sel_row, text="清除选择",
                   command=self.piano.clear_selection).pack(
            side="right", padx=(4, 0))
        ttk.Button(sel_row, text="导出选中段 .mid",
                   command=self.export_selection).pack(side="right")

        # ---- preview ----
        mid_frame = ttk.Frame(self.root)
        mid_frame.pack(fill="both", expand=True, **pad)
        self.preview = scrolledtext.ScrolledText(
            mid_frame, height=9, font=("Consolas", 9), state="disabled")
        self.preview.pack(fill="both", expand=True)
        btns = ttk.Frame(mid_frame)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="预览(移调+按键映射)",
                   command=self.do_preview).pack(side="left")

        # ---- transport ----
        transport = ttk.Frame(self.root)
        transport.pack(fill="x", **pad)
        self.aud_btn = ttk.Button(transport, text="🔊 试听(钢琴)",
                                  command=self.toggle_audition,
                                  state="disabled")
        self.aud_btn.pack(side="left", padx=(0, 10))
        self.play_btn = ttk.Button(transport, text="▶ 开始演奏",
                                   command=self.start_play, state="disabled")
        self.play_btn.pack(side="left", padx=(0, 6))
        self.pause_btn = ttk.Button(transport, text="⏸ 暂停",
                                    command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=(0, 6))
        self.stop_btn = ttk.Button(transport, text="⏹ 停止",
                                   command=self.do_stop, state="disabled")
        self.stop_btn.pack(side="left")
        self.progress = ttk.Progressbar(transport, maximum=1000)
        self.progress.pack(side="left", fill="x", expand=True, padx=10)
        self.pos_var = tk.StringVar(value="0.0s / 0.0s")
        ttk.Label(transport, textvariable=self.pos_var).pack(side="left")

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken",
                  anchor="w", font=("", 10)).pack(fill="x", side="bottom")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_windows()

    # ------------------------------------------------------------------
    # target window binding (DF-Auto_Blois style: bind hwnd, foreground it
    # before sending; UIPI needs admin when the game is elevated)
    # ------------------------------------------------------------------
    def refresh_windows(self):
        twcfg = self.settings.get("target_window", {})
        cands: list[WindowInfo] = []
        seen = set()
        game = find_game_window(twcfg.get("title_keywords", []),
                                twcfg.get("process_names", []))
        if game:
            cands.append(game)
            seen.add(game.hwnd)
        try:
            for w in list_top_windows():
                if w.hwnd not in seen and w.title:
                    cands.append(w)
                    seen.add(w.hwnd)
                if len(cands) >= 40:
                    break
        except Exception:
            pass
        self._window_choices = cands
        self.window_cb.configure(values=[w.label() for w in cands])
        if game and not self.target_window:
            self.target_window = game
            self.window_var.set(game.label())
        elif self.target_window:
            self.window_var.set(self.target_window.label())

    def _on_window_choice(self, _event=None):
        idx = self.window_cb.current()
        if 0 <= idx < len(self._window_choices):
            self.target_window = self._window_choices[idx]
            el = self.target_window.elevated
            if el and not is_admin():
                self.status_var.set(
                    f"已绑定 {self.target_window.title} — ⚠ 该窗口是管理员"
                    f"权限而本程序不是，输入会被 UIPI 丢弃，请先提权重启！")
            else:
                self.status_var.set(
                    f"已绑定目标窗口: {self.target_window.label()}")

    def _set_target(self, info: WindowInfo):
        self.target_window = info
        if info not in self._window_choices:
            self._window_choices.insert(0, info)
            self.window_cb.configure(
                values=[w.label() for w in self._window_choices])
        self.window_var.set(info.label())

    def pick_window(self):
        self.pick_btn.configure(state="disabled")
        self._pick_countdown(3)

    def _pick_countdown(self, n: int):
        if n > 0:
            self.status_var.set(
                f"点选窗口: {n} 秒内把鼠标移到游戏窗口上（不要点击）...")
            self.root.after(1000, self._pick_countdown, n - 1)
            return
        self.pick_btn.configure(state="normal")
        try:
            info = window_under_cursor()
        except Exception:
            info = None
        if info:
            self._set_target(info)
            self.status_var.set(f"已绑定: {info.label()}")
        else:
            self.status_var.set("点选失败：鼠标下方没有窗口")

    def _relaunch_admin(self):
        if not messagebox.askyesno(
                "以管理员身份重启",
                "将弹出 UAC 确认框，当前窗口会关闭并以管理员权限重开。\n"
                "继续？"):
            return
        if relaunch_as_admin():
            self.stop_audition(quiet=True)
            self.control.stop()
            self.root.destroy()
            sys.exit(0)
        messagebox.showerror("失败", "提权重启失败或被取消")

    def _build_key_panel(self, panel):
        mod_row = ttk.Frame(panel)
        mod_row.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(mod_row, text="修饰键:").pack(side="left")
        for name, mod in self.profile.modifiers.items():
            cn = MOD_CN.get(name, name)
            inp = MOUSE_CN.get(mod.input, mod.input)
            lbl = tk.Label(mod_row,
                           text=f"{cn} ({name})\n{inp}  {mod.semitones:+d}半音",
                           relief="ridge", bd=2, padx=10, pady=2,
                           bg="#f0f0f0")
            lbl.pack(side="left", padx=4)
            self.key_labels[mod.input] = (lbl, "#f0f0f0")

        base_row = ttk.Frame(panel)
        base_row.pack(fill="x", padx=6, pady=(2, 6))
        ttk.Label(base_row, text="基础音键:").pack(side="left")
        plain = sorted(
            (pitch, combo) for pitch, combo in self.profile.note_map.items()
            if not combo.modifiers
        )
        for pitch, combo in plain:
            lbl = tk.Label(base_row,
                           text=f"{combo.key.upper()}\n{midi_to_name(pitch)}",
                           relief="ridge", bd=2, width=4, pady=2,
                           bg="#f0f0f0", font=("Consolas", 10, "bold"))
            lbl.pack(side="left", padx=2)
            self.key_labels[combo.key] = (lbl, "#f0f0f0")
        lo, hi = self.profile.range()
        ttk.Label(
            panel,
            text=f"可奏音域: {midi_to_name(lo)} - {midi_to_name(hi)} "
                 f"(MIDI {lo}-{hi}, 共 {len(self.profile.note_map)} 个音)   "
                 f"组合示例: 半音+Z, 升调+X, 降调+半音+Z ...",
            font=("", 8)).pack(anchor="w", padx=8, pady=(0, 6))

    # ------------------------------------------------------------------
    # file / parse / selection
    # ------------------------------------------------------------------
    def choose_file(self):
        path = filedialog.askopenfilename(
            title="选择 MIDI 文件",
            filetypes=[("MIDI 文件", "*.mid *.midi"), ("所有文件", "*.*")])
        if path:
            self.path_var.set(path)
            self.load_file(Path(path))

    def load_file(self, path: Path):
        try:
            if not is_midi(path):
                raise ValueError(f"不是 MIDI 文件: {path}")
            self.mid = load_midi(path)
        except Exception as e:
            messagebox.showerror("加载失败", str(e))
            return
        values = ["自动(主旋律)", "全部合并"]
        tmap = build_tempo_map(self.mid)      # shared: one scan, not N
        for i, track in enumerate(self.mid.tracks):
            try:
                r = parse_midi(self.mid, track=i, tmap=tmap)
                cnt = len(r.notes)
                if cnt:
                    avg = round(sum(x.pitch for x in r.notes) / cnt)
                    mono = sum(
                        1 for a, b in zip(r.notes, r.notes[1:])
                        if a.end <= b.start + 1e-9) / max(1, cnt - 1)
                    tag = " [鼓]" if _is_drum_track(track) else ""
                    values.append(
                        f"轨道 {i}: {cnt} 音, 均高 {midi_to_name(avg)}, "
                        f"单音率 {mono:.0%}{tag}")
                else:
                    values.append(f"轨道 {i}: (无音符)")
            except Exception:
                values.append(f"轨道 {i}")
        self.track_cb.configure(values=values)
        self.track_var.set("自动(主旋律)")
        self.reparse()

    def reparse(self):
        if self.mid is None:
            return
        self.stop_audition(quiet=True)
        choice = self.track_var.get()
        try:
            if choice.startswith("轨道"):
                idx = int(choice.split()[1].rstrip(":"))
                result = parse_midi(self.mid, track=idx)
            elif choice == "全部合并":
                result = parse_midi(self.mid, auto_track=False)
            else:
                result = parse_midi(self.mid, auto_track=True)
        except Exception as e:
            messagebox.showerror("解析失败", str(e))
            return
        self.parse_result = result
        self.notes = result.notes
        self.piano.set_notes(self.notes, result.total_duration)
        self.clip_offset = 0.0
        s = summarize(self.notes)
        if s["count"] == 0:
            self.info_var.set("该轨道没有音符")
            self.play_btn.configure(state="disabled")
            self.aud_btn.configure(state="disabled")
            return
        used = (f"，使用轨道 {result.track_index}"
                if result.track_index is not None else "，合并全部轨道")
        self.info_var.set(
            f"音符 {s['count']} 个 | 音域 {s['lowest_note']} - "
            f"{s['highest_note']} | 时长 {s['total_duration']:.1f}s{used}")
        self.play_btn.configure(state="normal")
        self.aud_btn.configure(state="normal")
        self.sel_var.set("未选择区间 — 将演奏整曲")
        self._append_preview(
            f"已加载: {Path(self.path_var.get()).name or '(demo)'}\n"
            f"{s['count']} notes, {s['lowest_note']}-{s['highest_note']}, "
            f"{s['total_duration']:.1f}s\n")

    def _on_selection(self, sel):
        if sel is None:
            self.clip_offset = 0.0
            self.sel_var.set("未选择区间 — 将演奏整曲")
            return
        a, b = sel
        cnt = sum(1 for n in self.notes if n.end > a and n.start < b)
        self.sel_var.set(
            f"已选区间: {a:.2f}s → {b:.2f}s  ({b - a:.2f}s, {cnt} 个音)")

    def current_notes(self):
        """Notes to play: the selected region (rebased to 0) or the song."""
        sel = self.piano.get_selection()
        if sel:
            clipped = clip_notes(self.notes, sel[0], sel[1], rebase=True)
            self.clip_offset = sel[0]
            return clipped
        self.clip_offset = 0.0
        return list(self.notes)

    def export_selection(self):
        sel = self.piano.get_selection()
        if not sel:
            messagebox.showinfo("提示", "先在钢琴卷帘上拖拽选择一个区间")
            return
        clipped = clip_notes(self.notes, sel[0], sel[1], rebase=False)
        if not clipped:
            messagebox.showinfo("提示", "选中区间内没有音符")
            return
        stem = Path(self.path_var.get()).stem or "selection"
        out_dir = self.base_dir / self.settings.get("output_dir", "乐库")
        out = out_dir / f"{stem}_sel_{sel[0]:.1f}-{sel[1]:.1f}.mid"
        write_midi(clipped, out)
        self._append_preview(f"已导出选中段: {out} ({len(clipped)} 音)")
        messagebox.showinfo("导出成功", f"{out}\n{len(clipped)} 个音符")

    # ------------------------------------------------------------------
    # transpose + mono + map
    # ------------------------------------------------------------------
    def _transpose_choice(self):
        v = self.transpose_var.get()
        if v == "auto":
            return "auto"
        if v == "none":
            return "none"
        try:
            return int(float(self.transpose_spin.get()))
        except (ValueError, tk.TclError):
            return 0

    def compute_mapped(self):
        notes = self.current_notes()
        tcfg = self.settings.get("transposer", {})
        transposed, report = transpose_notes(
            notes, self.profile.playable_pitches(),
            semitones=self._transpose_choice(),
            search_range=int(tcfg.get("search_range", 24)),
            allow_folding=bool(tcfg.get("allow_octave_folding", True)),
            allow_snap=bool(tcfg.get("allow_nearest_snap", True)),
            max_snap=int(tcfg.get("max_snap_semitones", 2)),
        )
        mono_line = ""
        mcfg = self.settings.get("monophonic", {})
        if bool(mcfg.get("enabled", True)):
            from music.monophonic import make_monophonic
            transposed, mono_info = make_monophonic(
                transposed,
                select=str(mcfg.get("select", "highest")),
                cluster_window_ms=float(mcfg.get("cluster_window_ms", 40)),
            )
            mono_line = mono_info.format()
        mapped, unmapped = self.mapper.map_notes(transposed)
        return report, mapped, unmapped, mono_line

    def _append_preview(self, text: str):
        self.preview.configure(state="normal")
        self.preview.insert("end", text + "\n")
        self.preview.see("end")
        self.preview.configure(state="disabled")

    def do_preview(self):
        if not self.notes:
            messagebox.showinfo("提示", "请先选择 MIDI 文件")
            return
        report, mapped, unmapped, mono_line = self.compute_mapped()
        sel = self.piano.get_selection()
        head = (f"选中区间 {sel[0]:.2f}s - {sel[1]:.2f}s"
                if sel else "整曲")
        lines = ["=" * 62, f"[{head}]", report.format()]
        if mono_line:
            lines.append(mono_line)
        if unmapped:
            lines.append(f"WARNING: {len(unmapped)} 个音无法映射（将被跳过）")
        lines.append("-" * 62)
        for m in mapped:
            lines.append(f"{m.note.start:8.3f}  {m.note.name:4s} -> "
                         f"{m.combo.display():22s} (dur={m.note.duration:.3f})")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.configure(state="disabled")
        self._append_preview("\n".join(lines))
        self.status_var.set(
            f"预览完成[{head}]: {len(mapped)} 个音 | "
            f"transpose={report.semitones:+d}")

    # ------------------------------------------------------------------
    # playback
    # ------------------------------------------------------------------
    def start_play(self):
        if self.worker and self.worker.is_alive():
            return
        self.stop_audition(quiet=True)
        tw = self.target_window
        if tw and tw.elevated and not is_admin():
            if not messagebox.askyesno(
                    "权限问题 (UIPI)",
                    f"目标窗口「{tw.title}」以管理员权限运行，而本程序没有。\n"
                    "Windows 会静默丢弃低权限进程发给它的输入，游戏很可能"
                    "收不到按键。\n\n建议点上方「以管理员身份重启」。\n"
                    "仍要继续演奏吗？"):
                return
        if not self.notes:
            messagebox.showinfo("提示", "请先选择 MIDI 文件")
            return
        try:
            report, mapped, unmapped, mono_line = self.compute_mapped()
        except Exception as e:
            messagebox.showerror("错误", str(e))
            return
        if not mapped:
            messagebox.showerror(
                "错误", "没有可演奏的音符（选中的区间可能是空的）")
            return
        self.mapped = mapped
        pb = self.settings.get("playback", {})
        events, info = build_events(
            mapped, self.profile,
            min_key_hold_ms=float(pb.get("min_key_hold_ms", 8)),
            min_retrigger_gap_ms=float(pb.get("min_retrigger_gap_ms", 15)),
        )
        self.events_total_time = events[-1].time if events else 0.0
        self.control.reset()
        self.piano.clear_playhead()
        self.play_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal")
        self.stop_btn.configure(state="normal")
        sel = self.piano.get_selection()
        where = (f"选区 {sel[0]:.1f}-{sel[1]:.1f}s" if sel else "整曲")
        self.status_var.set(
            f"[{where}] 事件 {info.events} | 重触发移位 {info.shifted_notes} "
            f"| 修饰键连奏 {info.handoffs} —— 请切换到游戏窗口！")
        self.worker = threading.Thread(
            target=self._play_worker, args=(events, pb), daemon=True)
        self.worker.start()

    def _play_worker(self, events, pb):
        backend = None
        try:
            backend = Win32SendInputBackend(
                keyboard_mode=self.kbmode_var.get() or None)
            delay = float(self.delay_var.get())
            t_end = time.perf_counter() + delay
            while time.perf_counter() < t_end:
                if self.control.should_stop():
                    self.q.put(("aborted", None))
                    return
                left = t_end - time.perf_counter()
                self.q.put(("countdown", max(1, int(left + 0.999))))
                time.sleep(min(0.05, max(0.0, left)))
            # activate the bound game window right before playing
            # (same pattern as DF-Auto_Blois: SetForegroundWindow then send)
            tw = self.target_window
            auto_fg = bool(self.settings.get("target_window", {}).get(
                "auto_foreground", True))
            if tw and auto_fg:
                ok = activate_window(tw.hwnd)
                self.q.put(("status",
                            "[window] 游戏窗口已切到前台" if ok else
                            "[window] ⚠ 无法激活游戏窗口 — 请手动点击游戏！"))
                if not ok:
                    time.sleep(0.3)
            self.q.put(("playing", None))
            stats = execute(
                events, backend, self.control,
                busy_wait_threshold_ms=float(
                    pb.get("busy_wait_threshold_ms", 3)),
                max_sleep_chunk_ms=float(pb.get("max_sleep_chunk_ms", 5)),
                batch_inter_event_ms=float(pb.get("batch_inter_event_ms", 1)),
                latency_compensation_ms=float(
                    pb.get("latency_compensation_ms", 0)),
                progress_cb=lambda ev, _t: self.q.put(("event", ev)),
            )
            self.q.put(("done", stats.format()))
        except Exception as e:
            self.q.put(("error",
                        f"{e}\n\n{traceback.format_exc(limit=3)}"))
        finally:
            if backend is not None:
                backend.close()
            self.q.put(("finished", None))

    def toggle_pause(self):
        self.control.toggle_pause()

    def do_stop(self):
        self.control.stop()

    # ------------------------------------------------------------------
    # audition — listen through the Windows built-in MIDI synth (piano),
    # so the user can pick a region by ear
    # ------------------------------------------------------------------
    def toggle_audition(self):
        if self.auditioner.is_playing():
            self.stop_audition()
            return
        if self.worker and self.worker.is_alive():
            return                            # live performance has priority
        if not self.notes:
            messagebox.showinfo("提示", "请先选择 MIDI 文件")
            return
        sel = self.piano.get_selection()
        if sel:
            notes = clip_notes(self.notes, sel[0], sel[1], rebase=True)
            self.aud_clip_offset = sel[0]
        else:
            notes = list(self.notes)
            self.aud_clip_offset = 0.0
        if not notes:
            messagebox.showinfo("提示", "选中区间内没有音符")
            return
        self.stop_audition(quiet=True)        # release the previous temp file
        tmp = (self.base_dir / self.settings.get("output_dir", "乐库")
               / "_audition.mid")
        try:
            write_midi(notes, tmp, program=0, track_name="audition piano")
            self.auditioner.play(tmp)
        except (AuditionError, OSError) as e:
            messagebox.showerror(
                "试听失败",
                f"{e}\n\n(试听使用 Windows 内置的 Microsoft GS Wavetable "
                f"Synth 合成器)")
            return
        self.audition_duration = max(n.end for n in notes)
        self.aud_btn.configure(text="⏹ 停止试听")
        where = f"选区 {sel[0]:.1f}-{sel[1]:.1f}s" if sel else "整曲"
        self.status_var.set(f"🔊 试听中(钢琴) [{where}]...")
        self._aud_poll()

    def _aud_poll(self):
        self._aud_poll_id = None
        pos = self.auditioner.position_ms()
        if self.auditioner.is_playing() and pos is not None:
            t = pos / 1000.0
            self.piano.set_playhead(t + self.aud_clip_offset)
            self.status_var.set(
                f"🔊 试听中(钢琴): {t:.1f}s / {self.audition_duration:.1f}s")
            self._aud_poll_id = self.root.after(80, self._aud_poll)
        else:
            self.stop_audition(finished=True)

    def stop_audition(self, finished=False, quiet=False):
        if self._aud_poll_id is not None:
            try:
                self.root.after_cancel(self._aud_poll_id)
            except Exception:
                pass
            self._aud_poll_id = None
        self.auditioner.stop()
        self.aud_btn.configure(text="🔊 试听(钢琴)")
        self.piano.clear_playhead()
        if not quiet:
            self.status_var.set("试听结束" if finished else "就绪")

    # ------------------------------------------------------------------
    # queue -> UI (main thread only)
    # ------------------------------------------------------------------
    def _poll_queue(self):
        """Drain the worker queue; collapse a tick's worth of events into one
        visual update.

        Only the last transition per physical input matters for a lamp and
        only the newest event matters for the playhead / progress bar / status
        line, so a dense passage costs a handful of Tcl calls per tick instead
        of 5-6 per event.
        """
        newest = last_down = None
        lights: dict[str, str] = {}
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "event":
                    lights[payload.identifier] = payload.action
                    newest = payload
                    if payload.action == "down":
                        last_down = payload
                    continue
                if lights:                      # flush before a state change
                    self._light(lights)
                    lights = {}
                if newest is not None:
                    self._transport(newest, last_down)
                    newest = last_down = None
                self._handle_msg(kind, payload)
        except queue.Empty:
            pass
        if lights:
            self._light(lights)
        if newest is not None:
            self._transport(newest, last_down)
        self.root.after(30, self._poll_queue)

    def _handle_msg(self, kind, payload):
        if kind == "countdown":
            self.status_var.set(
                f"演奏开始倒计时: {payload} 秒 —— 请聚焦游戏窗口！")
        elif kind == "status":
            self.status_var.set(payload)
        elif kind == "playing":
            self.status_var.set("演奏中...")
        elif kind == "done":
            self.status_var.set("演奏结束")
            self._append_preview("=" * 62 + "\n" + payload)
            messagebox.showinfo("演奏完成", payload)
        elif kind == "aborted":
            self.status_var.set("已取消")
        elif kind == "error":
            self.status_var.set("演奏出错")
            messagebox.showerror("演奏出错", str(payload)[:1500])
        elif kind == "finished":
            self.play_btn.configure(
                state="normal" if self.notes else "disabled")
            self.pause_btn.configure(state="disabled")
            self.stop_btn.configure(state="disabled")
            self._unlight_all()
            self.piano.clear_playhead()

    def _light(self, lights: dict):
        """Lamp the key panel; `lights` is {identifier: last action}."""
        for ident, action in lights.items():
            entry = self.key_labels.get(ident)
            if entry is None:
                continue
            lit = action == "down"
            if self._lit.get(ident) == lit:
                continue                    # already in that state
            self._lit[ident] = lit
            lbl, default_bg = entry
            lbl.configure(bg=LIT_BG if lit else default_bg)

    def _transport(self, ev, last_down):
        """Once-per-tick playhead / progress / status update."""
        if self.events_total_time > 0:
            frac = min(1.0, ev.time / self.events_total_time)
            self.progress.configure(value=int(frac * 1000))
            self.pos_var.set(
                f"{ev.time:.1f}s / {self.events_total_time:.1f}s")
        # playhead maps back to the ORIGINAL song timeline
        self.piano.set_playhead(ev.time + self.clip_offset)
        if last_down is not None and \
                0 <= last_down.note_index < len(self.mapped):
            m = self.mapped[last_down.note_index]
            self.status_var.set(
                f"♪ {m.note.name} -> {m.combo.display()}   "
                f"({last_down.time:.1f}s / {self.events_total_time:.1f}s)")

    def _unlight_all(self):
        self._lit.clear()
        for lbl, bg in self.key_labels.values():
            lbl.configure(bg=bg)

    def on_close(self):
        self.stop_audition(quiet=True)
        self.control.stop()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=1.0)
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    MidiPlayerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
