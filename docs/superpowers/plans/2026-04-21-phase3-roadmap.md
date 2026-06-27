# Phase 3 实施路线图 — Backend Auto-Launch + 模型内置

**Date**: 2026-04-21
**Scope**: Phase 2 结束后的收官 sprint 组，目标 "用户下载 installer 双击就能用"。
**Precedes**: `v0.5.0-phase3-rc` 之后的所有版本，直到 `v1.0.0-ga`。
**Status**: **SIGNED-OFF**（2026-04-21 用户签字）

**Signed-off decisions**（2026-04-21）：
- ✅ Scope = **0（Backend auto-launch）+ 2（模型塞进 installer, A 方案）**
- ✅ Phase 2 延后项（PersonaRegistry / 跨平台 keyring / BillingLedger UI）**不做**，推迟到 Phase 4
- ✅ D3-0b = **A**：模型全塞进 installer，无首启下载 UX
- ✅ D3-0c = **不支持 CPU-only 用户**，硬门槛 NVIDIA GPU
- ✅ D3-0a = **B**：PyInstaller `--onedir`（体积/调试平衡，默认推荐）

---

## 0. 文档定位

和 `2026-04-14-phase2-v6-roadmap.md` 同级，属于 **sprint-级 roadmap**。每个
slice 开工前仍需按 `sp-writing-plans` 产出独立 plan 文档
（`docs/superpowers/plans/YYYY-MM-DD-p3s<N>-<slice>.md`）。

本文档职责：
1. 锁定 Phase 3 验收门
2. 把"Backend 自动启动 + 模型内置"拆成有依赖顺序的 slice
3. 给出发布节奏
4. 标出每个 slice 开工前需拍板的小决策
5. 声明 Phase 3 里**刻意不做**的事情

---

## 1. Phase 3 验收门（Phase 1/2 全部继承 + 新增）

| # | 门 | 测量机制 | 目标 |
|---|---|---|---|
| P3-G1 | 从 installer 安装到第一次 TTS 回复 | 手测（秒表） + `scripts/perf/cold_boot.py` 记录 | ≤ 90 秒（含 backend 冷启动 + Whisper/Silero/CosyVoice load） |
| P3-G2 | Installer 体积 | CI `ls -la` 检查产物 | ≤ 3.5 GB（含 faster-whisper 1.6GB + silero + CosyVoice + PyInstaller 打包的 torch/CUDA runtime） |
| P3-G3 | Backend 崩溃自愈 | 手测注入 `os._exit(1)` 的测试 build | 10 秒内自动重启、WebSocket 重连成功、用户端丢失 ≤ 1 轮对话 |
| P3-G4 | 无 N 卡机器启动 | 无 NVIDIA GPU 环境测试 | 弹出明确错误对话框 + 退出，**不得**静默挂起 |
| P3-G5 | 二次启动时间 | 手测（秒表） | ≤ 15 秒（冷启动后模型已 warm，仅需 backend 启动 + WebSocket 连接） |
| P3-G6 | 卸载残留 | Windows Apps & Features 卸载后检查 | ≤ 10MB（日志/config 保留到 `%AppData%\deskpet`，模型和二进制全清） |

**验收原则：** 任一门未达标 = Phase 3 不能打 `v1.0.0-ga`。

---

## 2. 架构总览

### 2.1 当前状态（Phase 2 结束）

```
用户操作                         DeskPet 架构
------------------------------  -----------------------------------
1. git clone deskpet             tauri-app/       (Tauri+React 前端)
2. cd backend && pip install     backend/         (Python FastAPI)
3. 下载模型到 backend/assets/    backend/assets/  (.gitignored, 手动装)
4. python main.py (独立窗口)     Backend on :8100
5. cd tauri-app && npm run tauri Tauri 连上 :8100，attach 已有进程
   dev （第二个窗口）
```

### 2.2 Phase 3 目标状态

```
用户操作                         DeskPet 架构
------------------------------  -----------------------------------
1. 下载 DeskPet-Setup.exe        单一 2.5-3.5 GB Windows installer
   (~3GB)                        ↓ 安装到 C:\Program Files\DeskPet\
2. 双击桌面快捷方式              Tauri (deskpet.exe) 启动
3. 等 ~60s 后桌宠出现            Tauri 内部：
                                 ├─ 检测 NVIDIA GPU（缺则弹窗退出）
                                 ├─ spawn bundled\backend\deskpet-backend.exe
                                 │  (PyInstaller --onedir 产物)
                                 │  env: DESKPET_MODEL_DIR=bundled\models\
                                 ├─ 等 SHARED_SECRET 从 stdout 出来
                                 ├─ 连 WebSocket
                                 └─ Live2D 渲染
```

