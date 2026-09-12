# RESEARCH.md — 参考项目分析与最终架构

> 生成日期：2026-09-10
> 分析对象：`refs/MeowField_AutoPiano`、`refs/AutoMidiPlayer`、`refs/pydirectinput`（均为 shallow clone）
> 另含：spotify/basic-pitch、Essentia MELODIA、Demucs 的可行性调查（基于本机环境实测）

---

## 1. 参考项目一览表

| 项目 | 许可证 | 语言/技术栈 | 可复用模块 | 需要修改的部分 | 建议直接依赖? | 建议复制思想重新实现? |
|---|---|---|---|---|---|---|
| **MeowField_AutoPiano** | **GPL-3.0** | C# / .NET 10 / WPF | 播放引擎（绝对时间轴调度）、移调评分（丢音数目标函数）、按键引用计数、暂停/恢复的 origin 平移、乐器 JSON 配置结构 | 全部（语言不同，且 GPL 传染性） | ❌ 否 | ✅ **是**——调度器/移调/防粘键设计思想全盘借鉴 |
| **AutoMidiPlayer** | **GPL-3.0** | C# / .NET 10 / WPF + DryWetMidi | 乐器 Profile 抽象（有序音号数组 ∥ 键位数组）、Smart 移调（八度折叠→调式量化→评分）、BaseKey/用户偏移分离 | 全部（同上） | ❌ 否 | ✅ 是——Profile 建模与折叠策略借鉴 |
| **pydirectinput** | **MIT** | Python (ctypes) | SendInput 结构体定义、scancode 表、键盘/鼠标标志常量、down/up 封装范式 | argtypes 缺失、扩展键 `+1024` 写法、`position()` falsy bug | ⚠️ 可作为 pip 依赖，但其 PAUSE/failsafe 装饰器不适合精确调度 | ✅ **直接改编其 MIT 代码**（保留版权声明），去掉装饰器层 |
| **spotify/basic-pitch** | Apache-2.0 | Python | 完整的 Audio→MIDI/NoteEvent 推理 | 无需修改 | ✅ **是**（pip 依赖，走 ONNX 路径，见 §3） | ❌ |
| **Essentia (MELODIA)** | **AGPL-3.0** | C++ / Python 绑定 | `PredominantPitchMelodia` 算法本体 | 无需修改 | ✅ 是——但 **PyPI 无任何 Windows wheel**，只能在 **WSL** 内作为独立进程使用（进程隔离，个人使用无 AGPL 传染问题） | ❌ |
| **Demucs** | MIT | Python (PyTorch) | 人声分离 CLI | 无需修改 | ✅ 可选依赖（torch 2.14 有 cp314 Windows wheel，**可在 Windows 原生跑**） | ❌ |
| **mido** | Apache-2.0 | Python | MIDI 文件解析、tempo map 合并迭代、MIDI 写出 | 无需修改 | ✅ **是**（核心依赖） | ❌ |

**GPL 合规结论**：MeowField_AutoPiano 与 AutoMidiPlayer 均为 GPL-3.0，本项目**不复制其任何代码**，仅借鉴算法设计思想（时间调度模型、移调目标函数、防粘键机制），用 Python 独立重新实现。pydirectinput 为 MIT，其结构体定义与 scancode 表可直接改编进 `playback/win32_sendinput.py`，并在文件头保留版权声明。

---

## 2. MeowField_AutoPiano 深度分析（最重要参考）

> 实际是 C#/.NET WPF 项目（并非 Rust），目标游戏为第五人格/摩尔庄园等。
> 注：仓库缺失 `src/MeowField.Infrastructure.Midi/`，MIDI 解析实现只能从接口与测试反推（用 DryWetMidi）。

### 2.1 值得全盘借鉴的设计

1. **解析层彻底吞掉 tempo/tick**：MIDI 解析结果直接摊平为绝对时间的 note 列表（`MidiNote(StartMs, EndMs, ...)`），下游 Domain 层零时序复杂度、可纯函数单测。→ 我们的 `NoteEvent(pitch, start, duration)` 采用同一边界（float 秒）。

2. **绝对时间轴 + origin 平移**（`PlaybackEngine.cs`）：
   - 用 QPC 高精度时钟，播放开始时算一次 `origin = now - cursor`；每个事件到期时刻 = `origin + eventOffset`，**永远相对 origin 而非上一事件**，sleep 误差不累积。
   - **暂停不破坏时间轴**：恢复时 `origin += 暂停时长`。
   - **两段式等待**：剩余 >3ms → `sleep(min(5, remaining-1))`；≤3ms → SpinWait 忙等，亚毫秒精度。

