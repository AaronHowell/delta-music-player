"""MELODIA backend: runs Essentia's PredominantPitchMelodia inside WSL
(essentia has no Windows wheels) and quantizes the returned F0 contour on
the Windows side with music/quantizer.py.

The WSL side is deliberately minimal (audio -> contour JSON); every tunable
post-processing parameter lives in the main project.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from audio.audio_loader import windows_to_wsl_path
from audio.melody_extractor import AudioTranscriber, ExtractionResult
from music.quantizer import contour_to_notes

SETUP_HINT = (
    "MELODIA runs inside WSL because essentia has no Windows wheels.\n"
    "One-time setup (from the project directory, in PowerShell):\n"
    "  1) python audio\\wsl\\fetch_wsl_wheels.py wsl_wheels\n"
    "  2) ((Get-Content audio\\wsl\\setup_wsl_offline.sh -Raw) -replace \"`r\",\"\") "
    "| wsl -d Ubuntu-26.04 -u root bash\n"
    "Adjust 'wsl' settings in config/settings.json if your distro/venv "
    "differ."
)


class MelodiaBackend(AudioTranscriber):
    name = "melodia"

    def __init__(self, wsl_cfg: Optional[dict] = None):
        cfg = wsl_cfg or {}
        self.distro: str = cfg.get("distro", "Ubuntu-26.04")
        self.python: str = cfg.get("python", "~/dmp-melodia/bin/python")
        self.enabled: bool = bool(cfg.get("enabled", True))
        self.timeout_s: float = float(cfg.get("timeout_seconds", 1800))

    # ------------------------------------------------------------------
    def _wsl_home(self) -> str:
        """$HOME inside the distro (for expanding '~/...' python paths —
        shlex.quoted '~' would not be expanded by bash)."""
        try:
            proc = subprocess.run(
                ["wsl", "-d", self.distro, "--", "bash", "-c", "echo $HOME"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
            return proc.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return ""

    def _resolve_python(self) -> str:
        py = self.python
        if py.startswith("~/"):
            home = self._wsl_home()
            if home:
                return home + py[1:]
        return py

    def transcribe(
        self, audio_path: Path, out_dir: Path, melody_cfg: dict
    ) -> ExtractionResult:
        if not self.enabled:
            raise RuntimeError(
                "melodia backend is disabled in settings.json (wsl.enabled)"
            )
        if shutil.which("wsl") is None:
            raise RuntimeError("WSL not found on this system.\n" + SETUP_HINT)

        script = Path(__file__).resolve().parent / "wsl" / "melodia_extract.py"
        out_dir.mkdir(parents=True, exist_ok=True)
        contour_path = out_dir / f"{Path(audio_path).stem}_contour.json"

        inner = " ".join(
            shlex.quote(part)
            for part in [
                self._resolve_python(),
                windows_to_wsl_path(script),
                windows_to_wsl_path(audio_path),
                windows_to_wsl_path(contour_path),
            ]
        )
        hop = melody_cfg.get("melodia_hop_size")
        if hop:
            inner += f" --hop {int(hop)}"

        cmd = ["wsl", "-d", self.distro, "bash", "-c", inner]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"MELODIA extraction timed out after {self.timeout_s}s"
            ) from None

        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.returncode != 0:
            raise RuntimeError(
                f"WSL MELODIA extraction failed (exit {proc.returncode}):\n"
                f"{(proc.stderr or '').rstrip()}\n\n{SETUP_HINT}"
            )

        with open(contour_path, "r", encoding="utf-8") as f:
            contour = json.load(f)

        notes = contour_to_notes(
            contour["freq"],
            contour["conf"],
            contour["hop_seconds"],
            min_note_duration=float(
                melody_cfg.get("min_note_duration", 0.08)),
            pitch_smoothing_window=int(
                melody_cfg.get("pitch_smoothing_window", 5)),
            confidence_threshold=float(
                melody_cfg.get("confidence_threshold", 0.5)),
            merge_gap=float(melody_cfg.get("merge_gap", 0.05)),
            pitch_change_threshold=float(
                melody_cfg.get("pitch_change_threshold", 0.6)),
        )
        return ExtractionResult(
            notes=notes,
            backend=self.name,
            contour_path=contour_path,
            info={
                "n_frames": contour.get("n_frames"),
                "audio_seconds": contour.get("audio_seconds"),
                "extract_seconds": contour.get("extract_seconds"),
            },
        )
