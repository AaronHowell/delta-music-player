# delta_music_player

歌曲 → 主旋律 → 游戏按键 → 自动演奏。目标游戏：《三角洲行动》(Delta Force)
中的可演奏乐器（基础音键 `Z X C V B N M ,` + 升调/降调/半音三个可组合修饰键）。

**当前状态**

- ✅ **MIDI → 演奏主线完成**（Phase 1-4）：解析、自动选旋律轨、自动移调、
  八度折叠、按键组合映射、Win32 SendInput（scancode）、绝对时间轴调度、
  长音/重触发/修饰键组合、Space 暂停 / Esc 停止、时序统计。
  120 个单元测试全绿；真实时钟 24s 演奏验证：mean +0.39ms / P95 +1.57ms /
  max +1.94ms / **最终漂移 +0.01ms**（零累积漂移）。
- ⏸ **音频主线暂缓**（按用户决定）：mp3/wav/flac → 主旋律提取的基础设施
  已搭好（WSL MELODIA 桥已跑通、量化管线已实现并测试、basic-pitch ONNX
  路线已调研），待回头用真实歌曲调优。详见 `audio/wsl/README.md` 与
  `RESEARCH.md`。

---

## 快速开始

```powershell
cd delta_music_player
python -m venv .venv
.venv\Scripts\pip install mido pytest numpy

# 生成演示 MIDI（小星星，含旋律轨+伴奏轨+tempo轨）
.venv\Scripts\python tools\make_demo_midi.py samples\twinkle.mid

# 预览（不发送任何按键）：显示移调报告 + 每个音的按键组合
.venv\Scripts\python app.py samples\twinkle.mid --dry-run

# 时序仿真（真实时钟跑完整调度循环，但不发按键）
.venv\Scripts\python app.py samples\twinkle.mid --simulate --start-delay 1

# 真实演奏：倒计时 5 秒内切到游戏窗口
.venv\Scripts\python app.py your_song.mid --start-delay 5
```

产物（`output/`）：

- `<歌名>_notes.json` —— 每个音的时间/时值/音高/按键组合，便于人工检查
- `<歌名>_contour.json`、`<歌名>_melody.mid` —— 音频输入时的 F0 轮廓与
  提取出的主旋律 MIDI（在 DAW 里核对提取质量用）

## 游戏收不到输入？（排障）

先跑诊断：

```powershell
.venv\Scripts\python tools\input_doctor.py           # 只读诊断
.venv\Scripts\python tools\input_doctor.py --test vk # 3秒后向前台窗口发 zzz 实测
```

按命中概率排序：

1. **权限（UIPI）——实测本机的主因**：三角洲行动以管理员权限运行
   （`DeltaForceClient-Win64-Shipping.exe [管理员]`），普通权限进程发的
   SendInput 会被 Windows **静默丢弃**（返回成功但游戏收不到）。
   → GUI 顶部有醒目警告条和「以管理员身份重启」按钮；CLI 请在管理员
   PowerShell 里跑。DF-Auto_Blois/WindowSpy 启动时强制 UAC 提权，同理。
2. **前台窗口**：SendInput 发给当前前台窗口。GUI/CLI 会自动探测游戏窗口
   （按进程名/标题关键字，`settings.json -> target_window`），倒计时结束
   自动 `SetForegroundWindow` 切过去；也可以在 GUI 里「点选窗口(3秒内
   指向游戏)」手动绑定。
3. **键盘模式**：默认 `scancode`（DirectInput 扫描码）。若提权+前台后游戏
   仍无反应，把 GUI「键盘模式」切成 `vk`（虚拟键码，即 DF-Auto_Blois 用的
   keybd_event 方式）再试；或改 `settings.json -> playback.keyboard_mode`。

> 若三者都正确游戏仍不响应，则是 ACE 反作弊在输入层过滤合成事件——本项目
> 不做任何绕过，请自行评估风险与可行性。

## GUI 可视化工具

```powershell
.venv\Scripts\python gui_app.py
```

- **选 MIDI** → 轨道下拉框列出每轨的音符数/平均音高/单音率（鼓轨标注 [鼓]），
  默认自动选主旋律轨，可手动改选或"全部合并"
- **钢琴卷帘**（时间×音高可视化）：
  - 左键拖拽 = 框选演奏区间（选中音符高亮橙色，单击 = 清除选择）
  - 滚轮 = 时间轴缩放（以光标为中心）
  - Shift+拖拽 / 中键拖拽 = 平移
  - 双击 = 回到全曲视图
  - 演奏时红色播放头实时移动
