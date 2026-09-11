"""Win32 SendInput backend — scancode keyboard + mouse button events.

Games reading input via DirectInput/RawInput see hardware scan codes, not
translated virtual keys; sending only wVk events is invisible to them.  All
keyboard events here therefore use wScan + KEYEVENTF_SCANCODE.

Adapted from pydirectinput (https://github.com/learncodebygaming/pydirectinput)
MIT License, Copyright (c) 2020 Ben Johnson — see NOTICE.txt in the project
root.  Changes from the original:
  * proper argtypes/restype and ULONG_PTR-sized dwExtraInfo
  * no global PAUSE/failsafe decorators (the scheduler owns all timing)
  * down/up primitives only — long notes are held by the caller
  * extended keys use KEYEVENTF_EXTENDEDKEY instead of the original's
    "+1024 scancode" convention
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("win32_sendinput is only available on Windows")

# --------------------------------------------------------------------------
# constants (values identical to the Win32 headers / pydirectinput)
# --------------------------------------------------------------------------
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSE_BUTTONS = {
    "mouse_left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "mouse_right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "mouse_middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

# Set-1 make codes (DirectInput scan codes), from pydirectinput's
# KEYBOARD_MAPPING (letters/digits/punctuation we can need for an instrument)
SCAN_CODES = {
    "backspace": 0x0E, "tab": 0x0F, "enter": 0x1C, "return": 0x1C,
    "shift": 0x2A, "lshift": 0x2A, "rshift": 0x36,
    "ctrl": 0x1D, "lctrl": 0x1D, "rctrl": 0x1D,  # rctrl is extended
    "alt": 0x38, "lalt": 0x38, "ralt": 0x38,      # ralt is extended
    "capslock": 0x3A, "esc": 0x01, "escape": 0x01,
    "space": 0x39, "spacebar": 0x39,
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
    "-": 0x0C, "=": 0x0D, "[": 0x1A, "]": 0x1B, "\\": 0x2B,
    ";": 0x27, "'": 0x28, "`": 0x29, ",": 0x33, ".": 0x34, "/": 0x35,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15,
    "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
    "j": 0x24, "k": 0x25, "l": 0x26,
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31,
    "m": 0x32,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F, "f6": 0x40,
    "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44, "f11": 0x57,
    "f12": 0x58, "f13": 0x64,
    "up": 0xC8, "down": 0xD0, "left": 0xCB, "right": 0xCD,  # extended
    "insert": 0x52, "delete": 0x53, "home": 0x47, "end": 0x4F,
    "pageup": 0x49, "pagedown": 0x51,  # extended where applicable
}

# keys needing the E0 prefix (KEYEVENTF_EXTENDEDKEY)
EXTENDED_KEYS = {
    "up", "down", "left", "right", "insert", "delete", "home", "end",
    "pageup", "pagedown", "rctrl", "ralt",
}

# named keys -> virtual-key codes (for "vk" keyboard mode)
VK_CODES = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "ctrl": 0x11, "lctrl": 0xA2, "rctrl": 0xA3,
    "alt": 0x12, "lalt": 0xA4, "ralt": 0xA5,
    "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "spacebar": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B, "f13": 0x7C,
}

# Keyboard event flavor:
#   "scancode" — wScan + KEYEVENTF_SCANCODE (DirectInput-friendly default)
#   "vk"       — wVk only, like keybd_event(vk,0,...) which is what
#                DF-Auto_Blois/WindowSpy successfully uses with Delta Force
KEYBOARD_MODE = "scancode"


def set_keyboard_mode(mode: str) -> None:
    global KEYBOARD_MODE
    if mode not in ("scancode", "vk"):
        raise ValueError(f"unknown keyboard_mode: {mode!r}")
    KEYBOARD_MODE = mode


def resolve_vk(name: str) -> int:
    key = name.lower()
    if key in VK_CODES:
        return VK_CODES[key]
    if len(key) == 1:
        vk = _user32.VkKeyScanW(ord(key)) & 0xFF
        if vk == 0xFF:
            raise ValueError(f"no virtual-key code for: {name!r}")
        return vk
    raise ValueError(f"unsupported key name: {name!r}")

# --------------------------------------------------------------------------
# ctypes structures
# --------------------------------------------------------------------------
ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


_user32 = ctypes.windll.user32
_SendInput = _user32.SendInput
_SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_SendInput.restype = wintypes.UINT


class SendInputError(RuntimeError):
    pass


def _send(event: INPUT) -> None:
    inserted = _SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
    if inserted != 1:
        # Most common cause: target window runs elevated (UIPI silently drops
        # input from a lower-integrity process).
        raise SendInputError(
            f"SendInput failed (inserted={inserted}). If the game runs as "
            f"administrator, run this program as administrator too."
        )


def _keyboard_event(scan: int, flags: int) -> INPUT:
    ev = INPUT()
    ev.type = INPUT_KEYBOARD
    ev.u.ki = KEYBDINPUT(0, scan, flags, 0, 0)
    return ev


def _mouse_event(flags: int) -> INPUT:
    ev = INPUT()
    ev.type = INPUT_MOUSE
    ev.u.mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)
    return ev


# --------------------------------------------------------------------------
# public primitives (down/up only — holding is the caller's job)
# --------------------------------------------------------------------------
def resolve_key(name: str) -> tuple[int, bool]:
    """name -> (scancode, extended).  Accepts single characters and the
    named keys in SCAN_CODES."""
    key = name.lower()
    if key not in SCAN_CODES:
        raise ValueError(f"unsupported key name: {name!r}")
    return SCAN_CODES[key], key in EXTENDED_KEYS


def key_down(name: str) -> None:
    if KEYBOARD_MODE == "vk":
        vk = resolve_vk(name)
        flags = KEYEVENTF_EXTENDEDKEY if name.lower() in EXTENDED_KEYS else 0
        ev = INPUT()
        ev.type = INPUT_KEYBOARD
        ev.u.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        _send(ev)
        return
    scan, extended = resolve_key(name)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    _send(_keyboard_event(scan, flags))


def key_up(name: str) -> None:
    if KEYBOARD_MODE == "vk":
        vk = resolve_vk(name)
        flags = KEYEVENTF_KEYUP | (
            KEYEVENTF_EXTENDEDKEY if name.lower() in EXTENDED_KEYS else 0)
        ev = INPUT()
        ev.type = INPUT_KEYBOARD
        ev.u.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        _send(ev)
        return
    scan, extended = resolve_key(name)
    flags = (KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
             | (KEYEVENTF_EXTENDEDKEY if extended else 0))
    _send(_keyboard_event(scan, flags))


def mouse_down(button: str) -> None:
    try:
        down, _ = MOUSE_BUTTONS[button.lower()]
    except KeyError:
        raise ValueError(f"unsupported mouse button: {button!r}") from None
    _send(_mouse_event(down))


def mouse_up(button: str) -> None:
    try:
        _, up = MOUSE_BUTTONS[button.lower()]
    except KeyError:
        raise ValueError(f"unsupported mouse button: {button!r}") from None
    _send(_mouse_event(up))


def is_mouse(button: str) -> bool:
    return button.lower() in MOUSE_BUTTONS


def input_down(identifier: str) -> None:
    """Unified primitive: 'mouse_left' -> mouse, anything else -> keyboard."""
    if is_mouse(identifier):
        mouse_down(identifier)
    else:
        key_down(identifier)


def input_up(identifier: str) -> None:
    if is_mouse(identifier):
        mouse_up(identifier)
    else:
        key_up(identifier)
