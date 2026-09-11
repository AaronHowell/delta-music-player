"""F0 contour -> NoteEvent[].

Pipeline (every parameter configurable via settings.json -> "melody"):

    Hz -> MIDI float -> median filter -> hysteresis segmentation
       -> min-duration filter -> merge adjacent same-pitch notes

Handles the known problems of raw pitch-tracker output:
  * pitch jitter / vibrato    -> median filter + hysteresis threshold
                                 (a note only changes when the contour moves
                                 more than pitch_change_threshold semitones
                                 away from the current note pitch)
  * brief wrong pitches       -> min_note_duration filter; the hole left
                                 behind is closed by the merge pass when both
                                 sides have the same pitch
  * silent / unvoiced regions -> frames with freq <= 0 or confidence below
                                 confidence_threshold become None; short None
                                 runs (<= merge_gap) are bridged inside the
                                 current note instead of splitting it
  * glissando                 -> produces a chain of short ascending/
                                 descending notes (honest: the game cannot
                                 bend pitch either)
  * repeated adjacent notes   -> audio has no re-articulation information;
                                 a sustained same-pitch contour merges into
                                 one note (expected behavior)

Pure stdlib + NoteEvent/pitch_utils — no numpy required, fully testable.
Frame i covers the time span [i*hop, (i+1)*hop).
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from music.note_event import NoteEvent, sort_notes
from music.pitch_utils import hz_to_midi


def hz_contour_to_midi_float(
    freqs: Sequence[float],
    confs: Sequence[float],
    confidence_threshold: float,
) -> List[Optional[float]]:
    """Hz + confidence -> MIDI-float contour; unvoiced frames become None."""
    out: List[Optional[float]] = []
    for f, c in zip(freqs, confs):
        if f is not None and f > 0 and (c is None or c >= confidence_threshold):
            out.append(hz_to_midi(f))
        else:
            out.append(None)
    return out


def median_filter(
    values: Sequence[Optional[float]], window: int
) -> List[Optional[float]]:
    """Edge-aware median over non-None values in the window."""
    if window <= 1:
        return list(values)
    half = window // 2
    n = len(values)
    out: List[Optional[float]] = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        vals = sorted(v for v in values[lo:hi] if v is not None)
        out.append(vals[len(vals) // 2] if vals else None)
    return out


def contour_to_notes(
    freqs: Sequence[float],
    confs: Sequence[float],
    hop_seconds: float,
    *,
    min_note_duration: float = 0.08,
    pitch_smoothing_window: int = 5,
    confidence_threshold: float = 0.5,
    merge_gap: float = 0.05,
    pitch_change_threshold: float = 0.6,
) -> List[NoteEvent]:
    if hop_seconds <= 0:
        raise ValueError("hop_seconds must be > 0")
    if len(freqs) != len(confs):
        raise ValueError("freqs and confs must have the same length")

    midi_floats = median_filter(
        hz_contour_to_midi_float(freqs, confs, confidence_threshold),
        pitch_smoothing_window,
    )

    # ---- hysteresis segmentation -------------------------------------
    raw: List[dict] = []
    cur_pitch: Optional[int] = None
    cur_start = 0
    cur_end = -1          # index of last voiced frame (inclusive)
    cur_conf_sum = 0.0
    cur_conf_n = 0
    gap_run = 0

    def close() -> None:
        nonlocal cur_pitch, cur_conf_sum, cur_conf_n
        if cur_pitch is not None and cur_end >= cur_start:
            raw.append({
                "pitch": cur_pitch,
                "start": cur_start * hop_seconds,
                "end": (cur_end + 1) * hop_seconds,
                "conf": cur_conf_sum / cur_conf_n if cur_conf_n else 1.0,
            })
        cur_pitch = None
        cur_conf_sum = 0.0
        cur_conf_n = 0

    for i, mf in enumerate(midi_floats):
        if mf is None:
            if cur_pitch is not None:
                gap_run += 1
                if gap_run * hop_seconds > merge_gap:
                    close()
                    gap_run = 0
            continue
        gap_run = 0
        cand = int(round(mf))
        conf = confs[i] if confs[i] is not None else 1.0
        if cur_pitch is None:
            cur_pitch = cand
            cur_start = i
            cur_end = i
            cur_conf_sum = conf
            cur_conf_n = 1
        elif abs(mf - cur_pitch) < pitch_change_threshold:
            # jitter / vibrato / small deviation: stays the same note
            cur_end = i
            cur_conf_sum += conf
            cur_conf_n += 1
        else:
            # real pitch change: close at the last voiced frame
            close()
            cur_pitch = cand
            cur_start = i
            cur_end = i
            cur_conf_sum = conf
            cur_conf_n = 1
    close()

    # ---- min-duration filter ------------------------------------------
    kept = [r for r in raw if (r["end"] - r["start"]) >= min_note_duration]

    # ---- merge adjacent same-pitch notes across small gaps ------------
    merged: List[dict] = []
    for r in kept:
        if (
            merged
            and merged[-1]["pitch"] == r["pitch"]
            and (r["start"] - merged[-1]["end"]) <= merge_gap
        ):
            prev = merged[-1]
            total = (prev["end"] - prev["start"]) + (r["end"] - r["start"])
            prev["conf"] = (
                prev["conf"] * (prev["end"] - prev["start"])
                + r["conf"] * (r["end"] - r["start"])
            ) / total if total > 0 else r["conf"]
            prev["end"] = r["end"]
        else:
            merged.append(dict(r))

    notes = [
        NoteEvent(
            pitch=r["pitch"],
            start=r["start"],
            duration=r["end"] - r["start"],
            velocity=max(1, min(127, int(r["conf"] * 127))),
            confidence=min(1.0, r["conf"]),
        )
        for r in merged
    ]
    return sort_notes(notes)
