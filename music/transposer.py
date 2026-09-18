"""Global transposition + octave folding into the instrument's playable set.

Strategy (per the project spec):
  1. ONE global transpose for the whole song, searched over
     -search_range..+search_range semitones, scored lexicographically:
       a. maximize notes that are directly playable after the shift
       b. minimize dropped notes
       c. minimize |transpose| (ties: prefer the smaller/more negative t)
  2. only the notes still out of range get octave-folded (+-12 until inside
     the instrument range).  Never fold every note independently first —
     that shreds the melodic contour.
  3. a folded note that lands on a pitch the instrument cannot play (scale
     gaps, e.g. white-key-only instruments) is snapped to the nearest
     playable pitch (within max_snap semitones), or dropped.

The playable set comes from the InstrumentProfile (profile.playable_pitches())
but this module only needs an iterable of MIDI pitches, keeping music/ free of
instrument/ imports.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from music.note_event import NoteEvent, sort_notes
from music.pitch_utils import midi_to_name


def fold_into_range(pitch: int, lo: int, hi: int) -> int:
    """Fold by octaves until inside [lo, hi].  If the range is narrower than
    an octave, folding cannot converge — clamp to the nearest bound instead."""
    if hi - lo < 12:
        return min(max(pitch, lo), hi)
    while pitch < lo:
        pitch += 12
    while pitch > hi:
        pitch -= 12
    return pitch


def nearest_playable(pitch: int, playable_sorted: Sequence[int]) -> Tuple[int, int]:
    """Return (nearest pitch, distance).  Ties prefer the lower pitch."""
    i = bisect_left(playable_sorted, pitch)
    if i == 0:
        first = playable_sorted[0]
        return first, first - pitch
    if i >= len(playable_sorted):
        last = playable_sorted[-1]
        return last, pitch - last
    lo, hi = playable_sorted[i - 1], playable_sorted[i]
    if pitch - lo <= hi - pitch:
        return lo, pitch - lo
    return hi, hi - pitch


@dataclass
class TransposeReport:
    semitones: int = 0
    original_range: Tuple[int, int] = (0, 0)
    instrument_range: Tuple[int, int] = (0, 0)
    total: int = 0
    direct: int = 0
    folded: int = 0
    snapped: int = 0
    dropped: int = 0
    dropped_notes: List[NoteEvent] = field(default_factory=list)

    def _pct(self, n: int) -> float:
        return 100.0 * n / self.total if self.total else 0.0

    def format(self) -> str:
        o_lo, o_hi = self.original_range
        i_lo, i_hi = self.instrument_range
        return "\n".join(
            [
                f"Original range: {midi_to_name(o_lo)} - {midi_to_name(o_hi)}",
                f"Instrument range: {midi_to_name(i_lo)} - {midi_to_name(i_hi)}",
                f"Global transpose: {self.semitones:+d}",
                f"Playable notes: {self._pct(self.direct + self.folded + self.snapped):.1f}%",
                f"  direct:        {self._pct(self.direct):.1f}%",
                f"  octave folded: {self._pct(self.folded):.1f}%",
                f"  snapped:       {self._pct(self.snapped):.1f}%",
                f"Dropped: {self._pct(self.dropped):.1f}%",
            ]
        )


def _classify(
    pitch: int,
    playable: frozenset,
    lo: int,
    hi: int,
    allow_folding: bool,
    allow_snap: bool,
    max_snap: int,
    playable_sorted: Sequence[int],
) -> Tuple[str, int]:
    """How one (already transposed) pitch would be handled: (action, pitch)."""
    if pitch in playable:
        return "direct", pitch
    if allow_folding:
        folded = fold_into_range(pitch, lo, hi)
        if folded in playable:
            return "folded", folded
        pitch = folded
    if allow_snap:
        cand, dist = nearest_playable(pitch, playable_sorted)
        if dist <= max_snap:
            return "snapped", cand
    return "dropped", pitch


def find_best_transpose(
    notes: Iterable[NoteEvent],
    playable: Iterable[int],
    search_range: int = 24,
    allow_folding: bool = True,
    allow_snap: bool = True,
    max_snap: int = 2,
) -> int:
    playable_set = frozenset(playable)
    if not playable_set:
        raise ValueError("empty playable pitch set")
    playable_sorted = sorted(playable_set)
    lo, hi = playable_sorted[0], playable_sorted[-1]
    # Score each candidate by pitch CLASS with multiplicity rather than by
    # walking every note: 2*search_range+1 transpositions x <=128 distinct
    # pitches instead of x note count (a 20k-note song went 240ms -> ~5ms).
    counts: Dict[int, int] = {}
    for n in notes:
        counts[n.pitch] = counts.get(n.pitch, 0) + 1

    best_t, best_key = 0, None
    for t in range(-search_range, search_range + 1):
        direct = dropped = 0
        for p, count in counts.items():
            action, _ = _classify(
                p + t, playable_set, lo, hi,
                allow_folding, allow_snap, max_snap, playable_sorted,
            )
            if action == "direct":
                direct += count
            elif action == "dropped":
                dropped += count
        # lexicographic: direct max -> dropped min -> |t| min -> t min
        key = (direct, -dropped, -abs(t), -t)
        if best_key is None or key > best_key:
            best_key, best_t = key, t
    return best_t


def transpose_notes(
    notes: List[NoteEvent],
    playable: Iterable[int],
    semitones: Union[int, str] = "auto",
    search_range: int = 24,
    allow_folding: bool = True,
    allow_snap: bool = True,
    max_snap: int = 2,
) -> Tuple[List[NoteEvent], TransposeReport]:
    """Apply global transposition (+ folding/snapping residue) to the song."""
    playable_set = frozenset(playable)
    if not playable_set:
        raise ValueError("empty playable pitch set")
    playable_sorted = sorted(playable_set)
    lo, hi = playable_sorted[0], playable_sorted[-1]

    if not notes:
        return [], TransposeReport(instrument_range=(lo, hi))

    if semitones == "auto":
        t = find_best_transpose(
            notes, playable_sorted, search_range,
            allow_folding, allow_snap, max_snap,
        )
    elif semitones == "none":
        t = 0
    else:
        t = int(semitones)

    report = TransposeReport(
        semitones=t,
        original_range=(min(n.pitch for n in notes), max(n.pitch for n in notes)),
        instrument_range=(lo, hi),
        total=len(notes),
    )
    out: List[NoteEvent] = []
    for n in notes:
        action, pitch = _classify(
            n.pitch + t, playable_set, lo, hi,
            allow_folding, allow_snap, max_snap, playable_sorted,
        )
        if action == "direct":
            report.direct += 1
        elif action == "folded":
            report.folded += 1
        elif action == "snapped":
            report.snapped += 1
        else:
            report.dropped += 1
            report.dropped_notes.append(n)
            continue
        out.append(
            NoteEvent(
                pitch=pitch,
                start=n.start,
                duration=n.duration,
                velocity=n.velocity,
                confidence=n.confidence,
            )
        )
    return sort_notes(out), report
