# WSL MELODIA 提取环境（音频主线暂缓，环境已就绪）

Essentia 在 PyPI 上**没有任何 Windows wheel**，因此 MELODIA 主旋律提取在
WSL 内以独立子进程运行：Windows 侧把音频路径转成 `/mnt/c/...`，WSL 内的
`melodia_extract.py` 输出 F0 轮廓 JSON，Windows 侧的 `music/quantizer.py`
完成全部可调参的后处理（中值滤波/滞回/分段/合并）。

## 本机现状

- 发行版：`Ubuntu-26.04`（Python 3.14.4，恰好匹配 essentia 唯一的 cp314 manylinux wheel）
- venv：`~/dmp-melodia`（essentia 2.1b6.dev1438 + numpy 2.5.3 + six）
- **已安装并验证可用**（`PredominantPitchMelodia` 导入成功，提取管线跑通）

## 为什么是离线安装

本机 WSL 网络已损坏：`.wslconfig` 配置了 `networkingMode=Mirrored`，但
mirrored 网络启动失败（`ConfigNetworking/0x8007054f`）并回退为无网络模式，
WSL 内 apt/pip 全部不可用。因此采用离线方案：

1. Windows 侧（网络正常）下载 Linux wheel：
   ```powershell
   python audio\wsl\fetch_wsl_wheels.py wsl_wheels
   ```
2. WSL 内以 root 建 venv 并解包 wheel（wheel 就是 zip，解包进 site-packages
   等价于 pip 对纯二进制包的安装动作）：
   ```powershell
   ((Get-Content audio\wsl\setup_wsl_offline.sh -Raw) -replace "`r","") | wsl -d Ubuntu-26.04 -u root bash
   ```

如需修复 WSL 网络本身：编辑 `C:\Users\<user>\.wslconfig` 移除/更换
`networkingMode=Mirrored` 后 `wsl --shutdown` 重启（会影响所有发行版，未做）。

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

产物：`output/song_contour.json`（F0 轮廓）、`song_notes.json`、`song_melody.mid`。

## 已知问题（暂缓处理）

在**合成音**（纯正弦/简单泛音叠加）测试中，MELODIA 对部分音符输出
conf=0（完全 unvoiced），且同参数下结果不完全一致——合成音缺乏真实
歌声/乐器的谐波结构，处于算法的病态输入区。真实歌曲上的效果待主线
（MIDI→演奏）验收后回头评估；届时可调
`settings.json -> melody -> confidence_threshold / merge_gap / ...`。
