# delta-music-player

将 MIDI 乐曲自动转换为《三角洲行动》(Delta Force) 可演奏乐器的按键序列并自动演奏：毫秒级时序、可视化选段、按键映射全配置化。

项目的核心是**把 MIDI 映射为游戏乐器的按键序列，并以毫秒级时序自动演奏**。移调、单音化与修饰键组合等处理，均服务于这一核心。

## 免责声明

**请在使用前完整阅读本节。**

- 本项目为学习与研究目的的开源工具，仅调用 Windows 标准输入模拟 API（SendInput / MCI）：不修改游戏、不注入进程、不读写游戏内存、不绕过任何反作弊机制。
- 反作弊系统对模拟输入的判定方式无法预知，**本项目不保证使用后不被判定违规或封号**。风险由使用者自行承担，建议先在可承受损失的环境中试用。
- 在装有反作弊系统的在线游戏中使用任何自动化工具，均可能违反游戏服务条款并导致账号处罚。
- 本项目与《三角洲行动》(Delta Force) 及其发行商无任何关联。
- `songs/father.mid` 为依据公开简谱人工转录的《父亲》（筷子兄弟）主旋律，仅作功能演示；原曲版权归原作者所有，请勿用于商业用途。

## 曲库（songs/）

`songs/` 收录经验证可直接演奏的主旋律 MIDI。**目前仅收录一首**，欢迎通过 Pull Request 扩充：

| 曲目 | 文件 | 说明 |
|---|---|---|
| 《父亲》（筷子兄弟） | [`songs/father.mid`](songs/father.mid) | 依据公开简谱人工转录的单音主旋律，约 3 分钟 / 296 音 |

贡献方式：

1. 将 `.mid` 文件放入 `songs/`，文件名使用 ASCII（如 `father.mid`）；
2. 在本文档上表中添加一行（曲目 / 文件 / 说明）。

提交要求：

- 推荐**单音主旋律**文件（游戏乐器同一时刻仅发一个音）。多音轨或含和弦的文件同样可用（工具会自动选轨并单音化），但旋律清晰的文件效果最佳；
- 提交前请运行 `python app.py songs\<曲目>.mid --dry-run` 确认映射正常，并以 `--simulate` 空跑验证时序；
- 仅接受**本人转录或整理**的版本，请勿上传自网络下载的有版权的成品 MIDI 文件。

## 用法

### 安装

要求：Windows 10/11，Python 3.10+

```powershell
git clone https://github.com/AaronHowell/delta-music-player.git
cd delta-music-player
python -m venv .venv
.venv\Scripts\pip install mido numpy
```

### GUI

```powershell
.venv\Scripts\python gui_app.py
```

1. 若顶部出现黄色权限警告，点击「以管理员身份重启」（游戏以管理员权限运行时必须，否则按键会被 Windows 静默丢弃）；
2. 「选择 MIDI...」加载乐曲，可直接选用曲库中的 `songs/father.mid`。轨道下拉框默认自动选中旋律轨，并标注每轨的音符数、平均音高与单音率，鼓轨标记为 `[鼓]`；
3. 「试听(钢琴)」可先试听；若只需演奏部分段落，在钢琴卷帘上拖拽框选即可；
4. 「预览」查看移调报告与每个音符的按键组合；
5. 在游戏内装备乐器并进入可演奏状态后，点击「开始演奏」，倒计时结束后自动切换至游戏窗口开始演奏；
6. 演奏过程中按键面板实时点亮、播放头同步移动，可随时暂停或停止，结束后输出时序统计。

钢琴卷帘操作：左键拖拽框选区间（单击取消）· 滚轮缩放 · Shift/中键拖拽平移 · 双击恢复全曲视图。

### CLI

```
python app.py <input.mid|mp3|wav|flac>
    [--dry-run]               打印音符→按键时间表与移调报告，不演奏
    [--simulate]              真实时钟空跑调度循环（不发按键），验证时序
    [--transpose auto|none|N] 移调模式（默认 auto：±24 半音全局搜索）
    [--track N]               MIDI：只解析第 N 轨（默认自动选旋律轨）
    [--no-auto-track]         MIDI：合并所有轨道
    [--clip 12.5-30]          只演奏某时间段（秒或 1:05-1:30；起点平移为 0）
    [--window 关键字]          指定要激活的目标窗口（默认按 settings 自动探测）
    [--keyboard-mode scancode|vk]  键盘事件模式
    [--start-delay S]         倒计时秒数
    [--backend melodia]       音频输入的主旋律提取后端（目前仅 melodia）
    [--output-dir dir]        产物目录（默认 library/）
```

