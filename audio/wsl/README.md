# WSL MELODIA 提取环境

Essentia 在 PyPI 上**没有任何 Windows wheel**，因此 MELODIA 主旋律提取在
WSL 内以独立子进程运行：Windows 侧把音频路径转成 `/mnt/c/...`，WSL 内的
`melodia_extract.py` 输出 F0 轮廓 JSON，Windows 侧的 `music/quantizer.py`
完成全部可调参的后处理（中值滤波/滞回/分段/合并）。

## 环境要求

- WSL2 + 一个 Python 版本与 essentia wheel 匹配的发行版
  （essentia 目前只发布 cp314 manylinux wheel，对应 Ubuntu 26.04 的
  Python 3.14；`config/settings.json -> wsl.distro` 可配置）
- 若 WSL 网络不可用，用下面的离线安装（推荐，普适）

## 安装（离线，无需 WSL 网络）

1. Windows 侧（网络正常）下载 Linux wheel：
   ```powershell
   python audio\wsl\fetch_wsl_wheels.py wsl_wheels
   ```
2. 在**项目根目录**执行（WSL 会继承当前 Windows 目录；脚本以 root 建
   `~/dmp-melodia` venv 并解包 wheel，wheel 即 zip，对纯二进制包等价于
   pip 安装）：
   ```powershell
   ((Get-Content audio\wsl\setup_wsl_offline.sh -Raw) -replace "`r","") | wsl -d Ubuntu-26.04 -u root bash
   ```
   默认用户取 UID 1000；可用环境变量覆盖：`TARGET_USER`、`WHEELS_WIN`。

WSL 网络正常时也可以直接在 WSL 里 `python3 -m venv ~/dmp-melodia &&
~/dmp-melodia/bin/pip install essentia numpy six`，效果相同。

> 提示：若你的 WSL 配置了 `networkingMode=Mirrored` 且启动报
> `ConfigNetworking` 错误回退为无网络模式，离线安装可完全绕开该问题；
> 修复网络本身需调整 `C:\Users\<user>\.wslconfig` 并 `wsl --shutdown`
> 重启（影响所有发行版）。

## 配置

`config/settings.json -> wsl`：

```json
{
  "distro": "Ubuntu-26.04",
  "python": "~/dmp-melodia/bin/python",
  "enabled": true,
  "timeout_seconds": 1800
}
```

## 使用

```powershell
python app.py song.mp3 --backend melodia --dry-run
```

产物：`乐库/song_contour.json`（F0 轮廓）、`song_notes.json`、
`song_melody.mid`（提取出的主旋律，可导入 DAW 核对）。

## 已知限制

- 在**合成音**（纯正弦/简单泛音叠加）上，MELODIA 对部分音符输出
  conf=0（完全 unvoiced），且同参数下结果不完全一致——合成音缺乏真实
  歌声/乐器的谐波结构，属于算法的病态输入区。真实歌曲效果请以
  `_melody.mid` 人工核对，并用 `settings.json -> melody` 下的
  `confidence_threshold / merge_gap / pitch_change_threshold /
  min_note_duration` 等参数调优。
- 完整歌曲（多乐器混音）的主旋律提取质量有限；可先用人声分离
  （如 Demucs）得到 vocals 再提取。