### 2.3 技术选型决策（全部已签字）

| 决策 | 选择 | 备选 | 理由 |
|---|---|---|---|
| Python 打包 | PyInstaller `--onedir` | onefile / Nuitka / embedded | 调试友好（堆栈正常）+ 启动快（无解压）+ 体积可控（比 onefile 大 20% 但比 embed 小） |
| 模型分发 | 全塞 installer | 首启下载 / CDN | 开箱即用，"安装完就能用"远比"首启再等 5 分钟"体验好 |
| CPU 支持 | 不支持 | CPU fallback | 维护成本大，CPU 上 Whisper 5-10s 延迟体验糟糕，且 Phase 2 架构已锁 CUDA |
| 端口 | 仍然 :8100 | 动态分配 | Phase 2 全链路硬编码，改端口触及面太大，Phase 4 再考虑 |
| 安装路径 | `C:\Program Files\DeskPet\` | `%LocalAppData%` | 需要管理员权限但系统规范，Phase 4 再考虑 per-user 安装 |
| Installer 工具 | Tauri 自带 WiX + NSIS | Inno Setup 等 | Phase 2 `release.yml` 已跑通 WiX 流程，沿用即可 |

### 2.4 目录结构（安装后）

```
C:\Program Files\DeskPet\
├── deskpet.exe                 # Tauri 主窗口（~150MB）
├── WebView2Loader.dll
├── resources\
│   ├── icons\
│   └── dist\                   # 前端 JS/CSS bundle
└── backend\                    # PyInstaller --onedir 产物
    ├── deskpet-backend.exe     # Python 启动器
    ├── python311.dll
    ├── _internal\              # CPython 标准库 + site-packages
    │   ├── ctranslate2\
    │   ├── faster_whisper\
    │   ├── numpy\
    │   ├── torch\              # ~1.2GB（GPU 相关 CUDA runtime）
    │   └── ...
    ├── models\                 # 本 Phase 新增，所有模型统一放这
    │   ├── faster-whisper-large-v3-turbo\   # ~1.6GB
    │   ├── silero_vad\                      # ~50MB
    │   └── cosyvoice2\  (可选)              # ~500MB
    └── config.toml             # 出厂默认配置

%AppData%\deskpet\              # 用户数据（卸载保留）
├── config.toml                 # 用户定制（覆盖 bundle 的默认）
├── data\
│   ├── memory.db
│   └── billing.db
└── logs\
    └── backend.log
