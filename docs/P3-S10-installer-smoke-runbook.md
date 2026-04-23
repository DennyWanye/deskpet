# P3-S10 — Installer Smoke Runbook

**Purpose**: 在全新 Windows 10/11 虚拟机上端到端验证 `v0.5.0-phase3-rc1`
的安装、启动、卸载流程。一次完整跑通即视为 P3-G1（冷启动 ≤ 90s）+
P3-G3（卸载不留垃圾）+ 体积门验证过关。

**Est. 60 分钟**（不含模型下载 / 上传 VM 镜像时间）。

---

## 0. 准备

### 0.1 虚拟机

- VMware / Hyper-V / Parallels 建一台全新 **Windows 10 22H2** 和一台
  **Windows 11 23H2**。关键属性：
  - ≥ 8 GB RAM，≥ 40 GB 空闲磁盘
  - 透传 **NVIDIA GPU**（Hyper-V 用 DDA，VMware 用 PCI passthrough）
  - 安装最新 NVIDIA Studio 驱动（含 `nvml.dll`）
  - **不**预装 CUDA Toolkit（installer 要能自己扛）
  - **不**预装 Python 3.x
- 登录一个普通非 Administrator 账号（P3-G5 验证点）

### 0.2 Installer 产物

从本地仓库构建：

```powershell
# 主仓库根（不是 worktree）
powershell scripts/build_backend.ps1
cd tauri-app
npm run tauri build
```

产出：
- `tauri-app/src-tauri/target/release/bundle/msi/DeskPet_0.5.0-phase3-rc1_x64_en-US.msi`
- `tauri-app/src-tauri/target/release/bundle/nsis/DeskPet_0.5.0-phase3-rc1_x64-setup.exe`

两种都要在 VM 里跑一遍。先从 NSIS 开始（用户默认优先选它）。

---

## 1. Checklist（每个 VM 各跑一次）

### T0 — 安装前

```powershell
# VM 内：基线指标
Get-ComputerInfo | Select-Object WindowsProductName,OsBuildNumber,TotalPhysicalMemory
# AppData 干净
Test-Path $env:APPDATA\deskpet      # 期望 False
Test-Path $env:LOCALAPPDATA\deskpet # 期望 False
```

### T1 — 安装（记录耗时）

1. 双击 `DeskPet_...setup.exe`
2. 计时从双击到 "Finish" 按钮可点 → **期望 ≤ 60s**（installer 本身体积
   ~1.5 GB，SSD 写入是瓶颈）
3. 勾选 "Run DeskPet"，点 Finish

```powershell
# 验证 Program Files 布局
Get-ChildItem "$env:ProgramFiles\DeskPet" -Recurse -Depth 2 | Measure-Object -Property Length -Sum
# 期望：~1.5 GB；包含 deskpet.exe + backend\ + WebView2\
```

### T2 — 首次启动（P3-G1 冷启门）

1. 启动时计时从 `deskpet.exe` 开始到 **splash overlay 消失 + 能看到
   Live2D 窗口** → **期望 ≤ 90s**（P3-G1）
2. Splash 应持续显示 "正在启动语音服务…"（P3-S8 验收点）
3. `%AppData%\deskpet\config.toml` 被 seed
4. `%LocalAppData%\deskpet\models\` 已自动包含模型（P3-S6 bundle 内含）

```powershell
Test-Path $env:APPDATA\deskpet\config.toml                          # True
Test-Path $env:APPDATA\deskpet\logs                                 # True
Test-Path $env:LOCALAPPDATA\deskpet\models\faster-whisper-large-v3-turbo # True
```

### T3 — 冷启动基线脚本

VM 里安装 Python 3.11（只为跑基线脚本），然后：

```powershell
# 把本 repo 的 scripts/perf/cold_boot.py 传进 VM
# 对着 installer 安装的 frozen exe 跑，--runs 3 即可
python .\scripts\perf\cold_boot.py `
    --cmd "$env:ProgramFiles\DeskPet\backend\deskpet-backend.exe" `
    --runs 3 --gate-sec 90
