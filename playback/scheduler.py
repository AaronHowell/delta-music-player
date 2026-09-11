"""Event scheduler: NoteEvent[] -> timed physical input events, and the
absolute-timeline execution loop.

Build stage (pure, testable):
  * each mapped note expands to: modifier-downs (config order), key-down at
    note start; key-up, modifier-ups (reverse order) at note end
  * key DEPTH COUNTING: overlapping notes sharing a physical input only emit
    a down at depth 0->1 and an up at depth 1->0, so a shared modifier is
    never released while another sounding note still needs it (MeowField's
    _keyDepths idea)
  * legato hand-off: when a previous note ends exactly where the next begins
    and they share a MODIFIER input, that modifier simply stays down (no
    retrigger, no timing shift).  Note keys are never handed off — a repeated
    key must physically retrigger or the game hears one long note.
  * minimum key hold: notes shorter than min_key_hold_ms get their release
    pushed back (games drop down/up pairs that are too close)
  * minimum retrigger gap: when an input would be pressed again within
    min_retrigger_gap_ms of its release, the WHOLE note shifts later, keeping
    the modifier/key ordering inside the note intact
  * events at the same instant are ordered: ups before downs (retrigger
    safety), modifiers before the note key on press, key before modifiers on
    release

Execution stage:
  * absolute timeline: target = origin + event.time - latency_compensation.
    Sleep errors never accumulate because every target derives from `origin`,
    never from the previous event.
  * two-phase wait: coarse sleeps (capped chunks) while far away, busy-wait
    inside the final few ms for sub-millisecond accuracy (MeowField pattern)
  * pause: release everything held, then shift `origin` by the paused
    duration — the timeline stays absolute (the in-flight note is cut, which
    is the honest behavior for a game instrument)
  * stop / exception: release everything held, always
  * records per-event timing error -> mean / P95 / max report
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from instrument.instrument_profile import InstrumentProfile
from instrument.mapper import MappedNote

EPS = 1e-9


@dataclass
class InputEvent:
    time: float          # seconds on the song timeline
    action: str          # "down" | "up"
    identifier: str      # physical input: "z", ",", "mouse_middle", ...
    kind: str            # "key" | "modifier"
    note_index: int = -1
    seq: int = 0         # emission order, tie-breaker within same instant


@dataclass
class BuildInfo:
    notes: int = 0
    events: int = 0
    suppressed_overlaps: int = 0   # down/up hidden by depth counting
    handoffs: int = 0              # modifiers kept down across a legato bound
    shifted_notes: int = 0         # notes delayed for the retrigger gap
    extended_notes: int = 0        # releases delayed for the min hold


@dataclass
class TimingStats:
    errors_ms: List[float] = field(default_factory=list)
    fired: int = 0
    stopped_early: bool = False
    paused_seconds: float = 0.0

    def record(self, error_s: float) -> None:
        self.errors_ms.append(error_s * 1000.0)
        self.fired += 1

    @property
    def mean_ms(self) -> float:
        return sum(self.errors_ms) / len(self.errors_ms) if self.errors_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.errors_ms:
            return 0.0
        s = sorted(self.errors_ms)
        return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]

    @property
    def max_ms(self) -> float:
        return max(self.errors_ms) if self.errors_ms else 0.0

    def format(self) -> str:
        lines = [
            f"Events fired: {self.fired}"
            + ("  (stopped early)" if self.stopped_early else ""),
            f"Timing error: mean={self.mean_ms:+.2f} ms  "
            f"P95={self.p95_ms:+.2f} ms  max={self.max_ms:+.2f} ms",
        ]
        if self.paused_seconds > 0:
            lines.append(f"Paused for: {self.paused_seconds:.2f} s total")
        if self.errors_ms:
            lines.append(f"Final drift: {self.errors_ms[-1]:+.2f} ms")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# build stage
# --------------------------------------------------------------------------

def _note_identifiers(
    m: MappedNote, profile: InstrumentProfile
) -> Tuple[List[str], List[str]]:
    """(identifiers in press order [mods..., key], matching kinds)."""
    idents = [profile.modifiers[n].input for n in m.combo.modifiers]
    kinds = ["modifier"] * len(idents)
    idents.append(m.combo.key)
    kinds.append("key")
    return idents, kinds


def build_events(
    mapped: List[MappedNote],
    profile: InstrumentProfile,
    *,
    min_key_hold_ms: float = 8.0,
    min_retrigger_gap_ms: float = 15.0,
) -> Tuple[List[InputEvent], BuildInfo]:
    min_hold = min_key_hold_ms / 1000.0
    gap = min_retrigger_gap_ms / 1000.0

    info = BuildInfo(notes=len(mapped))
    depth: Dict[str, int] = {}
    last_release: Dict[str, float] = {}
    # pending releases: (eff_end, [(identifier, kind)] in release order, nidx)
    pending_ups: List[Tuple[float, List[Tuple[str, str]], int]] = []
    events: List[InputEvent] = []
    seq = 0

    def emit(t: float, action: str, ident: str, kind: str, nidx: int) -> None:
        nonlocal seq
        events.append(InputEvent(t, action, ident, kind, nidx, seq))
        seq += 1

    def flush_due(until: float) -> None:
        """Emit every pending release with eff_end <= until.  Callers pass
        the epsilon they need (t_start - EPS for 'strictly before',
        t_start + EPS for 'up to the boundary')."""
        remaining = []
        for eff_end, idents, nidx in pending_ups:
            if eff_end <= until:
                for ident, kind in idents:
                    d = depth.get(ident, 0) - 1
                    depth[ident] = max(0, d)
                    if d <= 0:
                        emit(eff_end, "up", ident, kind, nidx)
                        last_release[ident] = eff_end
                    else:
                        info.suppressed_overlaps += 1
            else:
                remaining.append((eff_end, idents, nidx))
        pending_ups[:] = remaining

    ordered = sorted(enumerate(mapped), key=lambda p: (p[1].note.start, p[0]))
    for nidx, m in ordered:
        idents, kinds = _note_identifiers(m, profile)
        mod_idents: Set[str] = set(idents[:-1])
        t_start = m.note.start

        # A) releases strictly before this note's start
        flush_due(t_start - EPS)

        # B) releases exactly at the boundary: hand off shared modifiers
        #    (press them for the new note BEFORE releasing the old hold, so
        #    depth never hits 0 and nothing physical happens)
        handoff: Set[str] = set()
        boundary = [
            (ee, ids) for (ee, ids, _ni) in pending_ups
            if abs(ee - t_start) <= EPS
        ]
        if boundary:
            for _ee, ids in boundary:
                for ident, kind in ids:
                    if (kind == "modifier" and ident in mod_idents
                            and depth.get(ident, 0) >= 1):
                        handoff.add(ident)
            for ident in handoff:
                depth[ident] = depth.get(ident, 0) + 1  # this note's hold
                info.handoffs += 1
            flush_due(t_start + EPS)

        # C) earliest legal start: retrigger gap on inputs we will press
        eff_start = t_start
        for ident in idents:
            if ident in handoff or depth.get(ident, 0) > 0:
                continue        # stays down / already down: no physical press
            rel = last_release.get(ident)
            if rel is not None and eff_start < rel + gap - EPS:
                eff_start = rel + gap
        if eff_start > t_start + EPS:
            info.shifted_notes += 1

        # D) minimum hold
        eff_end = max(m.note.end, eff_start + min_hold)
        if eff_end > m.note.end + EPS:
            info.extended_notes += 1

        # E) presses (handoff inputs already carry this note's depth)
        for ident, kind in zip(idents, kinds):
            if ident in handoff:
                continue
            d = depth.get(ident, 0)
            depth[ident] = d + 1
            if d == 0:
                emit(eff_start, "down", ident, kind, nidx)
            else:
                info.suppressed_overlaps += 1

        # release order: key up first, then modifiers in reverse press order
        pending_ups.append(
            (eff_end, list(zip(reversed(idents), reversed(kinds))), nidx)
        )
        pending_ups.sort(key=lambda p: p[0])

    flush_due(float("inf"))

    events.sort(key=lambda e: (e.time, 0 if e.action == "up" else 1, e.seq))
    info.events = len(events)
    return events, info


# --------------------------------------------------------------------------
# execution stage
# --------------------------------------------------------------------------

class PlaybackControl:
    """Pause/stop signals consulted by the execution loop."""

    def is_paused(self) -> bool:
        return False

    def should_stop(self) -> bool:
        return False


def execute(
    events: List[InputEvent],
    backend,
    control: Optional[PlaybackControl] = None,
    *,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
    busy_wait_threshold_ms: float = 3.0,
    max_sleep_chunk_ms: float = 5.0,
    batch_inter_event_ms: float = 1.0,
    latency_compensation_ms: float = 0.0,
    progress_cb: Optional[Callable[[InputEvent, float], None]] = None,
) -> TimingStats:
    control = control or PlaybackControl()
    latency = latency_compensation_ms / 1000.0
    busy = busy_wait_threshold_ms / 1000.0
    chunk = max_sleep_chunk_ms / 1000.0
    gap = batch_inter_event_ms / 1000.0

    stats = TimingStats()
    held: Dict[str, None] = {}   # ordered set of physically-down inputs

    def fire(ev: InputEvent, target: float) -> None:
        actual = clock()
        if ev.action == "down":
            backend.down(ev.identifier)
            held[ev.identifier] = None
        else:
            backend.up(ev.identifier)
            held.pop(ev.identifier, None)
        stats.record(actual - target)
        if progress_cb:
            progress_cb(ev, actual)

    def release_all() -> None:
        for ident in reversed(list(held)):
            try:
                backend.up(ident)
            except Exception:
                pass    # cleanup must never kill the process (MeowField)
        held.clear()

    origin = clock()
    i, n = 0, len(events)
    try:
        while i < n:
            if control.should_stop():
                stats.stopped_early = True
                break
            if control.is_paused():
                release_all()
                paused_at = clock()
                while control.is_paused():
                    if control.should_stop():
                        break
                    sleep(0.01)
                elapsed_pause = clock() - paused_at
                stats.paused_seconds += elapsed_pause
                origin += elapsed_pause   # shift timeline, keep it absolute
                if control.should_stop():
                    stats.stopped_early = True
                    break
                continue

            target = origin + events[i].time - latency
            now = clock()
            if now < target:
                remaining = target - now
                if remaining > busy:
                    sleep(min(chunk, remaining - busy))
                # inside the busy-wait window: tight loop (no sleep) for
                # sub-millisecond accuracy
                continue

            # fire every event that is due now (same-instant batch)
            j = i
            while j < n and origin + events[j].time - latency <= clock() + EPS:
                j += 1
            j = max(j, i + 1)
            for k in range(i, j):
                fire(events[k], origin + events[k].time - latency)
                if gap > 0 and k + 1 < j:
                    sleep(gap)   # 1 ms between batch members: games may drop
                                 # inputs arriving within the same frame
            i = j
    finally:
        release_all()
    return stats
