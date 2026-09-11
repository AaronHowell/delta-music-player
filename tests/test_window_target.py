"""Tests for the VK keyboard mode and window-target utilities."""
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Win32-only module")

from playback import win32_sendinput as w32
from playback import window_target as wt


class TestVkMode:
    def test_resolve_vk_letters(self):
        assert w32.resolve_vk("z") == 0x5A
        assert w32.resolve_vk("Z") == 0x5A
        assert w32.resolve_vk("x") == 0x58

    def test_resolve_vk_punctuation(self):
        assert w32.resolve_vk(",") == 0xBC     # VK_OEM_COMMA

    def test_resolve_vk_named(self):
        assert w32.resolve_vk("space") == 0x20
        assert w32.resolve_vk("f13") == 0x7C

    def test_resolve_vk_unknown_raises(self):
        with pytest.raises(ValueError):
            w32.resolve_vk("not_a_key")

    def test_vk_mode_events(self, monkeypatch):
        captured = []
        monkeypatch.setattr(w32, "_send", lambda ev: captured.append(ev))
        monkeypatch.setattr(w32, "KEYBOARD_MODE", "vk")
        w32.key_down("z")
        w32.key_up("z")
        down, up = captured
        assert down.type == w32.INPUT_KEYBOARD
        assert down.u.ki.wVk == 0x5A           # DF-Auto style: VK in wVk
        assert down.u.ki.wScan == 0
        assert down.u.ki.dwFlags == 0          # no SCANCODE flag
        assert up.u.ki.dwFlags & w32.KEYEVENTF_KEYUP
        assert not (up.u.ki.dwFlags & w32.KEYEVENTF_SCANCODE)

    def test_set_keyboard_mode_validates(self):
        old = w32.KEYBOARD_MODE
        try:
            w32.set_keyboard_mode("vk")
            assert w32.KEYBOARD_MODE == "vk"
            w32.set_keyboard_mode("scancode")
            with pytest.raises(ValueError):
                w32.set_keyboard_mode("magic")
        finally:
            w32.set_keyboard_mode(old)


class TestWindowTarget:
    def test_is_admin_returns_bool(self):
        assert isinstance(wt.is_admin(), bool)

    def test_list_top_windows(self):
        ws = wt.list_top_windows()
        assert isinstance(ws, list)
        for w in ws[:5]:
            assert isinstance(w, wt.WindowInfo)
            assert w.hwnd

    def test_keyword_filter_no_match(self):
        assert wt.list_top_windows(
            title_keywords=["zzz_no_such_window_zz"]) == []
        assert wt.find_game_window(["zzz_none_zz"], ["nosuch.exe"]) is None

    def test_activate_invalid_hwnd_false(self):
        assert wt.activate_window(0xDEAD, timeout_s=0.05) is False

    def test_foreground_window(self):
        fg = wt.foreground_window()
        assert fg is None or isinstance(fg, wt.WindowInfo)

    def test_window_label(self):
        info = wt.WindowInfo(hwnd=1, title="Game", pid=999,
                             process_name="game.exe", elevated=True)
        assert "Game" in info.label() and "管理员" in info.label()

    def test_process_elevated_invalid_pid(self):
        # pid 0xffffffff should not crash; result may be None or False
        assert wt.process_elevated(0xFFFFFFFF) in (None, False)
