"""Tests for the Win32 SendInput backend and backend abstraction.

The real SendInput call (_send) is monkeypatched so tests never emit actual
keystrokes.  We assert on the INPUT structures that *would* be sent: scancodes,
flags, mouse buttons, and down/up separation.
"""
import sys

import pytest

from playback import win32_sendinput as w32
from playback.input_backend import DryRunBackend, Win32SendInputBackend

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Win32 SendInput is Windows-only"
)


@pytest.fixture
def sent(monkeypatch):
    """Capture INPUT structs instead of calling the real SendInput."""
    captured = []

    def fake_send(event):
        # snapshot the relevant union member
        if event.type == w32.INPUT_KEYBOARD:
            ki = event.u.ki
            captured.append(("key", ki.wVk, ki.wScan, ki.dwFlags))
        else:
            mi = event.u.mi
            captured.append(("mouse", mi.dx, mi.dy, mi.dwFlags))

    monkeypatch.setattr(w32, "_send", fake_send)
    return captured


class TestKeyboard:
    def test_key_down_uses_scancode_not_vk(self, sent):
        w32.key_down("z")
        kind, vk, scan, flags = sent[0]
        assert kind == "key"
        assert vk == 0                       # wVk must be 0 for scancode mode
        assert scan == 0x2C                  # 'z' Set-1 make code
        assert flags & w32.KEYEVENTF_SCANCODE
        assert not (flags & w32.KEYEVENTF_KEYUP)

    def test_key_up_sets_keyup_flag(self, sent):
        w32.key_up("z")
        _, _, scan, flags = sent[0]
        assert scan == 0x2C
        assert flags & w32.KEYEVENTF_SCANCODE
        assert flags & w32.KEYEVENTF_KEYUP

    def test_down_up_are_separate_events(self, sent):
        w32.key_down("c")
        w32.key_up("c")
        assert len(sent) == 2
        assert sent[0][3] & w32.KEYEVENTF_SCANCODE and not sent[0][3] & w32.KEYEVENTF_KEYUP
        assert sent[1][3] & w32.KEYEVENTF_KEYUP

    def test_instrument_keys_have_scancodes(self, sent):
        # Z X C V B N M ,  — the base note row
        for ch in "zxcvbnm,":
            w32.key_down(ch)
        scans = [e[2] for e in sent]
        assert scans == [0x2C, 0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x32, 0x33]

    def test_case_insensitive(self, sent):
        w32.key_down("Z")
        assert sent[0][2] == 0x2C

    def test_extended_key_flag(self, sent):
        w32.key_down("up")
        assert sent[0][3] & w32.KEYEVENTF_EXTENDEDKEY

    def test_unsupported_key_raises(self, sent):
        with pytest.raises(ValueError):
            w32.key_down("not_a_key")


class TestMouse:
    def test_left_button(self, sent):
        w32.mouse_down("mouse_left")
        w32.mouse_up("mouse_left")
        assert sent[0][0] == "mouse"
        assert sent[0][3] == w32.MOUSEEVENTF_LEFTDOWN
        assert sent[1][3] == w32.MOUSEEVENTF_LEFTUP

    def test_middle_button(self, sent):
        w32.mouse_down("mouse_middle")
        assert sent[0][3] == w32.MOUSEEVENTF_MIDDLEDOWN

    def test_right_button(self, sent):
        w32.mouse_down("mouse_right")
        assert sent[0][3] == w32.MOUSEEVENTF_RIGHTDOWN

    def test_unsupported_button_raises(self, sent):
        with pytest.raises(ValueError):
            w32.mouse_down("mouse_side")


class TestUnifiedDispatch:
    def test_input_down_routes_mouse_vs_keyboard(self, sent):
        w32.input_down("mouse_left")
        w32.input_down("x")
        assert sent[0][0] == "mouse"
        assert sent[1][0] == "key"
        assert sent[1][2] == 0x2D

    def test_is_mouse(self):
        assert w32.is_mouse("mouse_left")
        assert not w32.is_mouse("z")


class TestSendFailure:
    def test_send_failure_raises(self, monkeypatch):
        # real _send but a SendInput that reports 0 inserted (e.g. UIPI block)
        monkeypatch.setattr(w32, "_SendInput", lambda *a, **k: 0)
        with pytest.raises(w32.SendInputError):
            w32._send(w32.INPUT())


class TestBackendAbstraction:
    def test_dry_run_backend_records(self):
        backend = DryRunBackend(verbose=False, clock=lambda: 1.0)
        backend.down("z")
        backend.up("z")
        assert backend.events == [(1.0, "down", "z"), (1.0, "up", "z")]

    def test_win32_backend_delegates_and_tracks_held(self, sent):
        backend = Win32SendInputBackend()
        backend.down("z")
        backend.down("mouse_left")
        assert sent[0][0] == "key" and sent[1][0] == "mouse"
        # close() releases everything still held, in reverse press order
        backend.close()
        kinds = [e[0] for e in sent]
        assert kinds == ["key", "mouse", "mouse", "key"]  # 2 downs + 2 ups
        assert sent[2][3] == w32.MOUSEEVENTF_LEFTUP
        assert sent[3][3] & w32.KEYEVENTF_KEYUP

    def test_win32_backend_close_idempotent(self, sent):
        backend = Win32SendInputBackend()
        backend.down("z")
        backend.up("z")
        backend.close()   # nothing held -> no extra events
        assert len(sent) == 2