- **🔊 试听(钢琴)**：用 Windows 内置 MIDI 合成器（Microsoft GS Wavetable）
  播放当前轨道/选区，边听边选段；试听时播放头同步移动，再次点击停止
- **目标窗口**：自动探测游戏窗口（也支持下拉选择/「点选窗口」把鼠标指到
  游戏上绑定）；倒计时结束自动把游戏切到前台再开始发送按键；
  非管理员运行且游戏是管理员时显示警告条 + 「以管理员身份重启」按钮
- **键盘模式**：scancode（默认）/ vk 可切换（见下方排障）
- **▶ 开始演奏**：倒计时内切到游戏窗口；有选区时只演奏选区
  （移调/单音化/按键映射都针对选区重新计算，选区起点平移为 0，立即开奏）
- **导出选中段 .mid**：把框选的段落存成独立 MIDI（保留原时间轴），方便
  在 DAW 里核对或喂给其他工具
- **⏸ 暂停 / ⏹ 停止** 按钮；演奏中按键面板实时点亮当前按下的键与修饰键；
  结束后弹出时序统计（mean/P95/max/漂移）

管线与 CLI 完全一致：解析 → [选区截取] → 移调 → **单音化**（任意时刻只有一个音：
和弦取最高音、重叠截断前音，`settings.json -> monophonic` 可配）→ 映射 → SendInput。

## 游戏内实测流程（建议按顺序）

1. **按键到达性**：打开记事本并聚焦，运行
   `python tools\test_sendinput_manual.py`，应看到 `zxcvbnm,` 被逐键敲出
   （每键按住 0.3s）。加 `--mouse` 可测三个鼠标键（先把光标停在无害处）。
2. **乐器短语**：进游戏、装备乐器、开始演奏状态，运行
   `python tools\manual_play_test.py`。这段手写短语覆盖验收项：
   基础八键、半音(sharp)、升/降八度(upper/lower)、双修饰键组合(lower+sharp)、
   2 秒长音、同键重复音（重触发间隔）。
3. **整曲**：`python app.py your_song.mid`。

> ⚠️ 游戏以管理员权限运行时，普通权限进程的 SendInput 会被 Windows UIPI
> 静默丢弃 —— 请以相同或更高权限运行本程序。
> ⚠️ 仅使用正常 Windows 输入模拟，不涉及任何反作弊绕过。

## 播放控制

| 键 | 功能 |
|---|---|
| `Space` | 暂停 / 继续（暂停时自动释放所有按住的键，时间轴平移补偿，恢复后节奏不漂移；暂停瞬间正在 sounding 的音会被切断） |
| `Esc` | 停止（释放所有键） |

播放结束后输出时序统计：mean / P95 / max 误差与最终漂移。

## CLI

```
python app.py <input.mid|mp3|wav|flac>
    [--dry-run]            打印音符→按键时间表，不演奏
    [--simulate]           真实时钟空跑调度循环（不发按键），验证时序
    [--transpose auto|none|N]   auto=-24..+24 全局搜索（默认）
    [--track N]            MIDI: 只解析第 N 轨（默认自动选旋律轨）
    [--no-auto-track]      MIDI: 合并所有轨而不自动选轨
    [--start-delay S]      倒计时秒数（默认取 settings.json）
    [--clip 12.5-30]       只演奏/转换某一段时间（秒或 1:05-1:30；选段平移到 0）
    [--instrument path]    乐器配置（默认 config/instrument.json）
    [--settings path]      参数配置（默认 config/settings.json）
    [--backend melodia|basic_pitch]   音频输入时的提取后端
    [--output-dir dir]     产物目录（默认 output/）
```

## 校准指南（进游戏后必做）

`config/instrument.json` 是**唯一**的映射事实来源，代码里没有任何硬编码的
“升调=+12”假设：

```jsonc
{
  "modifiers": {
    "lower": { "input": "mouse_left",   "semitones": -12 },  // ← 待实测校准
    "sharp": { "input": "mouse_middle", "semitones": 1 },    // ← 待实测校准
    "upper": { "input": "mouse_right",  "semitones": 12 }    // ← 待实测校准
  },
  "base_notes": {            // Z X C V B N M , 在游戏中的实际音高
    "C4": "z", "D4": "x", "E4": "c", "F4": "v",
    "G4": "b", "A4": "n", "B4": "m", "C5": ","
  },
  "note_map": {              // 显式映射，优先级高于自动生成
    // "60": ["z"],  "61": ["sharp", "z"],  "72": ["upper", "z"]
  }
}
```

校准步骤建议：

