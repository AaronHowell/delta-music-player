"""Time-region clipping of a note list (GUI selection / --clip)."""
from __future__ import annotations

from typing import List, Optional

from music.note_event import NoteEvent


def parse_clip_spec(spec: str) -> tuple[float, Optional[float]]:
    """'12.5-30' -> (12.5, 30.0);  '12.5' -> (12.5, None)  (to the end).
    Also accepts 'mm:ss' style like '1:05-1:30'."""
    def one(part: str) -> float:
        part = part.strip()
        if ":" in part:
            m, s = part.split(":", 1)
            return float(m) * 60 + float(s)
        return float(part)

    if "-" in spec:
        a, b = spec.split("-", 1)
        return one(a), one(b)
    return one(spec), None


def clip_notes(
    notes: List[NoteEvent],
    start: float,
    end: Optional[float] = None,
    *,
    rebase: bool = True,
) -> List[NoteEvent]:
    """Keep notes overlapping [start, end), trimmed to the region bounds.

    rebase=True shifts the result so the region start becomes t=0 (playback
    begins immediately instead of waiting through the original offset).
    Notes trimmed to zero length are dropped.
    """
    if start < 0:
        raise ValueError(f"clip start must be >= 0, got {start}")
    if end is not None and end <= start:
        raise ValueError(f"clip end ({end}) must be after start ({start})")
    limit = end if end is not None else float("inf")

    out: List[NoteEvent] = []
    for n in notes:
        if n.end <= start or n.start >= limit:
            continue                      # fully outside
        s = max(n.start, start)
        e = min(n.end, limit)
        if e - s <= 0:
            continue                      # trimmed away
        if rebase:
            s -= start
            e -= start
        out.append(
            NoteEvent(
                pitch=n.pitch,
                start=s,
                duration=e - s,
                velocity=n.velocity,
                confidence=n.confidence,
            )
        )
    return out
