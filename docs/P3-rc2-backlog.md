# P3 rc2 Backlog

**来源**: P3-S10 rc1 smoke 留下的缺口（见 `P3-S10-smoke-report-rc1.md`）
**优先级**: P2（不阻塞 Phase 4 启动，但 GA 前必须关掉）
**最后审计**: 2026-05-03（P4-S16 阶段同步）

---

## 必办

### rc2-T1: MSI 变体补齐 ✅ **配置侧已完成**（2026-05-03）

**状态**：配置已就绪，下一次 `git tag v0.6.0-phase4-rcX push` 触发 release.yml 即产出 MSI。

落地内容：
- ✅ `tauri-app/src-tauri/tauri.conf.json::bundle.targets` 已包含 `["nsis", "msi"]`
- ✅ 加 `bundle.windows.wix.version = "0.6.0.3"` 覆写（MSI/WiX 不接受 SemVer
  pre-release `-phase4-rc3`，需要纯 X.Y.Z.W 4 段数字）
- ✅ `.github/workflows/release.yml` 期望 4 个 artifact：
  `setup.exe / setup.exe.sig / .msi / .msi.sig`，缺一就 fail
- ✅ workflow 同时上传 NSIS + MSI 到 GitHub Release

**待验**（属 rc2-T2 真机实测）：
- 真 Windows 上 `npm run tauri -- build --bundles nsis msi` 能产出有效 MSI
- 双击 MSI 能装上 + 卸载干净

> 升级 rc 时要同步 bump：`tauri.conf.json::version` 和 `bundle.windows.wix.version`
> 两个字段。当前规则：phase 数字 → minor，rcN → build。下次 rc4 → `0.6.0.4`。

### rc2-T2: 真机 runbook 全量 smoke ⏳ 待真机
需要带 NVIDIA GPU 的全新 Windows 环境。三条路线：
- **A**: 宿主机双系统 / 第二块 SSD（干净，廉价，但占物理机）
- **B**: 云 GPU VM（Azure NV6 ≈ $1.14/h，AWS G5）—— 按小时付费跑完销毁
- **C**: 找闲置笔记本重装 Win11

跑完 `docs/P3-S10-installer-smoke-runbook.md` T0-T8 全表，含 Win10 22H2 + Win11 23H2 两个版本。

P4-S16 后新增的真机验收点（在原 T0-T8 之外）：
- ✅ BGE-M3 INT8 模型在真机 GPU 上能 warmup（rc3 在 RTX 4090 实测 5.78s）
- ⏳ 首字延迟 p50 < 1100ms（需配 LLM API key）
- ⏳ 冷启动 ≤ 90s 全链路（含 BGE-M3 + faster_whisper 同时加载）
- ⏳ MSI 安装 → 启动 → 触发 LLM 一轮 → 卸载 全跑通

### rc2-T3: 非 admin 用户安装验证（P3-G5）⏳ 待真机
- 在 runbook VM 里建普通账号（非 Administrator）
- 跑一遍 T1 NSIS 安装 —— 确认不需要 UAC 升权
- 当前 NSIS 默认是 per-user install，理论应该过；需要实测

### rc2-T4: Updater endpoint 签名校验 ✅ **签名链路已就绪**（2026-05-03 审计）

**状态**：完整签名生成 + 发布链路就位，待真机触发更新验证。

已落地：
- ✅ `tauri-app/src-tauri/tauri.conf.json::plugins.updater.pubkey` 已配置
  （minisign 公钥 `5F623E5CDBAA4C5A`）
- ✅ `bundle.createUpdaterArtifacts: true`
- ✅ `release.yml` 通过 `TAURI_SIGNING_PRIVATE_KEY` secret 自动签发 `.sig`
- ✅ `release.yml::Verify bundle artifacts exist` step 强制 4 个 artifact
  齐全（含 .sig）才允许发布
- ✅ `latest.json` manifest 由 workflow 生成，包含 signature + url

**待验**（在 rc2-T2 真机 smoke 时一并测）：
- 装 rc1（之前未签）→ 装 rc3（已签）→ updater 触发 → 公钥验签
  → 自动更新成功
- 无效签名 → updater 拒绝更新
- 阻断 endpoint（防火墙/DNS）→ 静默不更新（不 crash）

---

## 好 have

### rc2-T5: 更新 `docs/P3-S10-smoke-report-rc1.md` 结果表 ⏳ 待 T2 完
跑完上面全量 smoke 后，把所有 `___ s` 空格和 `☐` 填上。

### rc2-T6: 写 `docs/P3-S10-smoke-report-rc2.md` ⏳ 待 T2 完
归档 rc2 轮次的完整实测。**附**：因为 P4 已经超过 rc3，此报告应改名为
`P3-S10-smoke-report-v0.6.0-phase4-rc3.md` 一次性覆盖 P3 + P4 两阶段
真机 smoke。

---

## 放弃 / Phase 4 以后

- Linux / macOS 支持（Phase 3 明确只 Windows）
- ARM64 安装包
- Code signing（EV 证书 $400+/y，等有营收再说）

---

## 已解决（2026-05-03）

- ~~rc2-T1 配置侧~~ — MSI targets 配置 + WiX version 覆写完成
- ~~rc2-T4 配置侧~~ — minisign 签名链路 + verify step + manifest 生成全部就位

剩余真机实测部分由"收到要发 GA 信号"触发统一跑。

---

**Est. 剩余工作量**: ~1 天（云 GPU VM + 真机 runbook 全量 + 回填两份报告）
**Trigger**: 收到 "要发 GA" 信号或 P4 进 GA 阶段
