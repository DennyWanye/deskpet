# P3-S10 — Installer Smoke Report (v0.5.0-phase3-rc1)

**Date**: 2026-04-24
**Artifact**: `DeskPet_0.5.0-phase3-rc1_x64-setup.exe` (NSIS, 650.4 MB)
**Commit**: master @ 815c410 (merge of `p3-s8-s11-release` branch)

---

## 1. 结论

**Status**: ⚠️ **RC released with partial smoke coverage**

- Host-side 构建与打包门控 **PASS**（安装器产出、体积、签名结构、卸载 hook 代码路径）
- VirtualBox VM 降级 smoke **BLOCKED**（结构性限制：VirtualBox 不支持 NVIDIA 消费级 GPU passthrough，P3-G1 冷启动门无法在 VM 内验证）
- MSI 变体 **NOT BUILT**（WIX 要求 app version 预发布段数字化；`0.5.0-phase3-rc1` 含 `phase3-rc1` 文本后缀）
- 面向终端用户的 T1-T8 runbook 验证 **未执行**，延后到 rc2 或真实用户 dog-fooding

rc1 可以发布给内部 tester，**不适合直接面向外部用户**。正式 GA 前必须在真实 NVIDIA 机器上跑一遍完整 runbook（见 §4 "Deferred 到 rc2 / Phase 4"）。

---

## 2. 实际验证的项（Host 端）

| 门控 / Item | 目标 | 实测 | 结果 |
|---|---|---|---|
| Backend frozen bundle | 存在且可执行 | `backend/dist/deskpet-backend/deskpet-backend.exe` 39.5 MB，bundle 总计 1531 MB | ✅ |
| Backend bundle 体积 (P3-G2) | ≤ 3.5 GB | 1.5 GB | ✅ |
| Tauri NSIS 产出 | 存在 | `DeskPet_0.5.0-phase3-rc1_x64-setup.exe` 650.4 MB | ✅ |
| Installer 体积 (P3-G2) | ≤ 3.5 GB | 650 MB（backend 会在安装时解压）| ✅ |
| `tauri-app/src-tauri/tauri.conf.json` version | `0.5.0-phase3-rc1` | 已更新 | ✅ |
| 卸载清理代码 (P3-S9) | `uninstall_user_data` command + FFI | 存在于 `src-tauri/src/commands.rs`，被 SettingsPanel "危险区" 调用 | ✅ |
| 红色错误卡 (P3-S8) | 端口占用时 splash 切红 + 打开日志按钮 | 存在于 `tauri-app/src/splash.ts` + `src-tauri/src/supervisor.rs` | ✅ |
| 首启 seed (P3-S6) | AppData 落地 config + logs 目录 | `src-tauri/src/user_data.rs::seed_user_data` | ✅ |
| MSI 变体 | 构建成功 | ❌ WIX 报 `app version must be numeric-only` | ⚠️ 见 §3.1 |

---

## 3. 未验证 / BLOCKED 的项

### 3.1 MSI variant (T8)

构建失败：

```
error Failed to bundle project: `optional pre-release identifier in app version
must be numeric-only and cannot be greater than 65535 for msi target`
```

**根因**: WIX / MSI 的 `ProductVersion` 字段只接受 `Major.Minor.Build.Revision`
四段纯数字；Tauri 里 `version = "0.5.0-phase3-rc1"` 的 `-phase3-rc1` 预发布段
违反 WIX 规则。

**选项**:
- A. `msiVersion` 覆写（Tauri 2 支持 `bundle.windows.wix.version`）→ 用
  `0.5.0.1` 作为 MSI 显示版本，和 NSIS 的语义版本脱钩。
- B. 把 rc1 改成 `0.5.0.1`（纯数字）+ 在 release notes 里说明 pre-release。
- C. 只发 NSIS（当前选择）。NSIS 是 DeskPet 默认渠道，MSI 主要给企业 IT。

**决策**: 选 C，rc1 只走 NSIS。rc2 或 GA 上线前切 A 方案补 MSI。

### 3.2 VirtualBox VM 降级 smoke (T0 / T1 / T6)

