"""Target-window utilities: find the game window, check elevation, activate.

Why this exists: SendInput goes to the FOREGROUND window, and Windows UIPI
silently drops injected input from a normal-integrity process into an
elevated one.  Delta Force runs elevated (its companion tools, e.g.
DF-Auto_Blois/WindowSpy, force an admin relaunch at startup for exactly
this reason).  So before performing we:

  1. detect whether WE are elevated, and whether the game is
  2. bind the game window (by title/process keyword, or by pointing the
     mouse at it — WindowFromPoint, same trick as WindowSpy's hotkey bind)
  3. SetForegroundWindow + verify right before the first keystroke

Also offers an elevated self-relaunch helper (ShellExecuteW 'runas').
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, List, Optional

if sys.platform != "win32":
    raise ImportError("window_target is only available on Windows")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32
advapi32 = ctypes.windll.advapi32

GA_ROOT = 2
SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TokenElevation = 20


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process_name: str
    elevated: Optional[bool]   # None = could not determine

    def label(self) -> str:
        admin = {True: " [管理员]", False: "", None: " [权限未知]"}[
            self.elevated]
        return f"{self.title or '(无标题)'}  ·  {self.process_name}" \
               f" (pid {self.pid}){admin}"


# ----------------------------------------------------------------------
# elevation
# ----------------------------------------------------------------------

def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def process_elevated(pid: int) -> Optional[bool]:
    """Best-effort: True/False, or None if we lack rights to inspect."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
                h, 0x0008, ctypes.byref(token)):   # TOKEN_QUERY
            return None
        try:
            class TOKEN_ELEVATION(ctypes.Structure):
                _fields_ = [("TokenIsElevated", wintypes.DWORD)]
            elev = TOKEN_ELEVATION()
            size = wintypes.DWORD()
            if advapi32.GetTokenInformation(
                    token, TokenElevation, ctypes.byref(elev),
                    ctypes.sizeof(elev), ctypes.byref(size)):
                return bool(elev.TokenIsElevated)
            return None
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(h)


def relaunch_as_admin() -> bool:
    """Relaunch this program elevated (UAC prompt). Returns False if the
    user declined / it failed.  On success the current process should exit."""
    try:
        params = " ".join(
            f'"{a}"' for a in sys.argv[1:]
        )
        rc = shell32.ShellExecuteW(
            None, "runas", sys.executable, params or None, None, 1)
        return int(rc) > 32
    except Exception:
        return False


# ----------------------------------------------------------------------
# window discovery
# ----------------------------------------------------------------------

def _window_info(hwnd: int) -> Optional[WindowInfo]:
    try:
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pname = _process_name(pid.value)
        return WindowInfo(hwnd=hwnd, title=title, pid=pid.value,
                          process_name=pname,
                          elevated=process_elevated(pid.value))
    except Exception:
        return None


def _process_name(pid: int) -> str:
    try:
        import ctypes.wintypes as wt
        TH32CS_SNAPPROCESS = 0x2

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return "?"
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
                while True:
                    if entry.th32ProcessID == pid:
                        return entry.szExeFile
                    if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snap)
    except Exception:
        pass
    return "?"


def list_top_windows(
    title_keywords: Optional[List[str]] = None,
    process_names: Optional[List[str]] = None,
) -> List[WindowInfo]:
    """Visible top-level windows, optionally filtered by title substring
    (case-insensitive) or process name."""
    found: List[WindowInfo] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            info = _window_info(hwnd)
            if info and (info.title or process_names):
                found.append(info)
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(cb), 0)
    except Exception:
        return found

    if title_keywords:
        kw = [k.lower() for k in title_keywords]
        found = [w for w in found
                 if any(k in w.title.lower() for k in kw)]
    if process_names:
        pn = [p.lower() for p in process_names]
        found = [w for w in found if w.process_name.lower() in pn]
    return found


def find_game_window(
    title_keywords: List[str],
    process_names: List[str],
) -> Optional[WindowInfo]:
    """Best guess for the game window: process-name match first, then
    title-keyword match."""
    by_proc = list_top_windows(process_names=process_names)
    if by_proc:
        return by_proc[0]
    by_title = list_top_windows(title_keywords=title_keywords)
    if by_title:
        # prefer the largest-title candidate that looks like a main window
        return by_title[0]
    return None


def window_under_cursor() -> Optional[WindowInfo]:
    """WindowSpy-style binding: the root window beneath the mouse cursor."""
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    return _window_info(root)


def foreground_window() -> Optional[WindowInfo]:
    hwnd = user32.GetForegroundWindow()
    return _window_info(hwnd) if hwnd else None


def activate_window(hwnd: int, timeout_s: float = 0.6) -> bool:
    """Restore if minimized, bring to front, verify it got focus."""
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        import time
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            if user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.02)
        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False
