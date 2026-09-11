"""Input backends: abstraction over "press / release one physical input".

The scheduler only emits identifiers like "z", ",", "mouse_middle"; backends
turn them into real events (Win32 SendInput) or console output (dry run).
"""
from __future__ import annotations

import time
from typing import Callable, List, Tuple


class InputBackend:
    """Interface: down/up primitives.  Holding a note = caller keeps the
    identifier down until its release event (long notes supported)."""

    def down(self, identifier: str) -> None:
        raise NotImplementedError

    def up(self, identifier: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources (and any keys still held, if tracked)."""


class DryRunBackend(InputBackend):
    """Records events and optionally prints them; never touches the OS."""

    def __init__(self, verbose: bool = True,
                 clock: Callable[[], float] = time.perf_counter):
        self.verbose = verbose
        self._clock = clock
        self.events: List[Tuple[float, str, str]] = []  # (t, action, id)

    def down(self, identifier: str) -> None:
        self._record("down", identifier)

    def up(self, identifier: str) -> None:
        self._record("up", identifier)

    def _record(self, action: str, identifier: str) -> None:
        t = self._clock()
        self.events.append((t, action, identifier))
        if self.verbose:
            print(f"  [dry] {action:4s} {identifier}")


class Win32SendInputBackend(InputBackend):
    """Real input via Win32 SendInput (scancode or VK keyboard + mouse)."""

    def __init__(self, keyboard_mode: str | None = None):
        # lazy import so the module loads on non-Windows for tests
        from playback import win32_sendinput as w32
        if keyboard_mode:
            w32.set_keyboard_mode(keyboard_mode)
        self._w32 = w32
        # dict-as-ordered-set: release order is reverse press order
        self._held: dict[str, None] = {}

    def down(self, identifier: str) -> None:
        self._w32.input_down(identifier)
        self._held[identifier] = None

    def up(self, identifier: str) -> None:
        self._w32.input_up(identifier)
        self._held.pop(identifier, None)

    def close(self) -> None:
        # never leave a key stuck down, even on errors (MeowField lesson);
        # release in reverse press order, like unwinding a stack
        for identifier in reversed(list(self._held)):
            try:
                self._w32.input_up(identifier)
            except Exception:
                pass
        self._held.clear()


def make_backend(dry_run: bool) -> InputBackend:
    return DryRunBackend() if dry_run else Win32SendInputBackend()