```

**期望** `p95 <= 90s` → 退出码 0 → P3-G1 PASS

### T4 — 对话功能（文字 + 语音）

1. DeskPet 窗口，打字 "你好" → Enter → 期望 AI 文字 + TTS 语音回复
2. 点麦克风 🎤 → 说 "今天天气怎么样" → 期望 ASR 识别 + AI 回复 + TTS 播放
3. 开 SettingsPanel（⚙）→ 确认云端账号 UI 能打开，今日使用有数据

### T5 — 端口占用错误路径（P3-S8 验收点）

1. 另起一个终端：`python -m http.server 8100`（占用 8100）
2. 关闭 DeskPet，重新启动 `deskpet.exe`
3. **期望** splash 变成红色错误卡片，显示 "端口 8100 已被其它程序占用…"
4. 点 "打开日志目录" → Explorer 弹出 `%AppData%\deskpet\logs`
5. 点 "退出"（或关闭 8100 占用后点 "重试"）

### T6 — 正常卸载（P3-S9 验收点）

1. "应用和功能" → "DeskPet" → 卸载
2. 期望 `C:\Program Files\DeskPet\` 全空
3. `%AppData%\deskpet\` **保留**（聊天历史 / config / 日志）
4. `%LocalAppData%\deskpet\models\` **保留**

```powershell
Test-Path "$env:ProgramFiles\DeskPet"    # False
Test-Path $env:APPDATA\deskpet           # True ← 重要：默认保留
Test-Path $env:LOCALAPPDATA\deskpet      # True
```

### T7 — 完全卸载（P3-S9 验收点）

1. 重装 DeskPet
2. 启动 → ⚙ → "危险区" → 勾选 "同时删除 …/models"
3. 点 "完全卸载（清除用户数据）" → 两步确认
4. DeskPet 自动退出
5. 再次 "应用和功能" → 卸载 DeskPet

```powershell
Test-Path $env:APPDATA\deskpet           # False
Test-Path $env:LOCALAPPDATA\deskpet      # False
Test-Path "$env:ProgramFiles\DeskPet"    # False
```

### T8 — MSI variant

用 MSI 再跑一遍 T1–T7。MSI 和 NSIS 行为应一致。

---

## 2. 失败 triage

| 现象 | 初步排查 |
|---|---|
| 安装后启动即闪退 | `%AppData%\deskpet\logs\*.log` → 搜 traceback；CUDA DLL 缺失看 GPU 对话框 |
| Splash 一直转不出来 | Task Manager 里看 `deskpet-backend.exe` CPU，如果 0% 多半 hang 在 import；90s 后应该自动超时弹红卡 |
| /health 不响应 | 端口冲突（T5 路径）；或 backend 崩了但 supervisor 还在重启（日志搜 `backend-restarted`） |
| 卸载后 AppData 还在 | 这是 **by design**，T7 才清 |
| 冷启动 > 90s | 先把 `%LocalAppData%\deskpet\models\` 删掉看首次触发模型解压是否慢；若是，档到 Phase 4 做"预热"优化 |

---

## 3. 验收表

| 项 | 目标 | 实测 (Win10) | 实测 (Win11) |
|---|---|---|---|
| Installer 体积 | ≤ 3.5 GB (P3-G2) | ___ MB | ___ MB |
| Install 耗时 | ≤ 60s | ___ s | ___ s |
| 冷启 p95 | ≤ 90s (P3-G1) | ___ s | ___ s |
| 文字对话 | OK | ☐ | ☐ |
| 语音对话 | OK | ☐ | ☐ |
| 端口占用 → 红卡 + 打开日志 | 显示中文错误 | ☐ | ☐ |
| 标准卸载保留 AppData | True | ☐ | ☐ |
| 完全卸载清空 AppData + Local | 全部 False | ☐ | ☐ |
| MSI / NSIS 行为一致 | 两种 installer T1–T7 都过 | ☐ | ☐ |

填完粘回 PR / Slice HANDOFF 即算本 slice 手测完成。

---

## 4. 注意事项

- **不要在宿主机上运行本 runbook** —— 宿主机已经装过 DeskPet，无法验证
  "全新 VM 冷启动" 场景。
- Updater endpoint 验证（签名校验）留到真正上 GitHub Release 后；本 slice
  不覆盖。
- macOS/Linux 跑不了本 runbook；Phase 3 明确只支持 Windows。