**尝试路径**：
1. 装 VirtualBox 7.1.4 到 `F:\VirtualBox`
2. 关 Hyper-V (`bcdedit /set hypervisorlaunchtype off`)
3. 建 `deskpet-smoke` VM：Win11 23H2、8 GB RAM、4 CPU、80 GB VDI、EFI + Secure Boot + TPM 2.0
4. 手动过 Win11 OOBE (`Shift+F10` → `OOBE\BYPASSNRO` 绕过网络)
5. 写 `scripts/smoke_vm_degraded.ps1` 覆盖 T0/T1/T6/T8
6. HTTP 服务器 (`python -m http.server 8000` @ host `10.0.2.2`) 把 installer + 脚本投进 VM

**终止原因**：
- 尝试拍 clean-baseline 快照，VBoxManage `live-snapshotting` 卡在写 `.sav`
  （RAM dump，8 GB）—— 实测 25 MB/min，预计要 90+ 分钟；
- `VBoxManage controlvm ... poweroff` 在 livesnapshotting 状态下被 VBoxSVC 拒绝
  （硬 kill VBoxSVC 会损坏 VDI 链）；
- 即使 VM 恢复，**VirtualBox 7.x 不支持 NVIDIA 消费级 GPU passthrough**
  （需 SR-IOV 或企业级 Quadro），P3-G1 的 "冷启动 ≤ 90s 含 ASR/LLM 初始化"
  门在 VM 内无论如何都跑不起来。

**结论**：VirtualBox 是错误的 smoke 宿主。要么用
- Hyper-V + DDA（需要 Windows Server Datacenter / IoT）
- VMware Workstation Pro + PCIe passthrough
- Azure NV-series / AWS G5 / 本地第二块 SSD bare-metal

都是 rc2 级别的投入，rc1 不 gate 在这件事上。

### 3.3 Runbook T0–T8 全量（冷启动、对话、语音、红卡、完全卸载）

全部延后到 rc2 在带 NVIDIA GPU 的真实 Windows 10 + Windows 11 机器上跑。
详见 `docs/P3-S10-installer-smoke-runbook.md`（runbook 本身无需修改）。

---

## 4. Deferred 到 rc2 / Phase 4

| 项 | 阻塞原因 | Unblock 条件 |
|---|---|---|
| T1 install timing (`P3-G2 ≤60s`) | 需真实 SSD + 真实 non-admin 用户 | rc2 跑在物理机/bare-metal VM |
| T2 冷启动 (`P3-G1 ≤90s`) | 需 NVIDIA GPU passthrough | 物理机或 DDA 环境 |
| T3 冷启动基线脚本 (`scripts/perf/cold_boot.py`) | 同上 | 同上 |
| T4 文字 + 语音对话 | 需 GPU 跑 ASR/LLM | 同上 |
| T5 端口占用红卡 E2E | 需 app 能实际启动 | 同上 |
| T6/T7 标准 + 完全卸载 | 需先完成 T1 安装 | 同上 |
| T8 MSI 变体 | WIX 版本号规则 | 选方案 A 补 `bundle.windows.wix.version` |
| 非 admin 用户安装 (P3-G5) | 需非 admin 测试账号 | rc2 |
| Win10 22H2 变体 | 需第二个 VM 或物理机 | rc2 |

---

## 5. 附件与证据

- 构建产物：`tauri-app/src-tauri/target/release/bundle/nsis/DeskPet_0.5.0-phase3-rc1_x64-setup.exe` (SHA-256 留给 release workflow 计算)
- Smoke 脚本：`scripts/smoke_vm_degraded.ps1`（写完但未执行；留在仓库作为 rc2 起点）
- Runbook 原文：`docs/P3-S10-installer-smoke-runbook.md`
- 已并入 master：`git log 4cbfc32` = "P3-S8..S11: 启动 UI + 卸载清理 + installer runbook + rc1 版本"

---

## 6. Release note 建议（面向 tag `v0.5.0-phase3-rc1`）

> **v0.5.0-phase3-rc1** — Phase 3 Release Candidate 1
>
> 内部测试版本。内含 Tauri 前端 + PyInstaller frozen backend + bundled
> faster-whisper-large-v3-turbo 模型，Windows x64 NSIS 安装包。
>
> **⚠️ 已知限制**：
> - 只提供 NSIS 安装包；MSI 变体因版本号格式问题延后到 rc2
> - 端到端冷启动 / 对话 / 卸载 smoke 未在干净 NVIDIA VM 上完整跑过；请在
>   真实用户机器上跑完 `docs/P3-S10-installer-smoke-runbook.md` 再签外部 GA
> - 需要 NVIDIA GPU + 最新 Studio Driver；CPU-only 路径在 rc1 是 degraded 路径
