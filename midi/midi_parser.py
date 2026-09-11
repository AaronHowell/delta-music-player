"""MIDI -> NoteEvent[] parsing.

Handles:
  * tempo map (multiple set_tempo events across tracks, tempo changes mid-song)
  * ticks -> seconds conversion (piecewise over the tempo map)
  * note_on with velocity 0 == note_off
  * retriggered notes (same channel+note pressed twice before release):
    the first instance is closed at the second's start
  * merged multi-track parsing (mido's real-time iterator) or a single track
    (--track N) with our own tempo map
  * automatic melody-track selection: higher average pitch + more monophonic

mido's merged iterator (``for msg in mid``) yields messages across all tracks
in playback order with ``msg.time`` already converted to seconds using the
full tempo map — that is the default path.  For single-track parsing we build
the tempo map ourselves, because tempo events usually live in track 0 while
the melody lives in another track (a detail AutoMidiPlayer also calls out:
keep the original tempo map when filtering tracks).

Note on mido 1.3: there is no ``msg.delta`` attribute — raw track messages
carry their tick delta in ``msg.time`` (int), and the merged real-time
iterator rewrites ``msg.time`` to seconds (float).
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import mido

from music.note_event import NoteEvent, sort_notes

DEFAULT_TEMPO = 500_000  # microseconds per quarter note == 120 BPM

DRUM_CHANNEL = 9


@dataclass
class ParseResult:
    notes: List[NoteEvent]
    total_duration: float
    track_index: Optional[int] = None  # None => merged from all tracks
    track_auto_selected: bool = False
    dropped_zero_length: int = 0
    track_stats: List[dict] = field(default_factory=list)


# --------------------------------------------------------------------------
# tempo map
# --------------------------------------------------------------------------

TempoMap = Tuple[List[int], List[float], List[int], int]
# (ticks[], cumulative seconds[], tempo per segment[], ticks_per_beat)


def build_tempo_map(mid: mido.MidiFile) -> TempoMap:
    """Collect set_tempo events from ALL tracks (type-1 files usually put them
    in track 0 only), then precompute cumulative seconds per segment."""
    tpb = mid.ticks_per_beat
    events: List[Tuple[int, int]] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo":
                events.append((tick, msg.tempo))
    events.sort(key=lambda e: e[0])

    # first tempo at each tick wins; ensure a segment starting at tick 0
    segments: List[Tuple[int, int]] = []
    seen_ticks = set()
    for tick, tempo in events:
        if tick not in seen_ticks:
            seen_ticks.add(tick)
            segments.append((tick, tempo))
    if not segments or segments[0][0] != 0:
        segments.insert(0, (0, DEFAULT_TEMPO))

    ticks = [segments[0][0]]
    secs = [0.0]
    tempos = [segments[0][1]]
    for tick, tempo in segments[1:]:
        prev_tick, prev_sec, prev_tempo = ticks[-1], secs[-1], tempos[-1]
        secs.append(
            prev_sec + (tick - prev_tick) * prev_tempo / tpb / 1_000_000.0
        )
        ticks.append(tick)
        tempos.append(tempo)
    return ticks, secs, tempos, tpb


def tick_to_seconds(tick: int, tmap: TempoMap) -> float:
    ticks, secs, tempos, tpb = tmap
    i = bisect_right(ticks, tick) - 1
    return secs[i] + (tick - ticks[i]) * tempos[i] / tpb / 1_000_000.0


# --------------------------------------------------------------------------
# note collection
# --------------------------------------------------------------------------

class _NoteCollector:
    """Tracks open notes keyed by (channel, note) and emits NoteEvents.

    ``time`` values passed in are already seconds (merged mode) or ticks
    (track mode, converted at the end via ``tmap``).
    """

    def __init__(self, tmap: Optional[TempoMap] = None):
        self._tmap = tmap  # None => times already in seconds
        self._active: Dict[Tuple[int, int], Tuple[float, int]] = {}
        self.notes: List[NoteEvent] = []
        self.dropped_zero_length = 0

    def _to_sec(self, t: float) -> float:
        return tick_to_seconds(int(t), self._tmap) if self._tmap else float(t)

    def feed(self, msg: mido.Message, time: float) -> None:
        if msg.type == "note_on" and msg.velocity > 0:
            key = (msg.channel, msg.note)
            if key in self._active:
                self.close(msg.channel, msg.note, time)
            self._active[key] = (time, msg.velocity)
        elif msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        ):
            self.close(msg.channel, msg.note, time)

    def close(self, channel: int, note: int, time: float) -> None:
        key = (channel, note)
        if key not in self._active:
            return  # stray note_off, ignore
        start, velocity = self._active.pop(key)
        start_s, end_s = self._to_sec(start), self._to_sec(time)
        duration = end_s - start_s
        if duration <= 0:
            self.dropped_zero_length += 1
            return
        self.notes.append(
            NoteEvent(
                pitch=note,
                start=start_s,
                duration=duration,
                velocity=velocity,
                confidence=1.0,
            )
        )

    def close_all(self, end_time: float) -> None:
        for channel, note in list(self._active):
            self.close(channel, note, end_time)


# --------------------------------------------------------------------------
# parsing modes
# --------------------------------------------------------------------------

def parse_merged(mid: mido.MidiFile) -> ParseResult:
    """Parse all tracks merged, using mido's real-time iterator (msg.delta in
    seconds, tempo map fully applied)."""
    collector = _NoteCollector(tmap=None)
    now = 0.0
    for msg in mid:
        now += msg.time
        if msg.type in ("note_on", "note_off"):
            collector.feed(msg, now)
    collector.close_all(now)
    return ParseResult(
        notes=sort_notes(collector.notes),
        total_duration=now,
        track_index=None,
        dropped_zero_length=collector.dropped_zero_length,
    )


def parse_track(mid: mido.MidiFile, index: int) -> ParseResult:
    """Parse a single track with our own tempo map built from ALL tracks."""
    if not 0 <= index < len(mid.tracks):
        raise ValueError(
            f"track {index} out of range (file has {len(mid.tracks)} tracks)"
        )
    tmap = build_tempo_map(mid)
    collector = _NoteCollector(tmap=tmap)
    tick = 0
    for msg in mid.tracks[index]:
        tick += msg.time
        if msg.type in ("note_on", "note_off"):
            collector.feed(msg, tick)
    collector.close_all(tick)
    total = tick_to_seconds(tick, tmap)
    return ParseResult(
        notes=sort_notes(collector.notes),
        total_duration=total,
        track_index=index,
        dropped_zero_length=collector.dropped_zero_length,
    )


# --------------------------------------------------------------------------
# automatic melody-track selection
# --------------------------------------------------------------------------

def _mono_ratio(notes: List[NoteEvent]) -> float:
    """Union of sounding time / sum of durations.  1.0 == strictly monophonic.
    (The user spec: prefer tracks with a high monophonic ratio.)"""
    total = sum(n.duration for n in notes)
    if total <= 0:
        return 0.0
    union = 0.0
    cur_s = cur_e = None
    for n in sorted(notes, key=lambda n: n.start):
        if cur_e is None or n.start > cur_e:
            if cur_e is not None:
                union += cur_e - cur_s
            cur_s, cur_e = n.start, n.end
        else:
            cur_e = max(cur_e, n.end)
    if cur_e is not None:
        union += cur_e - cur_s
    return min(1.0, union / total)


def _is_drum_track(track: mido.MidiTrack) -> bool:
    note_msgs = [m for m in track if m.type in ("note_on", "note_off")]
    if not note_msgs:
        return False
    drums = sum(
        1 for m in note_msgs if getattr(m, "channel", None) == DRUM_CHANNEL
    )
    return drums * 2 > len(note_msgs)


def choose_melody_track(mid: mido.MidiFile) -> ParseResult:
    """Score every non-drum track and pick the most melody-like one.

    score = average pitch + 12 * monophonic ratio
    (high, singable, one-note-at-a-time => melody).  Ties: more notes wins,
    then the earlier track index.
    """
    best: Optional[Tuple[Tuple[float, int, int], ParseResult]] = None
    stats: List[dict] = []
    for i, track in enumerate(mid.tracks):
        if _is_drum_track(track):
            continue
        result = parse_track(mid, i)
        if len(result.notes) < 4:
            continue
        mean_pitch = sum(n.pitch for n in result.notes) / len(result.notes)
        mono = _mono_ratio(result.notes)
        score = mean_pitch + 12.0 * mono
        stats.append(
            {
                "track": i,
                "notes": len(result.notes),
                "mean_pitch": round(mean_pitch, 2),
                "mono_ratio": round(mono, 3),
                "score": round(score, 2),
            }
        )
        key = (score, len(result.notes), -i)
        if best is None or key > best[0]:
            best = (key, result)
    if best is None:
        # no convincing melody track — fall back to the merged parse
        result = parse_merged(mid)
        result.track_stats = stats
        return result
    result = best[1]
    result.track_auto_selected = True
    result.track_stats = stats
    return result


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def parse_midi(
    mid: mido.MidiFile, track: Optional[int] = None, auto_track: bool = True
) -> ParseResult:
    """track=N  -> parse that track only
    track=None -> auto-select melody track when the file has multiple tracks
                  (auto_track=True), else merge everything."""
    if track is not None:
        return parse_track(mid, track)
    if len(mid.tracks) > 1 and auto_track:
        return choose_melody_track(mid)
    return parse_merged(mid)
