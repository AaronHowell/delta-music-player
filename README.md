# delta-music-player

🎹 把 MIDI 乐曲自动转换成《三角洲行动》(Delta Force) 可演奏乐器的按键并自动演奏——毫秒级时序、可视化选段、一键开演。

这个项目的核心是：**把 MIDI 映射成游戏乐器的按键序列，并以毫秒级时序自动演奏**。移调、单音化、修饰键组合，都是为这一核心服务的。仓库自带一首可直接演奏的《父亲》（`songs/father.mid`），安装后即可上手，见[快速上手](#快速上手gui)。

> 🎵 **曲库共建**：[`songs/`](songs/) 是开箱即用的曲库，目前收录《父亲》一首。欢迎通过 Pull Request 贡献你转录或调校的主旋律 MIDI，贡献方式见[曲库](#曲库songs)。

> 仅使用正常的 Windows 输入模拟 API（SendInput / MCI）：不修改游戏、不注入进程、不读写游戏内存、不绕过反作弊。

> ⚠️ **风险提示**：反作弊系统如何判定模拟输入无法预知，**本项目不保证使用后不被封号**。简言之——"**用别怕，怕别用**"。请自行评估风险，建议先在小号或可承受损失的环境中试用。详见文末[免责声明](#免责声明)。

## 功能特性

**乐谱处理**

- **MIDI 解析**：正确处理 tempo 变化与多轨文件；自动挑选旋律轨（平均音高 + 单音率评分），也可手动指定轨道或合并全部轨道
- **自动移调**：±24 半音全局搜索最优移调，残余越界音符八度折叠、音阶空洞就近吸附，输出量化报告（可奏率 / 折叠率 / 丢音率）
- **单音化**：乐器任意时刻只发一个音——和弦自动取一音（默认最高音，可选最低音/力度优先），重叠音自动截断前音
- **可视化选段**：钢琴卷帘（时间×音高）上拖拽框选任意段落，只演奏/转换选中部分，并可把选段导出为独立 `.mid`
- **试听**：用 Windows 内置钢琴音源（GS Wavetable）播放当前轨道或选区，边听边选

**游戏演奏**

- **按键映射全配置化**：基础音键 `Z X C V B N M ,` + 三个修饰键（降调/半音/升调，默认映射鼠标左/中/右键），支持任意组合（如 `降调+半音+Z`）；所有音高与键位都可在游戏内实测后校准，代码中**没有任何**"升调=+12"式的硬编码假设
- **毫秒级调度**：绝对时间轴，零累积漂移（实测 24 秒演奏 mean +0.39ms / max +1.94ms / 最终漂移 +0.01ms）；长音按住、同键重复音自动重触发、连奏时修饰键无缝保持
- **播放控制**：倒计时开演、`Space` 暂停/继续、`Esc` 停止；任何退出路径都自动释放全部按键，绝不卡键
- **时序统计**：演奏结束输出 mean / P95 / max 时序误差与最终漂移
- **演奏可视化**：按键面板实时点亮当前按下的键与修饰键，钢琴卷帘播放头同步移动

**输入兼容性**

- Win32 SendInput：**scancode**（DirectInput 扫描码）与 **VK**（虚拟键码）双键盘模式可切换
- **游戏窗口绑定**：按进程名/标题自动探测，也支持鼠标点选绑定；倒计时结束自动把游戏切到前台
- **权限检测**：游戏以管理员运行而本程序没有时（Windows UIPI 会静默丢弃输入），醒目警告 + 一键提权重启
- 一键诊断工具 `tools/input_doctor.py`

**音频输入（实验性）**

- mp3 / wav / flac → 主旋律提取 → 同样经过移调/单音化/映射后演奏
- MELODIA (Essentia) 后端在 WSL 内运行（essentia 无 Windows wheel），输出 F0 轮廓 JSON、提取结果 MIDI 与音符 JSON，便于人工核对

## 安装

要求：Windows 10/11，Python 3.10+

```powershell
git clone https://github.com/AaronHowell/delta-music-player.git
cd delta-music-player
python -m venv .venv
.venv\Scripts\pip install mido numpy
```

## 快速上手（GUI）

```powershell
.venv\Scripts\python gui_app.py
```

1. 顶部若出现**黄色权限警告** → 点「以管理员身份重启」（游戏以管理员运行时必须，否则按键会被系统丢弃）
2. 「选择 MIDI...」加载乐曲；轨道下拉框默认自动选中旋律轨（每轨标注音符数/均高/单音率，鼓轨标 [鼓]）
3. 点「🔊 试听(钢琴)」先听一遍；想只演奏某一段，就在钢琴卷帘上**拖拽框选**
4. 「预览」查看移调报告与每个音符的按键组合
5. 游戏里装备好乐器、进入可演奏状态 → 点「▶ 开始演奏」→ 倒计时结束自动切到游戏开弹
6. 演奏中：按键面板实时点亮、播放头同步移动；「⏸ 暂停 / ⏹ 停止」随时可控；结束后弹出时序统计

**没有现成乐谱？仓库自带可直接演奏的《父亲》**（筷子兄弟）：`songs/father.mid`——按公开简谱逐音校对转录的单音主旋律（约 3 分钟、296 个音，钢琴音色试听）。在 GUI 中通过「选择 MIDI...」打开，或使用 CLI：

```powershell
.venv\Scripts\python app.py songs\father.mid --dry-run    # 预览音符->按键映射
.venv\Scripts\python app.py songs\father.mid              # 装备乐器后直接演奏
```

这首曲子完整演示了项目的核心流程：输入一份普通 MIDI，输出游戏乐器按键序列。任何 `.mid` 文件都遵循同样的流程。

钢琴卷帘操作：**左键拖拽**=框选区间（单击=取消）· **滚轮**=缩放 · **Shift/中键拖拽**=平移 · **双击**=全曲视图

## 曲库（songs/）

`songs/` 收录**验证过可直接演奏**的单音主旋律 MIDI，克隆后即可使用：

| 曲目 | 文件 | 说明 |
|---|---|---|
| 《父亲》（筷子兄弟） | [`songs/father.mid`](songs/father.mid) | 按公开简谱人工转录的单音主旋律，约 3 分钟 / 296 音 |

**贡献方式**：提交 Pull Request，共两步：

1. 将你的 `.mid` 文件放入 `songs/`（文件名使用 ASCII，如 `father.mid`）；
2. 在上表中添加一行（曲名 / 文件 / 一句话说明）。

提交要求：

- 推荐**单音主旋律**文件（游戏乐器同一时刻只发一个音）。多音轨/含和弦的文件同样可用（工具会自动选轨并单音化），但旋律干净的文件效果最佳。提交前请运行 `.venv\Scripts\python app.py songs\你的曲子.mid --dry-run` 确认映射正常，再以 `--simulate` 空跑验证时序；
- 仅接受**本人转录/整理**的版本，请勿上传从网络下载的有版权的成品 MIDI 文件。

## 游戏内校准（可选，多数情况可跳过）

**可直接使用默认配置演奏；仅当发现音高不符时（如整体偏高偏低、修饰键无效），才需要进游戏实测校准。** `config/instrument.json` 的出厂默认（基础行 C4-C5、修饰键 降调=-12 / 半音=+1 / 升调=+12、鼠标左/中/右键）覆盖常见布局：

1. 逐个按基础键，确认真实音高 → 修改 `base_notes`（例如 Z 实际是 F3 就写 `"F3": "z"`）
2. 按住每个修饰键再按基础键，确认音高变化量与实际按键 → 修改 `modifiers` 的 `semitones` 和 `input`
3. 不规则映射直接写 `note_map`（优先级最高）：`"60": ["z"]`、`"61": ["sharp","z"]`、`"72": ["upper","z"]`（列表末位是主键，前面是修饰键名）
4. 用「预览」/`--dry-run` 核对整曲映射再进游戏

如需手动验证按键可达性，可使用配套的手动测试短语（覆盖全部键型：八键/半音/升降调/双修饰/2秒长音/重复音）：

```powershell
.venv\Scripts\python tools\manual_play_test.py     # 游戏内装备乐器后运行
.venv\Scripts\python tools\test_sendinput_manual.py  # 先在记事本验证按键可达
```

其余可调参数在 `config/settings.json`：重触发间隔 `min_retrigger_gap_ms`(15)、最短按键 `min_key_hold_ms`(8)、延迟补偿 `latency_compensation_ms`（游戏发声滞后于按键时调大，发送整体提前）、单音化策略、移调搜索范围等。

## CLI

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
    [--output-dir dir]        产物目录（默认 output/）
```

产物（`output/`）：`<歌名>_notes.json`（每个音的时间/时值/音高/按键组合）、音频输入另有 `_contour.json`（F0 轮廓）与 `_melody.mid`（提取出的主旋律，可导入 DAW 核对）。

示例：

```powershell
.venv\Scripts\python app.py song.mid --dry-run                # 预检
.venv\Scripts\python app.py song.mid --clip 30-60             # 只演奏 30-60 秒
.venv\Scripts\python app.py song.mid --window 三角洲           # 指定目标窗口
```

## 游戏收不到输入？（排障）

先跑诊断：

```powershell
.venv\Scripts\python tools\input_doctor.py            # 只读诊断
.venv\Scripts\python tools\input_doctor.py --test vk  # 3 秒后向前台窗口发 zzz 实测
```

按命中概率排序：

1. **权限（UIPI）**：三角洲行动以管理员权限运行（`DeltaForceClient-Win64-Shipping.exe`），普通权限进程发的 SendInput 会被 Windows **静默丢弃**。→ 用 GUI 的「以管理员身份重启」，或在管理员终端里跑 CLI。
2. **前台窗口**：SendInput 发给当前前台窗口。工具会自动探测并绑定游戏窗口，倒计时结束自动 `SetForegroundWindow`；也可用「点选窗口」手动绑定。
3. **键盘模式**：默认 `scancode`；提权+前台都正确仍无反应时，切换 `vk` 模式（DF-Auto_Blois 等工具对三角洲验证过的方式）再试。

若三者都正确游戏仍不响应，则是 ACE 反作弊在输入层过滤合成事件——本项目不做任何绕过，请自行评估风险。

## 项目结构

```
app.py                  CLI 入口
gui_app.py              tkinter GUI（钢琴卷帘/试听/按键面板/窗口绑定）
midi/                   MIDI 加载、解析（tempo map/多轨/自动选轨）、写出
music/                  NoteEvent、音高数学、移调/折叠、单音化、选段截取、F0 量化器
instrument/             乐器 Profile（JSON 配置驱动）、音符→按键组合映射
playback/               事件调度（绝对时间轴）、SendInput 后端、播放器、
                        窗口绑定/权限诊断、MCI 试听
audio/                  音频输入：WSL MELODIA 桥、backend 接口（实验性）
config/                 instrument.json / settings.json
songs/                  曲库：可直接演奏的主旋律 MIDI（欢迎 PR 共建）
tools/                  手动游戏内测试、输入诊断
tests/                  pytest（162 个）
RESEARCH.md             参考项目分析与架构决策
NOTICE.txt              第三方代码归属（pydirectinput, MIT）
```

核心数据结构（MIDI 与音频来源统一）：

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
.venv\Scripts\python -m pytest tests -q
```

覆盖：tick/tempo 换算、多轨与自动选轨、Hz↔MIDI、移调评分与折叠吸附、单音化（和弦/截断）、选段截取、修饰键组合生成与覆盖、SendInput 结构/scancode/VK 模式（mock）、事件构建（连奏 handoff/深度计数/重触发）、假时钟下的绝对时间轴/暂停平移/停止释放、F0 轮廓量化（颤音/滑音/毛刺/静音）。

## 音频输入（实验性）配置

MELODIA 提取在 WSL 内运行，一次性离线安装（无需 WSL 网络）：

```powershell
python audio\wsl\fetch_wsl_wheels.py wsl_wheels
((Get-Content audio\wsl\setup_wsl_offline.sh -Raw) -replace "`r","") | wsl -d Ubuntu-26.04 -u root bash
```

发行版/venv 路径在 `config/settings.json -> wsl` 配置。详细说明与已知限制见 `audio/wsl/README.md`。

## 免责声明

- 本项目为**学习与研究目的**的开源工具，仅使用正常的 Windows 输入模拟 API（SendInput / MCI），**不修改游戏、不注入进程、不读写游戏内存、不绕过任何反作弊机制**。
- 在装有反作弊系统的在线游戏中使用任何自动化工具都可能违反游戏服务条款并导致账号处罚。**本项目不保证使用后不被判定违规或封号**——"用别怕，怕别用"，风险由使用者自行承担。
- `songs/father.mid` 为我们按公开简谱人工转录的《父亲》（筷子兄弟）主旋律，仅作为功能演示；原曲版权归原作者所有，请勿用于任何商业用途。
- 本项目与《三角洲行动》(Delta Force) 及其发行商没有任何关联。

## 许可证

MIT（见 [LICENSE](LICENSE)）。`playback/win32_sendinput.py` 改编自 MIT 许可的 [pydirectinput](https://github.com/learncodebygaming/pydirectinput)（Copyright (c) 2020 Ben Johnson），归属见 [NOTICE.txt](NOTICE.txt)；调度/移调设计思想借鉴自 GPL-3.0 项目 MeowField_AutoPiano 与 AutoMidiPlayer（未复制代码，分析见 [RESEARCH.md](RESEARCH.md)）。
