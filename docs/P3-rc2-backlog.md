# P3 rc2 Backlog

**来源**: P3-S10 rc1 smoke 留下的缺口（见 `P3-S10-smoke-report-rc1.md`）
**优先级**: P2（不阻塞 Phase 4 启动，但 GA 前必须关掉）

---

## 必办

### rc2-T1: MSI 变体补齐
- 在 `tauri-app/src-tauri/tauri.conf.json` 加 `bundle.windows.wix.version = "0.5.0.1"` 覆写
- 或把全局 version 改成 `0.5.0.1`，pre-release 状态改到 CHANGELOG 描述
- 验证：`pnpm tauri build` 产出 `.msi` + `.exe` 两个 bundle

### rc2-T2: 真机 runbook 全量 smoke
需要带 NVIDIA GPU 的全新 Windows 环境。三条路线：
- **A**: 宿主机双系统 / 第二块 SSD（干净，廉价，但占物理机）
- **B**: 云 GPU VM（Azure NV6 ≈ $1.14/h，AWS G5）—— 按小时付费跑完销毁
- **C**: 找闲置笔记本重装 Win11

跑完 `docs/P3-S10-installer-smoke-runbook.md` T0-T8 全表，含 Win10 22H2 + Win11 23H2 两个版本。

### rc2-T3: 非 admin 用户安装验证（P3-G5）
- 在 runbook VM 里建普通账号（非 Administrator）
- 跑一遍 T1 NSIS 安装 —— 确认不需要 UAC 升权
- 当前 NSIS 默认是 per-user install，理论应该过；需要实测

### rc2-T4: Updater endpoint 签名校验
- rc1 没做：Tauri updater.json 的 signature 生成 + 验签链路
- rc2 上 GitHub Release 后：
  - `tauri signer sign <installer>` → `.sig` 文件
  - 写 `updater/latest.json` 指向 Release asset
  - 空仓 VM 装 rc1 → 触发 updater → 验证它能自动更新到 rc2

---

## 好 have

### rc2-T5: 更新 `docs/P3-S10-smoke-report-rc1.md` 结果表
跑完上面全量 smoke 后，把所有 `___ s` 空格和 `☐` 填上。

### rc2-T6: 写 `docs/P3-S10-smoke-report-rc2.md`
归档 rc2 轮次的完整实测。

---

## 放弃 / Phase 4 以后

- Linux / macOS 支持（Phase 3 明确只 Windows）
- ARM64 安装包
- Code signing（EV 证书 $400+/y，等有营收再说）

---

**Est. 总工作量**: ~1 天（含云 VM 配置 + 真机跑完 runbook + 回填报告）
**Trigger**: 收到"要发 GA"信号时启动