示例：

```powershell
.venv\Scripts\python app.py songs\father.mid --dry-run    # 预览音符→按键映射
.venv\Scripts\python app.py songs\father.mid              # 装备乐器后演奏
.venv\Scripts\python app.py song.mid --clip 30-60         # 只演奏 30-60 秒
```

产物写入 `library/`：`<歌名>_notes.json` 记录每个音的时间、时值、音高与按键组合；音频输入另有 `_contour.json`（F0 轮廓）与 `_melody.mid`（提取出的主旋律，可导入 DAW 核对）。

### 游戏内校准（可选）

默认配置通常可直接使用，仅在发现音高不符（整体偏高偏低、修饰键无效）时才需要校准。`config/instrument.json` 是按键映射的唯一事实来源，出厂默认：基础行 C4-C5，修饰键 降调=-12 / 半音=+1 / 升调=+12 半音，输入为鼠标左/中/右键。

1. 逐个按基础键确认真实音高，修改 `base_notes`（例如 Z 实际为 F3 则写 `"F3": "z"`）；
2. 按住每个修饰键后再按基础键，确认音高变化量与实际按键，修改 `modifiers` 的 `semitones` 与 `input`；
3. 不规则映射直接写 `note_map`（优先级最高）：`"60": ["z"]`、`"61": ["sharp","z"]`、`"72": ["upper","z"]`，列表末位为主键，其余为修饰键名；
4. 使用「预览」或 `--dry-run` 核对整曲映射后再进入游戏。

如需手动验证按键可达性，可使用配套测试短语（覆盖八键、半音、升降调、双修饰、长音与重复音）：

```powershell
.venv\Scripts\python tools\manual_play_test.py        # 游戏内装备乐器后运行
.venv\Scripts\python tools\test_sendinput_manual.py   # 先在记事本验证按键可达
```

其余参数见 `config/settings.json`：重触发间隔 `min_retrigger_gap_ms`(15)、最短按键 `min_key_hold_ms`(8)、延迟补偿 `latency_compensation_ms`（游戏发声滞后于按键时调大，使发送整体提前）、单音化策略、移调搜索范围等。

### 游戏未收到输入（排障）

先运行诊断工具：

```powershell
.venv\Scripts\python tools\input_doctor.py            # 只读诊断
.venv\Scripts\python tools\input_doctor.py --test vk  # 3 秒后向前台窗口发送 zzz 实测
```

按命中概率排序排查：

1. **权限（UIPI）**：三角洲行动以管理员权限运行（`DeltaForceClient-Win64-Shipping.exe`），普通权限进程发出的 SendInput 会被 Windows 静默丢弃。请使用 GUI 的「以管理员身份重启」，或在管理员终端中运行 CLI。
2. **前台窗口**：SendInput 发送至当前前台窗口。工具会自动探测并绑定游戏窗口，并在倒计时结束后调用 `SetForegroundWindow`；也可通过「点选窗口」手动绑定。
3. **键盘模式**：默认为 `scancode`；提权与前台窗口均正确但仍无响应时，可切换至 `vk` 模式重试。

若以上三项均正确而游戏仍无响应，说明输入被反作弊系统在输入层过滤。本项目不提供任何绕过手段，请自行评估风险。

## 项目原理与扩展

### 数据流

```
MIDI 文件 ──► midi/ 加载解析（tempo map、多轨、自动选轨）──┐
                                                          ├──► NoteEvent[]（统一中间表示，绝对秒时间轴）
音频文件 ──► audio/ 主旋律提取（WSL MELODIA，实验性）──────┘          │
                                                                     ▼
                                     music/transposer   全局移调 + 八度折叠 + 就近吸附
                                                                     ▼
                                     music/monophonic   单音化（和弦取一音、重叠截断）
                                                                     ▼
                                     instrument/mapper  音符 → 按键组合（配置驱动）
                                                                     ▼
                                     playback/scheduler 构建事件流 → 绝对时间轴调度 → SendInput
```

### 核心数据结构

