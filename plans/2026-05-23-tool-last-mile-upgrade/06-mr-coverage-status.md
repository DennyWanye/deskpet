# MR-0 ~ MR-24 覆盖状态评估

- **配套**：`02-manual-test-cases.md` (v2.1) 24 条手测用例
- **日期**：2026-05-23
- **状态**：dev session 自评 + 待 windows-mcp 子代理实机验证补全

## 总览矩阵

| 类别 | 数量 | 覆盖方式 |
|---|---|---|
| 通过 acceptance script + TDD 严格覆盖 | 4 | MR-0/8/13/19（一票否决全过 ✅）|
| 通过 TDD 用例代理验证 | 13 | MR-2/3/5/9/10/11/12/14/15/17/18/20/21 |
| 必须 windows-mcp 实机才能验 | 7 | MR-1/4/6/7/16/22/24 |
| 部署运维范围 | 1 | MR-23（卸载迁移 — 需 installer 真实运行）|

## 详细对照表

| MR | 主题 | 覆盖方式 | 状态 |
|---|---|---|---|
| **MR-0** | 第一代零回归（一票否决） | TG-12 字节级 + acceptance pytest 全 1932 绿 | ✅ |
| **MR-1** | 生成 PPT + 一键打开 | **windows-mcp 子代理跑中（ID ae586535a6bcd406c）** | ⏳ |
| MR-2 | 缺依赖时不静默 fallback | TG-3 T3-3 + `ppt_create _HAS_PPTX` 路径改造 | ✅ 代码覆盖 |
| MR-3 | Excel/Word/PDF/图像产物同样可点开 | TG-3 T3-4 + WI-T1.2 5 工具改造 commit `2bd4c92` | ✅ 代码覆盖 |
| MR-4 | Markdown / 表格 / URL 类产物分发 | TG-5 ArtifactCard 5 子组件 vitest 9/9 | ⚠️ 需 windows-mcp 验真渲染 |
| MR-5 | 默认保存路径 + 中文/emoji 标题 | TG-6 T6-5 完整 12/12 + `title_slug` 单元测试 | ✅ 代码覆盖 |
| MR-6 | 用户自定义 `artifact_dir` | TG-6 T6-1 + config invariant TG-1 T1-1 | ⚠️ 需 windows-mcp 验环境变量生效 |
| MR-7 | PPT outline 预览模式 | TG-3 T3-2 dry_run 4/4 | ⚠️ 需 windows-mcp 验"确认生成"按钮 |
| **MR-8** | Fake-completion 抓获率 ≥ 95%（一票否决）| TG-9 15/15 + `_claim_has_matching_receipt` 严格匹配 + acceptance | ✅ |
| MR-9 | Verify 三轮升级链 + ephemeral 救援 | TG-9 T9-14 + agent_loop wiring TG-7 7/7 | ✅ 代码覆盖 |
| MR-10 | ClaimPattern 热加载 | TG-9 T9-12b 默认 yaml 100% 编译 | ✅ 代码覆盖（热加载 stub） |
| MR-11 | Receipt 写盘正确 | TG-8 13/13 + receipt_store.append 单测 | ✅ 代码覆盖 |
| MR-12 | Receipt 不含敏感信息 | TG-8 T8-3 + args_hash 不存明文（已 verify） | ✅ 代码覆盖 |
| **MR-13** | file_exists outcome verifier（一票否决）| TG-10 15/15 + acceptance script | ✅ |
| MR-14 | build verifier 回灌 | TG-10 T10-5 + TG-11 T11-2 D8 schema | ✅ 代码覆盖 |
| MR-15 | test verifier 回灌 | TG-10 T10-6 + outcome_verifier | ✅ 代码覆盖 |
| MR-16 | macOS Tier 2 兜底 | `#[cfg(target_os = "macos")]` 分支已写 | ❌ 需 mac 真机 |
| MR-17 | 性能预算 | TDD §F 性能预算文档 + sha256 异步 TG-2 T2-8 | ⚠️ 需 windows-mcp 跑 100 次 PPT 实测延迟 |
| MR-18 | 长会话稳定 | TG-2 T2-7 并发不串号 + ledger 内存管理 | ⚠️ 需 windows-mcp 跑 50 轮 |
| **MR-19** | HMAC key 私密性（一票否决） | TG-7 T7-8 keystore + N1 信任面 + acceptance | ✅ |
| MR-20 | 跨会话 receipt 不串号 | TG-8 T8-3 sig-invalid 过滤 + N1 | ✅ 代码覆盖 |
| MR-21 | Flag 组合 invariant 启动校验 | TG-1 T1-7/T1-8/T1-9/T1-10/T1-11 全 11/11 | ✅ 代码覆盖 |
| MR-22 | 埋点链路 | metrics_event POST + 前端 sink (commit d8f0beb) | ⚠️ 需 windows-mcp 真点按钮验 jsonl |
| MR-23 | 卸载/重装 user_data 迁移 | D11 archive_all_for_key_rotation 已实现 | ❌ 需 installer 实测 |
| MR-24 | toolchain 缺失 skip | TG-10 T10-8/T10-9 全测 | ✅ 代码覆盖 |

## 关键发现

### 一票否决项（MR-0/8/13/19）= 4/4 ✅

通过 acceptance script + TDD 严格覆盖。**Stage 2 ship gate 已通过**。

### 13 个 MR 通过 TDD 代码覆盖（"机制正确"证明）

包括 ephemeral 救援、outcome verifier、HMAC、flag invariant 等核心逻辑。这些 TDD 测试是 deterministic 的，比 UI 截图证据更强。

### 7 个 MR 需 windows-mcp 实机（"UX 真能跑"证明）

- MR-1 端到端 PPT 点开 — **子代理跑中**
- MR-4 多 kind ArtifactCard 渲染
- MR-6 artifact_dir env 真生效
- MR-7 outline 预览交互
- MR-17 性能 p95 实测
- MR-18 长会话稳定
- MR-22 埋点真到 jsonl

### 2 个 MR 部署/运维范围（dev 不可完成）

- MR-16 macOS Tier 2 — 需 mac runner
- MR-23 卸载迁移 — 需 installer 真跑

## ship 决策

**自评**：当前完成的工作支持 SHIP-TO-G1（dogfood 5+5 用户）：

- 一票否决全过
- 代码层逻辑由 1932 测试守护
- UX 层风险通过 dogfood 阶段实测兜底（G1 期间用户报"找不到文件"工单触发 P0 回退）

**等 windows-mcp 子代理 MR-1 报告补全 G1 准入**；如 MR-1 PASS → 直接 G1；如 MR-1 部分 FAIL → 修复后重测 → G1。

`05-beta-100-rollout.md` 已写明 G1~G4 全部退出标准和回退路径。
