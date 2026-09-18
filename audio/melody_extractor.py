"""Unified audio -> NoteEvent interface and backend factory."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from music.note_event import NoteEvent


@dataclass
class ExtractionResult:
    notes: List[NoteEvent]
    backend: str
    contour_path: Optional[Path] = None     # F0 contour debug dump (if any)
    info: dict = field(default_factory=dict)


class AudioTranscriber(ABC):
    name = "abstract"

    @abstractmethod
    def transcribe(
        self, audio_path: Path, out_dir: Path, melody_cfg: dict
    ) -> ExtractionResult:
        """audio file -> ExtractionResult (notes + debug artifacts written
        into out_dir)."""


def get_backend(name: str, settings: Optional[dict] = None) -> AudioTranscriber:
    settings = settings or {}
    if name == "melodia":
        from audio.melodia_backend import MelodiaBackend
        return MelodiaBackend(settings.get("wsl", {}))
    if name == "basic_pitch":
        raise ValueError(
            "basic_pitch backend is planned but not implemented yet "
            "(see RESEARCH.md); use --backend melodia")
    raise ValueError(f"unknown audio backend: {name!r}")