```python
@dataclass
class NoteEvent:
    pitch: int        # MIDI 音号，60 = C4
    start: float      # 秒
    duration: float   # 秒
    velocity: int = 100
    confidence: float = 1.0
```

所有输入源（MIDI 文件、音频提取）均统一转换为 `NoteEvent[]`，下游模块只依赖这一结构。**新增歌曲来源时，只需产出 NoteEvent 列表**，其余管线可完全复用。

### 模块职责

```
app.py / gui_app.py     CLI / GUI 入口（管线一致：解析→移调→单音化→映射→调度）
midi/                   MIDI 加载、解析（tempo map/多轨/自动选轨）、写出
music/                  NoteEvent、音高数学、移调/折叠、单音化、选段截取、F0 量化器
instrument/             乐器 Profile（JSON 配置驱动）、音符→按键组合映射
playback/               事件调度（绝对时间轴）、SendInput 后端、窗口绑定/权限、MCI 试听
audio/                  音频输入：backend 工厂 + WSL MELODIA 桥（实验性）
config/                 instrument.json（按键映射）/ settings.json（全部可调参数）
songs/                  曲库
tools/                  手动游戏内测试、输入诊断
tests/                  pytest（162 个）
RESEARCH.md             参考项目分析与架构决策
NOTICE.txt              第三方代码归属（pydirectinput, MIT）
```

### 关键设计

- **毫秒级时序**：调度器基于绝对时间轴（目标时刻 = 起点 + 事件时间），sleep 误差不累积；采用两级等待策略，距离较远时粗粒度睡眠，目标前数毫秒内忙等。实测 24 秒演奏 mean +0.39ms / max +1.94ms / 最终漂移 +0.01ms；
- **修饰键深度计数**：共享同一物理输入的重叠音符，仅在深度 0→1 时发送按下、1→0 时发送释放，确保修饰键不会在其它音符仍需要它时被抬起；连奏边界处修饰键无缝保持，不产生重触发与时序偏移；
- **失败安全**：任何退出路径（停止、暂停、异常）均自动释放全部已按下的键；
- **GUI 性能**：钢琴卷帘采用分层 item 复用、变更才写入与按帧合并重绘，数千音符规模下拖拽、缩放与播放头移动均保持流畅。

### 扩展点

- **新乐器或新键位布局**：仅需修改 `config/instrument.json`。映射完全由配置驱动，代码中不存在"升调=+12"式的硬编码假设；
- **新的音频提取后端**：实现 `audio/melody_extractor.py` 中的 `AudioTranscriber` 接口（可参考 `melodia_backend.py`），并在工厂函数中注册；
- **调度参数调整**：重触发间隔、最短按键时长、延迟补偿等位于 `config/settings.json -> playback`；
- **架构取舍的详细说明**：见 [RESEARCH.md](RESEARCH.md)。

### 测试

```powershell
.venv\Scripts\python -m pytest tests -q
```

覆盖范围：tick/tempo 换算、多轨与自动选轨、Hz↔MIDI 转换、移调评分与折叠吸附、单音化（和弦/截断）、选段截取、修饰键组合生成、SendInput 结构/scancode/VK 模式（mock）、事件构建（连奏 handoff/深度计数/重触发）、假时钟下的绝对时间轴/暂停平移/停止释放、F0 轮廓量化。

### 音频输入（实验性）配置

MELODIA 提取在 WSL 内运行（essentia 无 Windows wheel），需一次性离线安装：

```powershell
python audio\wsl\fetch_wsl_wheels.py wsl_wheels
((Get-Content audio\wsl\setup_wsl_offline.sh -Raw) -replace "`r","") | wsl -d Ubuntu-26.04 -u root bash
```

发行版与 venv 路径在 `config/settings.json -> wsl` 中配置，详细说明与已知限制见 `audio/wsl/README.md`。

## 许可证

MIT，见 [LICENSE](LICENSE)。

`playback/win32_sendinput.py` 改编自 MIT 许可的 [pydirectinput](https://github.com/learncodebygaming/pydirectinput)（Copyright (c) 2020 Ben Johnson），归属声明见 [NOTICE.txt](NOTICE.txt)；调度与移调的设计思想借鉴自 GPL-3.0 项目 MeowField_AutoPiano 与 AutoMidiPlayer，未复制代码，分析见 [RESEARCH.md](RESEARCH.md)。
