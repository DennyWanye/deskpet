# DeskPet 手工测试主索引（Manual Test Master Index）

> **维护时间**：2026-05-26
> **用途**：给后续 agent / 真人作为"做某模块改动时，应该跑哪份手工测试"的总查询表。
> **使用方式**：
> 1. 接到任务 → 在 [§1 索引矩阵](#1-索引矩阵按模块) 找到对应模块
> 2. 跳到该模块的详细测试文档（每行第三列）
> 3. 根据"必跑用例（一票否决）"先跑红线 case，再按需补充范围内的其他 case
> 4. 若改动跨模块 → 把涉及模块的"必跑用例"都跑一遍 + 跑 [§3 通用回归冒烟](#3-通用回归冒烟所有改动都得跑)
>
> **重要约定**：
> - 每个详细测试文档都有自己的 **ID 命名空间**（A0-x、MR-S2-x、CASE-D0-x、TC-0x、MR-T-x 等），互不冲突，可以引用
> - 报告回填时必须截图 + 抓日志（feedback_simulate_manual_test）
> - 不允许"脚本回放"当 E2E 证据（feedback_real_e2e_not_script_replay）
> - 改代码只跑 pytest/tsc 不算完成——必须 windows-mcp 走真实 end-to-end

---

## §1 索引矩阵（按模块）

| 模块 / 功能领域 | ID 前缀 | 详细文档 | 用例数 | 一票否决 |
|---|---|---|---|---|
| **A. 安装 / 首启 / Onboarding** | `A0-x`、`A1-x` | [beta-100-manual-test.md](./2026-05-22-beta-100-manual-test.md) §A0/A1 | ~17 | A0-1, A1-6, A1-10 |
| **B. 老用户升级 + Updater** | `B0-x`、`B1-x` | [beta-100-manual-test.md](./2026-05-22-beta-100-manual-test.md) §B0/B1 | ~12 | B0-1（数据不丢） |
| **C. 进程生命周期** | `C-x` | [beta-100-manual-test.md](./2026-05-22-beta-100-manual-test.md) §C | ~10 | 无僵尸进程 |
| **D. 卸载** | `D-x` | [beta-100-manual-test.md](./2026-05-22-beta-100-manual-test.md) §D | ~10 | D-1（卸载清干净） |
| **E. 安装包文档核对** | `E-x` | [beta-100-manual-test.md](./2026-05-22-beta-100-manual-test.md) §E | ~10 | — |
| **R. 中转站登录 / 账户** | `R1-x ~ R9-x` | [relay-login-integration/02-manual-test-cases.md](./2026-05-22-relay-login-integration/02-manual-test-cases.md) | 9 | R3（核心聊天链路）、R9（OSS 零回归） |
| **MR-mem (Stage 1). 记忆系统 v2 Stage 1** | `MR-0 ~ MR-8` | [memory-system-upgrade/02-manual-test-cases.md](./2026-05-22-memory-system-upgrade/02-manual-test-cases.md) | 9 | MR-0（零回归）、MR-7（一键回退） |
| **MR-S2. 记忆系统 v2 Stage 2** | `MR-S2-0 ~ MR-S2-14` | [memory-system-stage2/02-manual-test-cases.md](./2026-05-23-memory-system-stage2/02-manual-test-cases.md) | 15 | MR-S2-0（零回归）、MR-S2-8（老库 schema 兼容）、MR-S2-13（R-MISS-2 防覆盖） |
| **MR-tool. 工具调用 Last-Mile** | `MR-0 ~ MR-24` | [tool-last-mile-upgrade/02-manual-test-cases.md](./2026-05-23-tool-last-mile-upgrade/02-manual-test-cases.md) | 25 | MR-0、MR-8（Fake-completion）、MR-13（路径幻觉）、MR-19（HMAC key 私密） |
| **MR-T. 工具层 v3 优化** | `MR-T-0 ~ MR-T-16` | [tool-layer-optimization-v3/02-manual-test-cases.md](./2026-05-24-tool-layer-optimization-v3/02-manual-test-cases.md) | 17 | MR-T-0（零回归） |
| **MR-skills. 内置 Skills (Beta 包)** | `B1 ~ B10` | [builtin-skills-manual-test.md](./2026-05-22-builtin-skills-manual-test.md) | 10 | B4（doc-edit 防手滑） |
| **TC. UI 面板 / DialogBar / 输入栏** | `TC-01 ~ TC-12` | [MANUAL-TEST-PLAN-2026-05-20.md](./MANUAL-TEST-PLAN-2026-05-20.md) | ~50 | TC-01/02/04/08/09/10/11 全 P0 |
| **CASE (pet-anim v1). 桌宠动画 — 微动 / 眼神 / 指针** | `CASE-D0/P/B/S/G/MP/PR/MET/PERF/REG/HMR/COLD/BLIND` | [pet-animation-ux/ManualTest.md](./2026-05-24-pet-animation-ux/ManualTest.md) | ~50 | CASE-D0 全部 Day-0 探针、CASE-PERF-01 |
| **CASE (pet-anim v2). 桌宠动画 v2 — 拖 / emotion / welcome / DND** | `CASE-A1/B1/B2/B3/B4/C1/C2/C3/D1/D2/E1/E2/F1/AC3/AC10` | [pet-animation-ux-v2/ManualTest.md](./2026-05-25-pet-animation-ux-v2/ManualTest.md) | ~50 | AC10-01~04（4 个一票否决）、AC3-01~04（v1 零回归 snapshot） |
| **A3. 成本护栏** | `A3-x` | [beta-100-manual-test.md](./2026-05-22-beta-100-manual-test.md) §A3 | 6 | A3-3（100% 真拦截） |
| **A4/A5. 反馈 + 诊断包 + 崩溃链路** | `A4-x`、`A5-x` | [beta-100-manual-test.md](./2026-05-22-beta-100-manual-test.md) §A4/A5 | 11 | A4-6（zip 内零密钥） |
| **A6. Feature-Flag 默认态** | `A6-x` | [beta-100-manual-test.md](./2026-05-22-beta-100-manual-test.md) §A6 | 4 | A6-1（memory-v2 五 flag 默认 OFF） |

---

## §2 模块详解（"我在改 X，要跑哪份"快速决策）

### 2.1 安装 / Onboarding / 升级 / 卸载（A、B、D、E）
- **触发场景**：MSI 包、首启流程、updater、卸载脚本、`%AppData%\deskpet\` 目录结构变化
- **路径**：`tauri-app/src-tauri/` installer 相关、Tauri updater、`onboarding_done.json`、`crash_reports/`
- **必跑红线**：
  - A0-1 ~ A0-6 — 干净机器 MSI 全流程不报错
  - A1-6/A1-8/A1-10 — onboarding 走通且只弹一次
  - B0-1 — 老用户升级 `state.db` 不丢
  - D-1 — 卸载干净（`%AppData%` 残留 ≤ 用户文档）
- **环境准备**：需要干净 Windows 11 测试机或快照（E1 / E2）；E3/E4 准备有效 + 无效 LLM key

### 2.2 中转站登录 / 账户 / 余额（R1-R9）
- **触发场景**：登录窗口、relay 反代、`tsk_*` access token、`key_*` device key、keychain 写读、余额面板、充值跳转、登出
- **路径**：`tauri-app/src/onboarding/`、`backend/secrets.py`、`backend/relay_*`、`backend/billing_*`
- **必跑红线**：
  - R3 — 登录后模型链路自动配好 + 真发一句话拿到 LLM 回复
  - R9 — OSS manual 构建（无 relay）零回归
- **环境准备**：测试账号 `<see LOCAL-DEV-CREDENTIALS.md> / <see LOCAL-DEV-CREDENTIALS.md>`（仅 DEV！见 [CLAUDE.md](../CLAUDE.md)）

### 2.3 基础对话 / 模型切换 / mic / 面板（TC-01 ~ TC-12 + A2）
- **触发场景**：桌宠 InputBar、DialogBar、Toolbar、消息面板（▶ toggle）、模型 modal、reasoning_effort/thinking 参数、ASR mic
- **路径**：`tauri-app/src/components/`、`tauri-app/src/panels/`、`backend/chat_handler.py`、`backend/asr_*`
- **必跑红线**：
  - TC-04（5 用例）— 面板发送一致性（sessionId="default"）
  - TC-08（6 用例）— 模型 modal + 参数切换
  - TC-09（6 用例）— mic 按钮 + 真实转写
  - TC-10（3 用例）— 主输入栏 / 面板互斥隐藏
  - A2-2/A2-3 — 基础对话上下文 + ASR
- **后续 agent 注意**：碰到 panel/InputBar 改动，TC-01/02/10 三组是隐藏依赖很多的雷区

### 2.4 桌宠动画 v1（CASE-D0/P/B/S/G/MP/PR/MET/PERF/REG/HMR/COLD/BLIND）
- **触发场景**：Perlin 微动、blink、saccade、gaze 追随、motion pool、pointer 单/双击 + hover、性能 FPS、HMR、冷启动
- **路径**：`tauri-app/src/pet-anim/`、`tauri-app/src-tauri/src/lib.rs`（ignore_cursor_events / hit-zone）
- **必跑红线**（任一失败必返工）：
  - CASE-D0-01~04 — Day-0 4 探针（addParameterValueByIndex、EyeBall 范围、window pointermove、hit-zone click）
  - CASE-D0-CLEANUP — 解 MAJOR M'3
  - CASE-PERF-01 — FPS（关 DevTools）
  - CASE-G-06 — window resize 后 face 自适应（解 BLOCKER B'1）
- **执行约定**：CASE-D0-CLEANUP 必须在 D0 探针之后立刻跑；DevTools 必须关闭做 PERF

### 2.5 桌宠动画 v2（CASE-A1/B1/B2/B3/B4/C1/C2/C3/D1/D2/E1/E2/F1 + AC3 + AC10）
- **触发场景**：拖动 + body wobble + spring back、用户输入歪头、first-chunk thinking 退出、viseme 双路径、welcome escalation（5min/15min/1h）、整点 + DND 抑制、emotion 5 类 + 投票分类、milestone 5 规则、4 边 snap、consent、DND 三 trigger
- **路径**：`tauri-app/src/pet-anim/` v2 模块、`tauri-app/src/pet-anim/edgeWatcher.ts`、`tauri-app/src/pet-anim/timeCelebration.ts`、backend 的 emotion / viseme / DND
- **必跑红线（4 个一票否决 + 4 条 v1 snapshot）**：
  - AC10-01：D1 sad **不**误归 happy
  - AC10-02：E2 pet **不**超屏
  - AC10-03：F1 **不**抑 red alert
  - AC10-04：A1 drag **不**破 v1 click
  - AC3-01：v2_all=off → 386 单测过
  - AC3-02：v2_all=off → 27/27 v1 OS 手测过
  - AC3-03：v2_all=on + 13 FR all off → snapshot diff = 0
  - AC3-04：单 FR on → 仅该 FR 相关 param 变（13 sub-tests）
- **执行约定**：CASE-D0-01~06 Day-0 6 探针必先做（v2 比 v1 多 2 个）；blind 盲选（CASE-BLIND-v2-01）必做

### 2.6 工具调用 Last-Mile（MR-0 ~ MR-24）
- **触发场景**：tool 产物（PPT/Excel/Word/PDF/图像/Markdown/URL）一键打开、artifact_dir 配置、ClaimPattern 热加载、Receipt 落盘、Outcome verifier、Fake-completion 抓获、HMAC key 私密性、跨会话 Receipt 不串号、卸载/重装 user_data 迁移、点击率埋点、flag invariant 校验
- **路径**：`backend/tool_*`、`backend/verify_gate.py`、`backend/outcome_verifier.py`、`backend/artifact_*`、`backend/receipt_store.py`、`tauri-app/src/components/ArtifactCard.tsx`
- **必跑红线（4 个一票否决）**：
  - MR-0 — 第一代零回归（flag 全关）
  - MR-8 — Fake-completion 抓获（"我已经帮你生成了"但没真产物，必须报错）
  - MR-13 — Outcome verifier `file_exists` 抓 LLM 路径幻觉
  - MR-19 — HMAC key 私密性（v2 改 DPAPI/Keychain）
- **准入硬条件**：N1/N2 + 4 个一票否决，跑 `python scripts/acceptance/last_mile_smoke.py` 期望 `DECISION: SHIP`

### 2.7 工具层 v3 优化（MR-T-0 ~ MR-T-16）
- **触发场景**：VerifyGate 真接电、retention 30 天、`duration_ms > 0`、Tauri cargo test、vitest CI、metrics dashboard、`memory_*` 双注册、`skill_invoke` 真接电、`mcp_call/delegate` 删除、`ToolNameConflictError` + `replace_allowed` opt-in、plugin 自动前缀、`_config.py` 扩展、OpenSpec 回填、24h 持续运行
- **路径**：`backend/tool_registry.py`、`backend/verify_gate.py`、`backend/metrics_*`、`tauri-app/src-tauri/`、CI 配置
- **必跑红线**：
  - MR-T-0 — 零回归（all flags OFF）
  - MR-T-1 — VerifyGate 真接电（last-mile P0-1 兜底）
  - MR-T-8 — `memory_*` 双注册
  - MR-T-15 — flag 一键回退（综合）
  - MR-T-16 — 24h 持续运行健康度

### 2.8 内置 Skills（B1 ~ B10）
- **触发场景**：excel-generate / doc-edit（新建 + 修改） / ppt-generate / pdf-export / file-organize / translate-doc / web-read / screenshot-ocr
- **路径**：`backend/skills/`、`skills/` 用户 skill 包、对应 tool handler
- **必跑红线**：
  - B4 — doc-edit 防手滑（**一票否决**，不允许误删用户文档）
  - 每个 skill 至少跑一次 happy path + 缺依赖路径

### 2.9 记忆系统 v2 Stage 1（MR-0 ~ MR-8）
- **触发场景**：facts 抽取（shadow）、enhanced_retriever、reranker、workspace_memory、reflection、feedback_loop、flag 一键回退、worktree 隔离自检
- **路径**：`backend/memory/*`、`backend/facts_*`、`backend/reranker_*`
- **必跑红线**：
  - MR-0 — 第一代零回归（对照组，flag 全关）— **一票否决**
  - MR-7 — flag 一键回退
  - MR-8 — 并行开发隔离自检（worktree 环境）
- **当前状态**：Stage 1 已 Go（4 轮 LLM-enabled 复测全过，见同文件 §4-§6）

### 2.10 记忆系统 v2 Stage 2（MR-S2-0 ~ MR-S2-14）
- **触发场景**：跨 key 矛盾治理（cross_key_merge）、memory_forget 工具 + UI、entity 索引、episodic→semantic 固化、eval 门控严格化、MR-4 GUI 联调、老库 schema migrator、性能/延迟、facts_conflict_cleanup、MemoryPanel facts view UI、R-MISS-2 被遗忘 fact 防覆盖、strict CI 自动化触发
- **路径**：`backend/memory/stage2_*`、`backend/memory_forget.py`、`backend/entity_index.py`、`tauri-app/src/panels/MemoryPanel.tsx`
- **必跑红线**：
  - MR-S2-0 — Stage 2 零回归（Stage 2 flag 全关 + Stage 1 全开）— **一票否决**
  - MR-S2-8 — 老库兼容性（schema migrator，**M0 出门必跑**）
  - MR-S2-13 — R-MISS-2 被遗忘 fact 防覆盖（v2 专项）

### 2.11 成本护栏 / 反馈 / 崩溃 / Feature-Flag（A3、A4、A5、A6）
- **触发场景**：daily_budget、用量数字累计、80%/100% 提示、诊断包打包（zip）、`meta.json` 内 `api_key`/`secret`/`token` 必须**零命中**、crash_reports 收集、内测 feature-flag 默认态
- **路径**：`backend/budget_*`、`backend/feedback_bundle.py`、`crash_reports/`、`docs/beta-feature-flags.md`、`config.toml`
- **必跑红线**：
  - A3-3 — 100% 真拦截（不允许静默失败）
  - A4-6 — 诊断包 zip 内 `api_key`/`secret`/`token` 零命中（**最严重的隐私一票否决**）
  - A4-7 — zip 内无 `llm_runtime.json` 原文
  - A6-1 — memory-v2 五个 flag 默认 OFF（对照 `docs/beta-feature-flags.md`）

---

## §3 通用回归冒烟（所有改动都得跑）

无论改的是哪个模块，merge 前最低限度要走完：

| # | 用例 | 时间预算 | 失败动作 |
|---|---|---|---|
| **SMOKE-1** | 启动 deskpet → 看到桌宠 + 绿 `已连接` 徽章 + FPS > 0 | 90s | 反查 backend 启动日志 |
| **SMOKE-2** | 主输入框打"你好" → 拿到非空回复 | 30s | 反查 chat_handler + LLM key |
| **SMOKE-3** | 主 mic 按钮录一句"今天天气怎样" → 转写正确 + 回复 | 60s | 反查 ASR 链路 |
| **SMOKE-4** | 打开 ▶ 消息面板 → 主输入栏消失 → 面板内发消息 → 回到 default 流 | 45s | TC-04 / TC-10 |
| **SMOKE-5** | 关面板 → 主输入栏恢复 | 15s | TC-02 / TC-10 |
| **SMOKE-6** | Toolbar 反馈按钮 → 一键打包 → zip 出现 + 路径进剪贴板 | 60s | A4 |
| **SMOKE-7** | 关窗后任务管理器：`deskpet.exe` + `python` (backend) + Vite **必须全清** | 30s | feedback_tauri_dev_cleanup |

任何一个 SMOKE 失败 = 不允许合并。

---

## §4 命名空间冲突表（避免新建测试时撞 ID）

| 已占用前缀 | 所属模块 | 不要再用 |
|---|---|---|
| `A0-x`、`A1-x`、`A2-x`、`A3-x`、`A4-x`、`A5-x`、`A6-x` | beta-100（安装/对话/护栏/反馈/flag） | 是 |
| `B0-x`、`B1-x` ~ `B10` | beta-100 升级 + builtin-skills（B1-B10 实为 skills，与升级 B0/B1 序列重叠 — **新模块禁用单字母 B**） | 是 |
| `C-x`、`D-x`、`E-x` | beta-100 进程/卸载/文档 | 是 |
| `R1-x ~ R9-x` | relay-login | 是 |
| `MR-0 ~ MR-24` | tool-last-mile（注意：memory-v2 Stage 1 也用 `MR-0 ~ MR-8`，**两套，靠所在文档区分**） | 部分 |
| `MR-S2-x` | memory-v2 Stage 2 | 是 |
| `MR-T-x` | tool-layer v3 | 是 |
| `TC-01 ~ TC-12` | UI 面板手测 | 是 |
| `CASE-D0/P/B/S/G/MP/PR/MET/PERF/REG/HMR/COLD/BLIND` | pet-anim v1 | 是 |
| `CASE-A1/B1/B2/B3/B4/C1/C2/C3/D1/D2/E1/E2/F1/AC3/AC10` | pet-anim v2 | 是 |

**新建测试的 ID 前缀规则**：模块缩写 + 短 dash + 编号，例如 `TTS-01`、`UPD-03`、`SKL-PPT-04`。**禁止**单字母前缀（B/C/D/E 已被 beta-100 系列占了）。

---

## §5 给后续 Agent 的执行 SOP

1. **接到任务先 grep 自己的领域**：
   - 改了 `tauri-app/src/pet-anim/*` → 跑 §2.4 + §2.5
   - 改了 `backend/memory/*` → 跑 §2.9 + §2.10
   - 改了 `backend/tool_*` → 跑 §2.6 + §2.7
   - 改了 `backend/skills/*` 或 `skills/*` → 跑 §2.8
   - 改了登录/relay → 跑 §2.2
   - 改了 UI 面板/DialogBar → 跑 §2.3
   - 改了启动/安装/updater → 跑 §2.1

2. **跨模块改动**：把所有命中模块的"必跑红线（一票否决）"列出来 + §3 SMOKE-1~7 全跑。

3. **写测试报告**：参考各模块文档里的"结果回报格式"小节（通常是 §2），格式包含 case id / 实际结果 / 截图 / 日志位置。

4. **报告归档**：放在对应模块目录的 `manual-results-YYYY-MM-DDTHHMMSSZ/` 子目录，与 `02-manual-test-cases.md` 同级。

5. **截图前先关 onboarding 窗**（防 DEV 测试账号密码截图泄露）。

6. **遇到 BLOCKED**：先说原因 + 替代方案，等用户确认，不要硬绕过。

---

## §6 文档维护规则

- **新增测试模块** → 在 §1 加一行 + 在 §2 写一段模块详解 + 在 §4 注册 ID 前缀
- **某模块测试已完全归档** → 不删行，在"详细文档"列加 `（已归档）` 标记，留作 fix 历史回查
- **本索引文档不写具体 case** → 具体步骤一律放在各模块自己的 `02-manual-test-cases.md` 或 `ManualTest.md`
- **本索引每次大版本（每 1-2 个月）review 一次** → 检查是否有新增/失效模块

---

## §7 已知的"测试文档之间的依赖关系"

```
beta-100-manual-test.md (主干，所有内测前必跑)
 ├── 依赖 relay-login-integration（R1-R9 是 A1 onboarding 的细化）
 ├── 依赖 builtin-skills（B1-B10 是 A6 feature-flag 内的具体 skill）
 ├── 依赖 memory-system-upgrade (Stage 1)（A6-1 验 flag 默认态）
 └── 依赖 memory-system-stage2（同上）

tool-last-mile-upgrade ←── tool-layer-optimization-v3 (v3 是 last-mile 的子集精修)
    └── 影响 A4 反馈链路（artifact 落盘路径变化）

pet-animation-ux (v1) ←── pet-animation-ux-v2 (v2 必须 AC3 v1 零回归 snapshot)
    └── 影响 SMOKE-1（启动后桌宠是否仍流畅）

MANUAL-TEST-PLAN-2026-05-20.md (TC-01~12)
    └── 是 §2.3 UI 面板的最权威文档，被多次复用
```

跨模块改动 → 沿着箭头反向追，把上游受影响的"必跑红线"也补上。

---

**END — 维护人：Claude（2026-05-26 首次生成）；下次 review 建议 2026-07-01**
