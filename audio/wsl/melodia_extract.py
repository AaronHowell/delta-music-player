#!/usr/bin/env python3
"""Runs INSIDE WSL (needs essentia, which has no Windows wheels).

audio file -> Essentia PredominantPitchMelodia -> F0 contour JSON.

Usage:
    ~/dmp-melodia/bin/python melodia_extract.py AUDIO OUT.json \
        [--hop 128] [--frame 2048] [--sr 44100]

Output JSON:
    {"backend": "melodia", "sample_rate": ..., "hop_size": ...,
     "hop_seconds": ..., "n_frames": N, "audio_seconds": ...,
     "extract_seconds": ..., "freq": [...Hz, 0/neg = unvoiced...],
     "conf": [...0..1...]}

All note segmentation/quantization happens on the Windows side
(music/quantizer.py) so parameters stay testable and tunable there.
"""
from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("out")
    ap.add_argument("--hop", type=int, default=128)
    ap.add_argument("--frame", type=int, default=2048)
    ap.add_argument("--sr", type=int, default=44100)
    args = ap.parse_args()

    try:
        import essentia.standard as es
    except ImportError as e:
        print(f"[melodia-wsl] essentia not available: {e}", file=sys.stderr)
        print("[melodia-wsl] run audio/wsl/setup_wsl_offline.sh first",
              file=sys.stderr)
        return 3

    t0 = time.time()
    print(f"[melodia-wsl] loading {args.audio}", flush=True)
    try:
        audio = es.MonoLoader(filename=args.audio, sampleRate=args.sr)()
    except Exception as e:
        print(f"[melodia-wsl] failed to load audio: {e}", file=sys.stderr)
        return 4
    dur = len(audio) / args.sr
    print(f"[melodia-wsl] {dur:.1f}s of audio; extracting F0 "
          f"(hop={args.hop}, frame={args.frame}, sr={args.sr})...", flush=True)

    melodia = es.PredominantPitchMelodia(
        frameSize=args.frame, hopSize=args.hop, sampleRate=args.sr
    )
    pitch, confidence = melodia(audio)

    data = {
        "backend": "melodia",
        "sample_rate": args.sr,
        "hop_size": args.hop,
        "hop_seconds": args.hop / args.sr,
        "n_frames": int(len(pitch)),
        "audio_seconds": dur,
        "extract_seconds": time.time() - t0,
        "freq": [round(float(f), 3) for f in pitch],
        "conf": [round(float(c), 4) for c in confidence],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"[melodia-wsl] wrote {args.out}: {data['n_frames']} frames "
          f"in {data['extract_seconds']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
