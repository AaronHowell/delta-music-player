#!/usr/bin/env python
"""delta_music_player CLI.

Phase 1: MIDI -> NoteEvent[] -> listing + 乐库/<stem>_notes.json
Phase 2: + InstrumentProfile / auto-transpose / key-combination mapping
Later phases add: SendInput playback (3/4), audio melody extraction (5-7).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# make sibling packages importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from instrument.instrument_profile import InstrumentProfile
from instrument.mapper import InstrumentMapper
from midi.midi_loader import is_midi, load_midi
from midi.midi_parser import parse_midi
from music.note_event import summarize
from music.transposer import transpose_notes

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="app.py",
        description="Song -> main melody -> game keys -> auto performance "
                    "(Delta Force instrument)",
    )
    p.add_argument("input", help="path to .mid / .mp3 / .wav / .flac")
    p.add_argument("--backend", choices=["melodia"],
                   default="melodia",
                   help="audio melody-extraction backend (audio input only)")
    p.add_argument("--instrument", default="config/instrument.json",
                   help="instrument profile JSON")
    p.add_argument("--settings", default="config/settings.json",
                   help="settings JSON")
    p.add_argument("--transpose", default="auto",
                   help="'auto', 'none' or an integer semitone shift")
    p.add_argument("--track", type=int, default=None,
                   help="MIDI: parse only this track (default: auto-select "
                        "melody track)")
    p.add_argument("--no-auto-track", action="store_true",
                   help="MIDI: merge all tracks instead of auto-selecting one")
    p.add_argument("--start-delay", type=float, default=None,
                   help="countdown seconds before playback (default from "
                        "settings.json)")
    p.add_argument("--clip", default=None,
                   help="play only a time region, e.g. '12.5-30', '1:05-1:30' "
                        "or '12.5' (to the end); region is rebased to 0")
    p.add_argument("--window", default=None,
                   help="title keyword of the game window to activate before "
                        "playing (default: auto-detect via settings.json "
                        "target_window; 'none' disables)")
    p.add_argument("--keyboard-mode", choices=["scancode", "vk"], default=None,
                   help="keyboard event flavor (default from settings.json; "
                        "try 'vk' if the game ignores scancode input)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the note/key schedule without sending input")
    p.add_argument("--simulate", action="store_true",
                   help="run the real playback loop in real time through a "
                        "dry-run backend (verifies scheduling/timing without "
                        "sending any keys)")
    p.add_argument("--output-dir", default=None,
                   help="directory for *_notes.json / *_melody.mid "
                        "(default: <settings.output_dir>)")
    return p


def load_settings(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = load_settings(args.settings)

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Input file not found: {in_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.output_dir or settings.get("output_dir", "乐库"))
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- load notes ----------------
    from_audio = False
    dropped_info = 0
    if is_midi(in_path):
        print(f"Loaded {in_path}")
        mid = load_midi(in_path)
        result = parse_midi(
            mid, track=args.track, auto_track=not args.no_auto_track
        )
        if result.track_stats:
            print("Track scores (auto melody selection):")
            for st in result.track_stats:
                mark = " *" if st["track"] == result.track_index else "  "
                print(f"  {mark} track {st['track']}: notes={st['notes']} "
                      f"mean_pitch={st['mean_pitch']} "
                      f"mono={st['mono_ratio']} score={st['score']}")
        notes = result.notes
        dropped_info = result.dropped_zero_length
    elif in_path.suffix.lower() in AUDIO_EXTENSIONS:
        from audio.melody_extractor import get_backend

        from_audio = True
        print(f"Loaded {in_path}")
        print(f"Extracting melody (backend: {args.backend})...")
        try:
            backend = get_backend(args.backend, settings)
            extraction = backend.transcribe(
                in_path, out_dir, settings.get("melody", {})
            )
        except Exception as e:
            print(f"Melody extraction failed:\n{e}", file=sys.stderr)
            return 5
        notes = extraction.notes
        print(f"  frames: {extraction.info.get('n_frames')}  "
              f"extraction time: "
              f"{extraction.info.get('extract_seconds', 0):.1f}s")
        if extraction.contour_path:
            print(f"  contour: {extraction.contour_path}")
        if not notes:
            print("No melody detected in the audio.", file=sys.stderr)
            return 4
    else:
        print(f"Unsupported input format: {in_path.suffix}", file=sys.stderr)
        return 2

    # ---------------- optional time-region clip ----------------
    if args.clip:
        from music.clip import clip_notes, parse_clip_spec
        try:
            c_start, c_end = parse_clip_spec(args.clip)
            before = len(notes)
            notes = clip_notes(notes, c_start, c_end, rebase=True)
        except ValueError as e:
            print(f"--clip 无效 ({args.clip}): {e}\n"
                  f"示例: --clip 12.5-30 或 --clip 1:05-1:30 或 --clip 12.5",
                  file=sys.stderr)
            return 2
        end_desc = f"{c_end:.2f}s" if c_end is not None else "结尾"
        print(f"Clip: {c_start:.2f}s - {end_desc}  "
              f"({len(notes)}/{before} notes, 已平移回 0)")
        if not notes:
            print("选中区间内没有音符。", file=sys.stderr)
            return 4

    summary = summarize(notes)
    if summary["count"] == 0:
        print("No notes found in input.", file=sys.stderr)
        return 4
    print(f"\nDetected notes: {summary['count']}")
    print(f"Range: {summary['lowest_note']} - {summary['highest_note']} "
          f"(MIDI {summary['lowest_pitch']}-{summary['highest_pitch']})")
    print(f"Duration: {summary['total_duration']:.2f} s")
    if dropped_info:
        print(f"Dropped zero-length notes: {dropped_info}")

    # ---------------- instrument profile ----------------
    profile = InstrumentProfile.load(args.instrument)
    print()
    print(profile.describe())

    # ---------------- transposition ----------------
    tcfg = settings.get("transposer", {})
    transposed, report = transpose_notes(
        notes,
        profile.playable_pitches(),
        semitones=args.transpose,
        search_range=int(tcfg.get("search_range", 24)),
        allow_folding=bool(tcfg.get("allow_octave_folding", True)),
        allow_snap=bool(tcfg.get("allow_nearest_snap", True)),
        max_snap=int(tcfg.get("max_snap_semitones", 2)),
    )
    print()
    print(report.format())

    # ---------------- monophonic enforcement ----------------
    # the game instrument sounds exactly one note at a time: chords collapse
    # to a single note and overlaps truncate the previous note
    mcfg = settings.get("monophonic", {})
    if bool(mcfg.get("enabled", True)):
        from music.monophonic import make_monophonic
        transposed, mono_info = make_monophonic(
            transposed,
            select=str(mcfg.get("select", "highest")),
            cluster_window_ms=float(mcfg.get("cluster_window_ms", 40)),
        )
        print(mono_info.format())

    # ---------------- mapping ----------------
    mapper = InstrumentMapper(profile)
    mapped, unmapped = mapper.map_notes(transposed)
    if unmapped:
        print(f"\nWARNING: {len(unmapped)} notes could not be mapped and will "
              f"be skipped (this should not happen after transposition).")

    # ---------------- export notes json ----------------
    notes_path = out_dir / f"{in_path.stem}_notes.json"
    rows = []
    for m in mapped:
        combo = m.combo
        physical = [profile.modifiers[mod].input for mod in combo.modifiers]
        physical.append(combo.key)
        rows.append({
            "time": round(m.note.start, 6),
            "duration": round(m.note.duration, 6),
            "pitch": m.note.pitch,
            "note": m.note.name,
            "input": physical,
            "combo": combo.display(),
            "velocity": m.note.velocity,
            "confidence": round(m.note.confidence, 4),
        })
    with open(notes_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {notes_path}")

    # ---------------- melody MIDI export (audio sources) ----------------
    if from_audio:
        from midi.midi_writer import write_midi
        mid_out = out_dir / f"{in_path.stem}_melody.mid"
        write_midi(notes, mid_out)   # original extraction, pre-transpose
        print(f"Wrote {mid_out}")

    # ---------------- dry run ----------------
    print("\nDry run schedule:")
    for m in mapped:
        print(f"{m.note.start:8.3f}  {m.note.name:4s} -> {m.combo.display()}"
              f"   (dur={m.note.duration:.3f})")

    if args.dry_run:
        return 0

    # ---------------- real playback ----------------
    import sys as _sys
    from playback.input_backend import DryRunBackend, Win32SendInputBackend
    from playback.player import Player
    from playback.window_target import (
        find_game_window, is_admin, list_top_windows)

    keyboard_mode = args.keyboard_mode or str(
        settings.get("playback", {}).get("keyboard_mode", "scancode"))

    # ---- target window ----
    target = None
    twcfg = settings.get("target_window", {})
    auto_fg = bool(twcfg.get("auto_foreground", True))
    if args.window and args.window.lower() != "none":
        cands = list_top_windows(title_keywords=[args.window])
        target = cands[0] if cands else None
        if target is None:
            print(f"WARNING: no window matching {args.window!r} found",
                  file=_sys.stderr)
    elif args.window is None:
        target = find_game_window(
            twcfg.get("title_keywords", []),
            twcfg.get("process_names", []))
    if target:
        print(f"Target window: {target.label()}")
        if target.elevated and not is_admin():
            print("=" * 62, file=_sys.stderr)
            print("WARNING: 游戏以管理员权限运行，而本程序没有。",
                  file=_sys.stderr)
            print("Windows UIPI 会静默丢弃输入 —— 请以管理员身份重跑本程序！",
                  file=_sys.stderr)
            print("(PowerShell: 右键以管理员运行，或 GUI 里点'以管理员重启')",
                  file=_sys.stderr)
            print("=" * 62, file=_sys.stderr)

    if args.simulate:
        print("\n[simulate] real-time playback through the dry-run backend "
              "(no keys will be sent)")
        backend = DryRunBackend(verbose=False)
    else:
        backend = Win32SendInputBackend(keyboard_mode=keyboard_mode)
        print(f"\nKeyboard mode: {keyboard_mode}")
        print("Switch to the game window NOW — make sure the instrument is")
        print("equipped and the game will receive keystrokes.")
    player = Player(backend, settings)
    stats = player.play(
        mapped, profile, start_delay=args.start_delay,
        target_hwnd=target.hwnd if target else None,
        auto_foreground=auto_fg and not args.simulate,
    )
    print()
    print(stats.format())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
