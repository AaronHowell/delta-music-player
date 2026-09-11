"""MIDI audition via the Windows MCI sequencer.

Plays a .mid file through the built-in Microsoft GS Wavetable Synth (the
GM soundfont shipped with every Windows — program 0 is a piano), so the
user can LISTEN to a track/region while choosing what to perform.

Zero dependencies: ctypes -> winmm.dll MCI commands.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

if sys.platform == "win32":
    _winmm = ctypes.windll.winmm
    _winmm.mciSendStringW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HWND]
    _winmm.mciSendStringW.restype = wintypes.UINT
    _winmm.mciGetErrorStringW.argtypes = [
        wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    _winmm.mciGetErrorStringW.restype = wintypes.BOOL
else:
    _winmm = None


class AuditionError(RuntimeError):
    pass


class MidiAuditioner:
    """Open/play/stop one MIDI file at a time under a fixed MCI alias."""

    ALIAS = "dmp_audition"

    def __init__(self):
        self._open = False

    # ------------------------------------------------------------------
    def _send(self, cmd: str) -> str:
        if _winmm is None:
            raise AuditionError("audition is only available on Windows")
        buf = ctypes.create_unicode_buffer(512)
        err = _winmm.mciSendStringW(cmd, buf, 512, None)
        if err:
            ebuf = ctypes.create_unicode_buffer(256)
            _winmm.mciGetErrorStringW(err, ebuf, 256)
            raise AuditionError(
                f"MCI error {err}: {ebuf.value or '(no description)'} "
                f"[cmd: {cmd}]"
            )
        return buf.value

    def _try(self, cmd: str) -> None:
        try:
            self._send(cmd)
        except AuditionError:
            pass

    # ------------------------------------------------------------------
    def play(self, midi_path: str | Path) -> None:
        """(Re)start playback of a MIDI file. Stops any current audition."""
        self.stop()
        p = Path(midi_path).resolve()
        if not p.exists():
            raise AuditionError(f"audition file not found: {p}")
        self._send(f'open "{p}" type sequencer alias {self.ALIAS}')
        self._open = True
        self._send(f"set {self.ALIAS} time format milliseconds")
        self._send(f"play {self.ALIAS}")

    def stop(self) -> None:
        if self._open:
            self._try(f"stop {self.ALIAS}")
            self._try(f"close {self.ALIAS}")
            self._open = False

    def position_ms(self) -> int | None:
        if not self._open:
            return None
        try:
            return int(self._send(f"status {self.ALIAS} position"))
        except (AuditionError, ValueError):
            return None

    def is_playing(self) -> bool:
        if not self._open:
            return False
        try:
            return self._send(f"status {self.ALIAS} mode") == "playing"
        except AuditionError:
            return False
