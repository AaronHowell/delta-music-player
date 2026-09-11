#!/usr/bin/env python
"""Synthesize a test WAV with a KNOWN melody — the ground truth for testing
melody extraction (MELODIA / basic-pitch) without needing a real song.

Default melody: A4 C5 D5 (0.5 s each), 0.3 s silence, G4 (1.0 s),
pure sines with 10 ms fades (no clicks).

Usage:
    python tools/make_demo_wav.py [output.wav]
"""
from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

SR = 44100

# (midi pitch, seconds)  — None pitch = silence
SCORE = [
    (69, 0.5),    # A4
    (72, 0.5),    # C5
    (74, 0.5),    # D5
    (None, 0.3),  # silence
    (67, 1.0),    # G4
]


def midi_to_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def synth(score, sr=SR, amp=0.6, fade_ms=10.0):
    samples = []
    fade_n = int(sr * fade_ms / 1000)
    for pitch, dur in score:
        n = int(sr * dur)
        if pitch is None:
            samples.extend([0.0] * n)
            continue
        f = midi_to_hz(pitch)
        for i in range(n):
            env = amp
            if i < fade_n:
                env *= i / fade_n
            elif i > n - fade_n:
                env *= (n - i) / fade_n
            samples.append(env * math.sin(2 * math.pi * f * i / sr))
    return samples


def write_wav(path: Path, samples, sr=SR):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = struct.pack(
            f"<{len(samples)}h",
            *(max(-32767, min(32767, int(s * 32767))) for s in samples),
        )
        w.writeframes(frames)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "samples/demo_melody.wav")
    samples = synth(SCORE)
    write_wav(out, samples)
    total = len(samples) / SR
    print(f"wrote {out} ({total:.2f} s, {SR} Hz mono 16-bit)")
    print("ground truth notes:")
    t = 0.0
    for pitch, dur in SCORE:
        if pitch is not None:
            print(f"  {t:5.2f} - {t + dur:5.2f}  MIDI {pitch}")
        t += dur
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