1. 游戏里按单个基础键，用调音器/听感确认其真实音高 → 改 `base_notes`
   的音名（例如 Z 实际是 F3 就写 `"F3": "z"`）。
2. 按住修饰键再按基础键，确认音高变化量 → 改 `semitones`；确认修饰键
   实际是鼠标哪个键/键盘哪个键 → 改 `input`。
3. 特殊映射（或不规则音阶）直接写 `note_map`，格式
   `"<MIDI号或音名>": [修饰键..., 主键]`（末位是主键）。
4. `--dry-run` 检查整曲映射结果再进游戏。

其他可调参数都在 `config/settings.json`：重触发间隔
`min_retrigger_gap_ms`(15)、最短按键 `min_key_hold_ms`(8)、延迟补偿
`latency_compensation_ms`（若游戏发声滞后于按键，调大它让发送提前）等。

## 项目结构

```
app.py                  CLI 入口
midi/                   MIDI 加载、解析（tempo map/多轨/自动选轨）、写出
music/                  NoteEvent、音高数学、移调/折叠、F0 轮廓量化器
instrument/             乐器 Profile（JSON 配置驱动）、音符→按键组合映射
playback/               事件调度（绝对时间轴）、SendInput 后端、播放器
audio/                  音频输入（暂缓）：WSL MELODIA 桥、backend 接口
config/                 instrument.json / settings.json
tools/                  demo 生成、手动游戏内测试、MELODIA 探针
tests/                  pytest（120 个）
RESEARCH.md             参考项目分析 + 架构决策
NOTICE.txt              第三方代码归属（pydirectinput, MIT）
```

核心数据结构（所有来源统一）：

```python
@dataclass
class NoteEvent:
    pitch: int        # MIDI 音号，60 = C4
    start: float      # 秒
    duration: float   # 秒
    velocity: int = 100
    confidence: float = 1.0
```

## 测试

```powershell
.venv\Scripts\python -m pytest tests -q     # 120 passed
```

覆盖：tick/tempo 换算与 tempo 变化、note_on(vel=0)、重触发、多轨自动选轨、
Hz↔MIDI、音名解析、全局移调评分、八度折叠、就近吸附、修饰键组合生成与
显式覆盖、SendInput 结构/scancode/标志（mock）、事件构建（handoff/深度
计数/最短保持/重触发间隔）、假时钟下的绝对时间轴/暂停平移/停止释放。

## 音频部分（暂缓）现状

- **MELODIA（WSL）**：桥已跑通（Windows → `wsl` 子进程 → F0 轮廓 JSON →
  `music/quantizer.py` → NoteEvent）。essentia 因无 Windows wheel 装在
  WSL Ubuntu-26.04 的离线 venv 里（WSL 网络损坏，见 `audio/wsl/README.md`）。
  已知问题：合成测试音上 MELODIA 输出不稳定，需真实歌曲验证后再调参。
- **basic-pitch**：Python 3.14 装不了 tensorflow，但 basic-pitch 0.4.0
  自带 ONNX 模型且 onnxruntime 有 cp314 wheel，`--no-deps` 安装即可用
  （命令见 requirements.txt 注释）。待接入对比。
- **Demucs**：torch 2.14 有 cp314 Windows wheel，可原生跑，作为可选前置
  （`song → vocals → 提取`）。待接入。

## 免责声明

- 本项目为**学习与研究目的**的开源工具，仅使用正常的 Windows 输入模拟
  API（SendInput / MCI），**不修改游戏、不注入进程、不读写游戏内存、
  不绕过任何反作弊机制**。
- 在装有反作弊系统的在线游戏中使用任何自动化工具都可能违反游戏服务
  条款并导致账号处罚，**风险由使用者自行承担**。
- 本项目与《三角洲行动》(Delta Force) 及其发行商没有任何关联。
- 许可证：MIT（见 LICENSE；第三方归属见 NOTICE.txt）。

## 致谢与许可

- 调度/移调/防粘键设计思想借鉴自 GPL-3.0 项目 MeowField_AutoPiano 与
  AutoMidiPlayer（**未复制任何代码**，详见 RESEARCH.md）。
- `playback/win32_sendinput.py` 改编自 MIT 许可的 pydirectinput
  （Copyright (c) 2020 Ben Johnson），见 NOTICE.txt。
- 运行时依赖：mido (Apache-2.0)；可选：basic-pitch (Apache-2.0)、
  onnxruntime (MIT)、demucs (MIT)、essentia (AGPL-3.0，仅作为 WSL 内
  独立进程使用)。
