#!/usr/bin/env python
"""Probe MELODIA behavior on synthetic tones (dev tool).

Generates two WAVs — pure sines vs harmonic-rich tones — with the same
six pitches, runs the WSL MELODIA extractor on both, and prints per-note
detection results.  Tells us whether extraction failures come from the
timbre (pure sines lack harmonics) or the pipeline.

Usage:  python tools/melodia_probe.py
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audio.audio_loader import windows_to_wsl_path  # noqa: E402

SR = 44100
PITCHES = [("G4", 392.0), ("A4", 440.0), ("B4", 493.88),
           ("C5", 523.25), ("D5", 587.33), ("E5", 659.25)]
DUR = 1.0


def synth(freq, dur, harmonic=False, amp=0.5):
    n = int(SR * dur)
    fade = int(SR * 0.01)
    out = []
    for i in range(n):
        t = i / SR
        env = amp
        if i < fade:
            env *= i / fade
        elif i > n - fade:
            env *= (n - i) / fade
        s = math.sin(2 * math.pi * freq * t)
        if harmonic:
            s += 0.5 * math.sin(2 * math.pi * 2 * freq * t)
            s += 0.25 * math.sin(2 * math.pi * 3 * freq * t)
            s += 0.12 * math.sin(2 * math.pi * 4 * freq * t)
            s /= 1.87
        out.append(env * s)
    return out


def write_wav(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(struct.pack(
            f"<{len(samples)}h",
            *(max(-32767, min(32767, int(s * 32767))) for s in samples)))


def run_melodia(wav: Path, out_json: Path) -> dict:
    import shlex
    script = ROOT / "audio" / "wsl" / "melodia_extract.py"
    # ~ expands inside WSL bash; venv location configurable via env
    py = "~/dmp-melodia/bin/python"
    inner = " ".join(shlex.quote(x) if not x.startswith("~") else x for x in
                     [py, windows_to_wsl_path(script),
                      windows_to_wsl_path(wav), windows_to_wsl_path(out_json)])
    cmd = ["wsl", "-d", "Ubuntu-26.04", "bash", "-c", inner]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    if proc.returncode != 0:
        print(proc.stdout, proc.stderr)
        raise SystemExit(f"melodia failed with {proc.returncode}")
    return json.loads(out_json.read_text(encoding="utf-8"))


def report(name, contour):
    freq, conf, hop = contour["freq"], contour["conf"], contour["hop_seconds"]
    print(f"\n=== {name} ===")
    for k, (label, f0) in enumerate(PITCHES):
        lo = int(k * DUR / hop)
        hi = int((k + 1) * DUR / hop)
        seg_f = freq[lo:hi]
        seg_c = conf[lo:hi]
        voiced = [f for f in seg_f if f > 0]
        if voiced:
            avg = sum(voiced) / len(voiced)
            midi = 69 + 12 * math.log2(avg / 440)
            print(f"  {label} ({f0:6.1f}Hz): voiced {len(voiced)}/{len(seg_f)}"
                  f"  avg={avg:6.1f}Hz (midi {midi:5.2f})  "
                  f"avgconf={sum(seg_c)/len(seg_c):.2f}")
        else:
            print(f"  {label} ({f0:6.1f}Hz): ALL UNVOICED  "
                  f"avgconf={sum(seg_c)/len(seg_c):.2f}")


def main():
    for harmonic in (False, True):
        tag = "harm" if harmonic else "pure"
        samples = []
        for _label, f0 in PITCHES:
            samples.extend(synth(f0, DUR, harmonic=harmonic))
        wav = ROOT / "probes" / f"probe_{tag}.wav"
        write_wav(wav, samples)
        out = ROOT / "probes" / f"probe_{tag}_contour.json"
        contour = run_melodia(wav, out)
        report(f"{tag} tones", contour)


if __name__ == "__main__":
    main()
