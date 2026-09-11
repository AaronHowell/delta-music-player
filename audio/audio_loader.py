"""Audio file helpers: format detection and Windows -> WSL path conversion."""
from __future__ import annotations

import wave
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma",
                    ".opus", ".mp4", ".webm"}


def is_audio(path: str | Path) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def windows_to_wsl_path(path: str | Path) -> str:
    r"""C:\Users\me\song.mp3 -> /mnt/c/Users/me/song.mp3

    Only drive-letter paths are supported (project files and normal user
    paths).  UNC paths raise ValueError.
    """
    p = Path(path).resolve()
    s = str(p)
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        drive = s[0].lower()
        rest = s[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    raise ValueError(f"cannot convert path to WSL form: {p}")


def wav_duration(path: str | Path) -> float | None:
    """Duration of a PCM .wav without decoding anything else; None if not a
    readable wav."""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None