3. **延迟补偿的对称设计**：事件按 `t - latency` 提前发送（抵消音频链路延迟），UI 进度按 `elapsed + latency` 补回显示。→ 我们在 `settings.json` 留 `latency_compensation_ms`。

4. **防粘键/防丢音双保险**：
   - 构建期：同一按键的重叠音符，前一个 End 截断到后一个 Start；完全同刻的合并为一次按键。
   - 运行期：按键**引用计数** `_keyDepths`，depth 0→1 才真正发 down，降到 0 才发 up。
   - 事件排序：**同一时刻 Up 排在 Down 之前**（保证同键重触发先抬后按）。
   - `MinimumKeyHoldMs = 8ms`：防止极短音符 down/up 同刻被游戏丢弃。
   - **同批次多按键逐个发送、间隔 1ms**：一次性批量 SendInput 会被游戏在同一输入帧合帧丢键（踩坑经验）。

5. **移调目标函数把"丢音数"计入**：不只看"多少音符落在音域内"，还模拟真实按键时序统计**实际听不见的音符数**（同键同时间簇互相覆盖），与音域命中率做词典序比较。搜索范围 ±12 半音（八度折叠已覆盖更大范围）。

6. **任何退出路径都释放所有按下的键**（异常/停止/暂停均有 ReleaseActiveKeys 兜底）。

### 2.2 关键工程数值（直接采纳为默认配置）

| 项 | 值 | 说明 |
|---|---|---|
| 最小按键保持 | 8 ms | 短于则 up 顺延 |
| 忙等阈值 | 3 ms | 之下 spin，之上 sleep |
| 单次 sleep 上限 | 5 ms | 让出 CPU 又不睡过头 |
| 同批次按键间隔 | 1 ms | 防游戏合帧丢键 |
| 和弦/同时判定窗口 | 40 ms | 时间聚类粒度 |
| 移调搜索范围 | ±12~±24 半音 | 见 §5.3 |

---

## 3. 本机环境实测结论（影响技术选型）

本机：**Windows 11 + Python 3.14.5**（py 注册表中的 3.11/3.12 已失效），WSL2 已装（Ubuntu-26.04 = Python 3.14.4 等 5 个发行版）。

| 包 | Python 3.14 / Windows 可用性 | 结论 |
|---|---|---|
| mido 1.3.3 / numpy / pytest | ✅ | 已装入项目 venv |
| **essentia** | ❌ **PyPI 无任何 Windows wheel**（仅 manylinux cp314 / macOS） | MELODIA 走 **WSL 子进程**（用户已确认此方案）；WSL Ubuntu-26.04 的 Python 3.14 恰好匹配 essentia 唯一发布的 cp314 manylinux wheel |
| tensorflow | ❌ 无 cp314 wheel（最高 cp313），且 basic-pitch 钉死 `<2.15.1`（仅支持 ≤py3.11） | 弃 TF 路径 |
| **basic-pitch 0.4.0** | ✅ **wheel 自带 `nmp.onnx` 模型**，代码内置 ONNX 推理分支；onnxruntime 1.29 有 cp314 wheel | `pip install --no-deps` + 手动装依赖（跳过 tensorflow）即可在 Windows 原生跑 |
| librosa 1.0 / numba 0.67 / llvmlite / scipy / soundfile / soxr | ✅ 全部有 cp314 wheel | basic-pitch 的依赖链完整可用 |
| torch 2.14 / torchaudio 2.11 | ✅ cp314 Windows wheel 存在 | **Demucs 可 Windows 原生跑**，无需 WSL（Phase 7 可选依赖） |

**音频主线**（用户确认）：
```
song.mp3 ──(WSL: essentia MELODIA)──► F0 轮廓 JSON ──(Windows: quantizer)──► NoteEvent[]
song.mp3 ──(Windows: basic-pitch ONNX, Phase 6 对比)──────────────────────► NoteEvent[]
song.mp3 ──(Windows: demucs 分离人声, Phase 7 可选前置)──► vocals.wav ──► 上述任一管线
```
WSL 侧只做"essentia 提取 F0 轮廓"这一件最小任务（自包含脚本），所有可配置的后处理（滤波/滞回/分段/合并）留在 Windows 主项目中，保证可测试、可调参、与 MELODIA 解耦。

---

## 4. AutoMidiPlayer 借鉴要点

