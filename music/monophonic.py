"""Monophonic enforcement: at any moment exactly ONE note may sound.

The game instrument is monophonic — one key + its modifier combination at a
time.  This stage runs AFTER transposition (so chord picks see the pitches
that will actually sound) and BEFORE mapping/event building:

  1. cluster notes that start within cluster_window_ms of each other
     (chords / near-simultaneous hits) and keep exactly one per cluster
     — by default the HIGHEST pitch (melody usually sits on top;
     selectable: highest / lowest / velocity)
  2. truncate each kept note's end to the next kept note's start
     (a new note cuts the previous one off — no overlapping holds)
  3. drop notes truncated to zero length

After this, build_events sees a strictly monophonic stream; its depth
counting remains as defense-in-depth, and the retrigger gap / legato
hand-off rules handle the physical transitions.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Tuple

from music.note_event import NoteEvent


@dataclass
class MonoInfo:
    input_notes: int = 0
    output_notes: int = 0
    chord_notes_removed: int = 0    # cluster members not chosen
    truncated_notes: int = 0        # ends shortened by the next note
    dropped_zero_length: int = 0

    def format(self) -> str:
        return (
            f"Monophonic: {self.output_notes}/{self.input_notes} notes kept, "
            f"{self.chord_notes_removed} chord notes removed, "
            f"{self.truncated_notes} truncated, "
            f"{self.dropped_zero_length} dropped (zero-length)"
        )


def _pick(cluster: List[NoteEvent], select: str) -> NoteEvent:
    if select == "lowest":
        return min(cluster, key=lambda n: (n.pitch, -n.velocity))
    if select == "velocity":
        return max(cluster, key=lambda n: (n.velocity, n.pitch))
    # default: highest pitch (melody on top); ties -> louder, then earlier
    return max(cluster, key=lambda n: (n.pitch, n.velocity, -n.start))


def make_monophonic(
    notes: List[NoteEvent],
    *,
    select: str = "highest",
    cluster_window_ms: float = 40.0,
) -> Tuple[List[NoteEvent], MonoInfo]:
    info = MonoInfo(input_notes=len(notes))
    if not notes:
        return [], info
    if select not in ("highest", "lowest", "velocity"):
        raise ValueError(f"unknown select strategy: {select!r}")

    window = cluster_window_ms / 1000.0
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))

    # 1) cluster + pick
    chosen: List[NoteEvent] = []
    i = 0
    while i < len(ordered):
        cluster_start = ordered[i].start
        j = i
        while j < len(ordered) and ordered[j].start - cluster_start <= window:
            j += 1
        cluster = ordered[i:j]
        chosen.append(replace(_pick(cluster, select)))
        info.chord_notes_removed += len(cluster) - 1
        i = j

    # 2) truncate overlaps: a note ends when the next one begins
    for k in range(len(chosen) - 1):
        a, b = chosen[k], chosen[k + 1]
        if a.end > b.start:
            a.duration = b.start - a.start
            info.truncated_notes += 1

    # 3) drop zero/negative length results
    final = [n for n in chosen if n.duration > 0]
    info.dropped_zero_length = len(chosen) - len(final)
    info.output_notes = len(final)
    return final, info
