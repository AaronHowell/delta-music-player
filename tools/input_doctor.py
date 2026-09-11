#!/usr/bin/env python
"""Input doctor: diagnose why the game may not receive our keystrokes.

Checks (read-only):
  * are WE running as administrator?
  * which windows match the game (title/process from settings.json)?
  * is the game process elevated?  (elevated game + normal us = UIPI drops
    every injected keystroke, silently — the #1 cause)
  * what is the current foreground window?

Optional live test:
  --test scancode|vk   after a 3 s countdown, sends  z z z  to whatever is
                       focused (open Notepad first, or the game's chat box)
                       so you can compare which keyboard mode arrives.

Usage:
  python tools/input_doctor.py
  python tools/input_doctor.py --test vk
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", choices=["scancode", "vk"], default=None)
    ap.add_argument("--settings", default=str(ROOT / "config" / "settings.json"))
    args = ap.parse_args()

    from playback.window_target import (
        find_game_window, foreground_window, is_admin, list_top_windows,
        process_elevated)

    settings = {}
    try:
        settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    except Exception:
        pass
    twcfg = settings.get("target_window", {})

    print("=" * 62)
    print("INPUT DOCTOR")
    print("=" * 62)

    admin = is_admin()
    print(f"[1] 本程序管理员权限: {'是 ✓' if admin else '否 ✗'}")
    if not admin:
        print("    → 若游戏以管理员运行，UIPI 会静默丢弃我们发的所有输入。")
        print("    → 解决: 以管理员身份运行 (GUI 有'以管理员重启'按钮;")
        print("      CLI: 管理员 PowerShell 里重跑)")

    print(f"\n[2] 匹配的游戏窗口 (keywords={twcfg.get('title_keywords')}, "
          f"process={twcfg.get('process_names')}):")
    game = find_game_window(twcfg.get("title_keywords", []),
                            twcfg.get("process_names", []))
    if game:
        print(f"    找到: {game.label()}")
        if game.elevated is True and not admin:
            print("    ⚠⚠ 游戏是管理员权限而本程序不是 —— 这就是收不到")
            print("       输入的原因。请以管理员身份重跑本工具！")
        elif game.elevated is True:
            print("    ✓ 游戏与本程序都是管理员权限，UIPI 不拦截")
        elif game.elevated is None:
            print("    ? 无法确定游戏权限（受保护进程），建议直接以管理员跑")
    else:
        print("    未找到 —— 游戏没在运行，或标题/进程名不匹配。")
        print("    当前所有含标题的窗口(前 15 个):")
        for w in list_top_windows()[:15]:
            print(f"      {w.label()}")

    fg = foreground_window()
    print(f"\n[3] 当前前台窗口: {fg.label() if fg else '(无)'}")

    kmode = settings.get("playback", {}).get("keyboard_mode", "scancode")
    print(f"\n[4] settings.json 键盘模式: {kmode}")
    print("    scancode = DirectInput 扫描码(默认);  vk = 虚拟键码")
    print("    (DF-Auto_Blois 对三角洲行动用的是 vk/keybd_event 方式)")

    if args.test:
        from playback import win32_sendinput as w32
        w32.set_keyboard_mode(args.test)
        print(f"\n[TEST] 3 秒后向当前前台窗口发送 z z z (模式: {args.test})")
        print("       现在切到记事本或游戏聊天框...")
        for i in (3, 2, 1):
            print(f"       {i}...")
            time.sleep(1.0)
        for _ in range(3):
            w32.key_down("z")
            time.sleep(0.08)
            w32.key_up("z")
            time.sleep(0.15)
        print(f"[TEST] 完成。目标窗口收到 'zzz' 了吗？模式={args.test}")

    print("\n诊断建议顺序: ①管理员权限 → ②绑定/前台游戏窗口 → ③vk 模式")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