```

---

## 3. Sprint 拆解

Phase 3 只有一个 sprint，按依赖顺序拆 slice。估时基于 subagent-driven-development 模式下的 calendar time。

### 3.1 Sprint P3 — Backend Auto-Launch + 模型内置（目标版本 `v0.5.0-phase3-rc1`）

**时长估算：** 3-4 周

**前置条件：**
- `master` 绿（Phase 2 收官状态，已满足）
- 签字决策（已完成，见文档开头）
- 本地至少一台 NVIDIA 测试机 + 一台无 N 卡测试机（用于 P3-G4）

#### Slice 清单

| Slice | 内容 | 估时 | 阻塞 |
|---|---|---|---|
| **P3-S1 模型目录收拢 + config 分离** | 把 `backend/assets/*` 改名 `backend/models/*`；`[asr]` / `[tts]` / `[vad]` 加 `model_dir` 配置项；`main.py` 全部用 env / config 读目录，不再硬编码 `__file__ / "assets"` | 2 天 | 无 |
| **P3-S2 CUDA 前置检查** | Rust 侧 `gpu_check.rs`：启动前探测 NVIDIA GPU（走 `nvml-wrapper` crate 或最差 `nvidia-smi.exe`）；缺失则弹 dialog + 退出；backend 侧 `faster-whisper load` 失败也要回传结构化错误给前端显示 | 2 天 | 无 |
| **P3-S3 Supervisor 自管 backend 路径** | 当前 `start_backend` 接收 `python_path` + `backend_dir` 参数；改成自动解析 `tauri::path::resource_dir() / "backend" / "deskpet-backend.exe"`；移除前端参数；加"开发模式 vs 打包模式"分支（dev 仍走 .venv/python + main.py） | 3 天 | P3-S1 |
| **P3-S4 PyInstaller 打包脚本** | `scripts/build_backend.ps1`：`pyinstaller --onedir backend/main.py`；spec 文件显式列 `datas` = 所有 provider 必要的 .py + silero jit + faster-whisper config 文件；测试冷启动能跑 + 能读模型 | 4 天 | P3-S1 |
| **P3-S5 Tauri bundle 吸纳 backend 产物** | `tauri.conf.json::bundle.resources` 加 `backend/dist/`；`tauri.conf.json::bundle.externalBin` 注册 `deskpet-backend.exe`；验证安装包体积 + 安装后路径正确 | 2 天 | P3-S3, P3-S4 |
| **P3-S6 模型文件纳入 bundle** | 在 P3-S4 的 PyInstaller datas 里显式 include `backend/models/*`（当前 .gitignored，CI 从 Release asset 下载或走 LFS）；更新 CI `release.yml` 拉取模型到 `backend/models/`；体积门检查 | 3 天 | P3-S1, P3-S4 |
| **P3-S7 用户数据目录迁移** | `%AppData%\deskpet\`：config.toml 优先读 AppData，fallback 到 bundle；SQLite db_path 从相对改成 `%AppData%\deskpet\data\`；logs 同理；首启做一次迁移（从老的相对路径） | 3 天 | P3-S1 |
| **P3-S8 启动时序 + 错误 UI** | Tauri 启动后的 splash screen："正在启动语音服务..."；spawn 失败 / GPU 缺失 / 端口占用 / SHARED_SECRET 超时 都弹明确错误 dialog（中文）；日志一键打开按钮 | 2 天 | P3-S2, P3-S3 |
| **P3-S9 卸载残留清理** | WiX uninstall 脚本：`C:\Program Files\DeskPet\*` 全删；`%AppData%\deskpet\` **不删**（保留用户对话历史 / 预算）；SettingsPanel 加"完全卸载"按钮触发 AppData 清理 | 1 天 | P3-S7 |
| **P3-S10 端到端 installer smoke** | 全新 Windows 10/11 虚拟机：下载 MSI → 安装 → 双击 → 用户对话 → 卸载；记录每步耗时；`scripts/perf/cold_boot.py` 重跑验证 P3-G1 | 2 天 | P3-S9 |
| **P3-S11 release + 版本号** | 打 `v0.5.0-phase3-rc1`；release notes 写清 "仅支持 NVIDIA GPU" 硬门槛；跑 Updater 密钥轮换后的第一次签名验证 | 1 天 | P3-S10 |

**合计：** ~25 个工作日（3-4 周 calendar time）。

#### 开工前决策点（本 sprint 已全部闭合）

- ~~**D3-0a 打包方式**~~ ✅ PyInstaller --onedir（2026-04-21）
- ~~**D3-0b 模型分发**~~ ✅ 全塞 installer（2026-04-21）
- ~~**D3-0c CPU 支持**~~ ✅ 不支持（2026-04-21）
- ~~**D3-0d Supervisor 自管**~~ ✅ 必做（2026-04-21）
- ~~**D3-0e Scope**~~ ✅ 仅 0 + 2（2026-04-21）

#### 滚动决策（slice 开工时确认）

- **D3-S4a**：PyInstaller 如何处理 `torch` + CUDA runtime 依赖？候选：
  1. 依赖系统 CUDA（用户需装 CUDA 12.x runtime）—— 体积小但门槛高
  2. Bundle CUDA runtime DLL（`nvrtc`, `cublas`, `cudnn`）—— 体积大但开箱即用
  - **我的倾向**：2 —— 和"模型塞 installer"哲学一致
- **D3-S6a**：模型文件走 Git LFS 还是 Release asset？候选：
  1. LFS —— 开发者 clone 自动拉，但 GitHub LFS 有月流量上限（1GB free）
  2. Release asset —— CI 步骤显式下载，但开发者手动拉
  - **我的倾向**：2 —— 避免 LFS 配额爆炸；开发者有 `scripts/fetch_models.ps1` 一键拉
- **D3-S9a**：是否在卸载时清 `%AppData%\deskpet\`？候选：
  1. 不清（推荐，用户重装时保留聊天历史）
  2. 清（彻底干净但破坏性）
  - **我的倾向**：1 + SettingsPanel 加"完全卸载"按钮

#### 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| PyInstaller 漏打包动态 import（ctranslate2 plugin、silero torch hub） | backend 打包后跑不起来 | P3-S4 实机 smoke；spec 文件显式 `hiddenimports` 列白名单 |
| CUDA runtime DLL 冲突（用户系统已装不同版本） | 随机崩溃 | 所有 CUDA DLL 都用 bundle 版本，`PATH` 前置；文档说明 |
| Installer 体积爆炸超过 P3-G2（3.5GB） | 下载体验糟 | P3-S5 + P3-S6 每个 commit 都检查体积；超标即 revert |
| Whisper jit pt 文件反序列化在 frozen 环境失败 | ASR 不可用 | P3-S4 强制测试 `transcribe()` 实跑一条音频才算 green |
| Updater 签名 + 大体积 installer 在 GitHub Release CI 超时 | 发布失败 | CI 步骤加超时提升，必要时走 GitHub Actions 自建 runner |
| 无 N 卡用户反馈负评 | 品牌影响 | P3-G4 明确弹窗 + README 显眼"NVIDIA Only"标识 |

---

## 4. 发布路线图

| 版本 | 里程碑 | 阻塞 |
|---|---|---|
| `v0.5.0-phase3-rc1` | Installer 可双击运行、自动启动 backend、模型内置 | P3-S1..S11 |
| `v0.5.x-phase3-rc2` | 用户反馈 bugfix 迭代（如果需要） | rc1 公测 |
| `v1.0.0-ga` | Phase 1/2/3 全部验收门 PASS + 1 周公测无 BLOCKER | rc 稳定 |

---

## 5. Phase 3 里**刻意不做**

下列项目全部推迟到 **Phase 4** 或更远：

| 项目 | 为什么延后 |
|---|---|
| CPU-only 支持 | 维护成本 vs 收益不合算；Phase 2 架构全链路 CUDA 假设 |
| Mac / Linux 支持 | 安装器工具链完全不同；单平台先稳住 |
| Per-user 安装（不要管理员） | Tauri + WebView2 在非管理员安装路径下有已知问题 |
| 自动更新到 `v0.5.x` | Updater 签名已就位，但 v0.2.0 用户需手动升（密钥已轮换）；作为 release notes 说明 |
| PersonaRegistry + 多角色 | 桌宠产品定位单角色已够；Phase 4 再评估商业价值 |
| 跨平台 keyring（Mac/Linux） | 没跨平台需求就不做 |
| BillingLedger UI 历史面板 | 有 SQLite 数据就能查，UI 是锦上添花 |
| `cost_aware` / `latency_aware` 路由 | `local_first` 已覆盖所有真实需求 |
| 自研 Live2D 渲染器 | 无限延后（P2-∞） |

---

## 6. 流程规范（per-slice）

继承 Phase 1/2 codingsys 规范：

1. **Brainstorm**（slice 跨 Rust + Python + CI 时强制）—— `sp-brainstorming`
2. **Spec-first** —— 每 slice 独立 plan 文档
3. **Worktree 隔离** —— `EnterWorktree` 独立分支
4. **Subagent-driven 执行** —— fresh implementer per task + spec compliance review
5. **Auto-verify** —— 两层验证循环
6. **E2E regression** —— CDP 套件 + `cold_boot.py` 不允许退化
7. **Final code review** —— code-reviewer agent 对整 slice
8. **HANDOFF 文档** —— `docs/superpowers/handoffs/p3s<N>-<slice>.md`
9. **Commit + tag**
10. **ExitWorktree + 主线 merge**

**Phase 3 特有规范：**
- **每个 slice 最后必须 smoke 测**打包路径（即便 slice 只改 Python 代码，也要跑一次 `pyinstaller` 确认没破坏打包）
- **体积监控**：每个 commit 到 `master` 后，CI 要把 installer 体积落入 `docs/PERFORMANCE.md`；单次增长 > 100MB 需人工 review

---

## 7. 文档地图（Phase 3 预期产物）

- `docs/superpowers/plans/2026-04-21-phase3-roadmap.md` — 本文档
- `docs/superpowers/plans/YYYY-MM-DD-p3s<N>-<slice>.md` — 每 slice 一份
- `docs/superpowers/handoffs/p3s<N>-<slice>.md` — 每 slice 一份 handoff
- `docs/PACKAGING.md`（新）— PyInstaller + Tauri bundle 全流程 + 故障排查
- `docs/PERFORMANCE.md` — 补 P3-G1/G2/G5 章节
- `docs/INSTALL.md`（新）— 面向终端用户的安装/卸载/GPU 要求说明
- `docs/superpowers/plans/2026-XX-XX-phase3-handoff.md` — Phase 3 GA 总 handoff

---

## 8. 下一步

**已拍板：** D3-0a/b/c/d/e 全部闭合（2026-04-21）

**立即动作**：
1. 用户签字本路线图 → 状态切 SIGNED-OFF
2. 启动 **P3-S1 模型目录收拢 + config 分离**（第一个 slice，是所有后续 slice 的前提）
3. 按 §6 流程：`sp-writing-plans` 产出 `docs/superpowers/plans/2026-04-XX-p3s1-model-dir-config.md` → worktree 隔离 → subagent 执行

**状态：** **SIGNED-OFF（2026-04-21）** —— 开始 P3-S1。