- **乐器 Profile 建模**：`InstrumentConfig(有序 MIDI 音号数组) ∥ KeyboardLayoutConfig(有序键位数组)`，映射 = `notes.IndexOf(note)` 一行。任何键数/布局的乐器都能用同一结构表达。→ 我们的 `instrument.json` 用 `note_map: {midi: [modifiers..., key]}` 显式表 + `base_notes × modifiers` 自动生成两种互补方式（见 §5.4）。
- **修饰键表示法**：用 DSL 字符串（`"a"`/`"A"`/`"^a"`）表达 主键+修饰键组合。→ 我们简化为列表 `["sharp","z"]`（末位=主键，前面=修饰键），JSON 里更直白。
- **Smart 移调三段式**：先八度折叠 → 若折后音不可奏（全音阶乐器遇黑键）→ 在可用音级中按"距离优先+惩罚跨八度"选最近可奏音。→ 采纳为 folding 后的 nearest-playable 兜底。
- **BaseKey（自动检测调中心）与用户手动偏移分离存储**。→ MVP 简化为 `--transpose auto|N|none`，预留结构。
- 调度本身委托给 DryWetMidi（无自建漂移补偿）→ 这部分以 MeowField 为准。

## 5. pydirectinput 借鉴要点（MIT，直接改编）

- **游戏为什么必须用 scancode**：DirectInput/RawInput 在 HID 层读键盘扫描码，不走虚拟键翻译；只填 `wVk` 的合成事件游戏看不到。→ 键盘事件一律 `wScan + KEYEVENTF_SCANCODE`。
- 直接改编：`INPUT/KEYBDINPUT/MOUSEINPUT` 结构体（补 argtypes/restype、dwExtraInfo 用 `ULONG_PTR`）、Set-1 scancode 表（`z x c v b n m ,` = `0x2C 0x2D 0x2E 0x2F 0x30 0x31 0x32 0x33`）、鼠标标志（LEFTDOWN=0x02/LEFTUP=0x04/RIGHTDOWN=0x08/RIGHTUP=0x10/MIDDLEDOWN=0x20/MIDDLEUP=0x40）。
- 修正其已知问题：不用装饰器全局 PAUSE（会破坏精确调度）；`press()` 的 down/up 之间由我们自己的调度器控制时长；扩展键不用 `+1024` 写法。
- 注意：游戏若以管理员权限运行，普通权限进程的 SendInput 会被 UIPI 静默丢弃 → README 提示"以相同或更高权限运行"。

---

## 6. 最终架构

### 6.1 数据流

```
                        ┌─────────────────────────────┐
 song.mp3/wav/flac ────►│ audio/ (Phase 5-7)           │
   │                    │  melodia_backend ─(WSL 子进程)│
   │                    │  basic_pitch_backend (ONNX)  │──► F0 轮廓 / note events
   │                    │  (demucs 前置分离, 可选)       │         │
   │                    └─────────────────────────────┘         ▼
   │                                                   music/quantizer.py
 song.mid ────► midi/ (Phase 1)                                │
                 midi_loader + midi_parser ──────────────┐     │
                 (mido, tempo map, --track N/自动选轨)     │     │
                                                         ▼     ▼
                                                   NoteEvent[]  ◄── 统一数据结构
                                                         │      (pitch:int, start/duration:float 秒)
                                          music/transposer.py   │  全局移调搜索 + 八度折叠
                                                         ▼
                                     instrument/ (Phase 2)  InstrumentProfile + Mapper
                                          midi_pitch → InputCombination(mods+[key])
                                                         ▼
                                      playback/ (Phase 3-4)  Scheduler(绝对时间轴)
                                          → InputBackend: Win32SendInput / DryRun
                                          → Player: 倒计时、Space 暂停、Esc 停止、时序统计
```

### 6.2 目录结构（与你给的规划一致）

```
delta_music_player/
    app.py                     # CLI 入口（argparse）
    audio/
        audio_loader.py        # 格式探测/存在性校验；WSL 路径转换
        melody_extractor.py    # AudioTranscriber 统一接口 + backend 工厂
        melodia_backend.py     # WSL 子进程桥：essentia MELODIA → F0 轮廓 → quantizer
        basic_pitch_backend.py # basic-pitch ONNX（lazy import）
        wsl/
            melodia_extract.py # 自包含脚本，在 WSL 内运行（仅依赖 essentia+numpy）
            setup_wsl.md       # WSL 环境搭建说明
    midi/
        midi_loader.py         # .mid → mido.MidiFile
        midi_parser.py         # → NoteEvent[]（tempo 合并迭代、--track N、自动选旋律轨）
    music/
        note_event.py          # NoteEvent dataclass + JSON/MIDI 导出
        pitch_utils.py         # Hz↔MIDI、音名↔MIDI（60=C4）
        quantizer.py           # F0 轮廓 → NoteEvent[]（中值滤波/滞回/分段/合并）
        transposer.py          # 全局移调搜索 + 八度折叠 + 统计报告
    instrument/
        instrument_profile.py  # 加载/校验 instrument.json，生成 midi→组合 表
        mapper.py              # InputCombination 查询、可奏音域
    playback/
        scheduler.py           # NoteEvent[] → 时间排序的输入事件流（含 retrigger/hold/引用计数）
        input_backend.py       # Backend 抽象 + DryRunBackend
        win32_sendinput.py     # ctypes SendInput（scancode 键盘 + 鼠标，MIT 改编）
        player.py              # 倒计时、播放循环、暂停/停止、时序统计
    config/
        instrument.json        # 乐器键位映射（可编辑、可校准）
        settings.json          # 全部可调参数（阈值/间隔/延迟补偿/WSL 配置）
    output/                    # xxx_notes.json / xxx_melody.mid / xxx_contour.json
    tests/                     # pytest
    requirements.txt           # Windows 侧依赖（分层：core / audio / demucs）
    RESEARCH.md / README.md
```

### 6.3 关键设计决策

1. **统一 NoteEvent**（float 秒时间轴）：所有下游模块不知道音源是 MP3 还是 MIDI。
2. **移调策略**：先全局搜索 t ∈ [-24, +24]，评分词典序 = ①可奏音符数最大化 → ②需折叠数最小化 → ③|t| 最小；再对残余越界音符做八度折叠；折叠后仍不在映射表（音阶有空洞）→ 就近吸附到最近可奏音（借鉴 AutoMidiPlayer Smart 模式的距离+跨八度惩罚），无法吸附才丢弃。全程输出统计日志。
3. **InstrumentProfile 双模式**：
   - 显式 `note_map`：`{"60": ["z"], "61": ["sharp","z"], "72": ["upper","z"]}` —— 用户校准后直接编辑；
   - 自动生成：`base_notes`（音名→键）× `modifiers`（每个修饰键声明 `input` 和 `semitones` 偏移，**不硬编码 +12/-12/+1**，全部来自配置，可校准），生成所有组合后冲突时取修饰键最少的方案；显式表优先。
4. **调度器**（借鉴 MeowField）：绝对时间轴 `t0 + offset`；两段式等待（>3ms sleep(≤5ms)，≤3ms spin）；同刻事件 Up 先于 Down；同键引用计数；`min_key_hold_ms=8`、`min_retrigger_gap_ms=15`（可配）；修饰键按下→主键按下→保持→主键抬起→修饰键逆序抬起；同批多输入逐个发送间隔 1ms；暂停时释放全部按键、恢复时 origin 平移并补按。
5. **时序统计**：每个 down 事件记录 expected/actual/latency，结束输出 mean / P95 / max。
6. **输入后端可插拔**：`DryRunBackend`（打印 + 写 notes.json）/ `Win32SendInputBackend`；播放控制 Space=暂停、Esc=停止（msvcrt 非阻塞轮询，仅 Windows 控制台）。
7. **MELODIA 桥**：`wsl -d <distro> <venv_python> melodia_extract.py <audio> <out_json>`，Windows 路径自动转 `/mnt/c/...`；输出 `{hop_seconds, frames:[[time, freq, confidence],...]}`；Windows 侧 quantizer 完成后处理。essentia 不可用时给出清晰的安装指引而非崩溃。
8. **Debug 能力**：任何音频输入都自动落盘 `output/<stem>_contour.json`（F0 轮廓）、`<stem>_notes.json`、`<stem>_melody.mid`（mido 写出，可用 DAW 检查）。

### 6.4 MVP 阶段划分（实现路线）

| Phase | 内容 | 验证方式 |
|---|---|---|
| 1 | MIDI → NoteEvent → dry-run 打印 | pytest（mido 构造 fixture：tempo 变化、多轨、tick→秒精确断言）|
| 2 | InstrumentProfile + Mapper + Transposer | pytest（60→z、61→sharp+z、组合生成/显式覆盖、折叠、吸附）|
| 3 | Win32 SendInput 后端 | mock 结构体断言 + 真实无害按键（F13）+ tools/test_sendinput_manual.py |
| 4 | Scheduler + Player | pytest 假时钟（事件顺序/绝对时间轴/暂停平移/停止释放）+ 真实时钟演奏统计 |
| 5 | WSL MELODIA → quantizer → NoteEvent | 合成 WAV 已知旋律 → 提取比对；quantizer 纯函数测试 |
| 6 | basic-pitch ONNX backend | 同一音频对比两后端输出 |
| 7 | Demucs 可选前置（Windows 原生 torch） | 有 demucs 则接线，无则清晰提示；不强依赖 |

后续扩展（在 MVP 之上）：tkinter GUI（钢琴卷帘选段、MCI 试听、按键面板、
目标窗口绑定与 UIPI 权限诊断）、单音化管线（music/monophonic.py）、
选段截取（music/clip.py 与 --clip）。
