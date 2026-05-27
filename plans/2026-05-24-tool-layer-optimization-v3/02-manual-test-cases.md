# 人工测试用例 — DeskPet 工具层优化 v3

**关联**: `00-PRD.md` / `01-TDD.md`（**第 2 版（v2）**）
**状态**: **第 2 版（v2）** —— 已过架构评审 round1，按评审意见修订

> ## 第 2 版修订要点（详 PRD v2 §9）
> 1. **MR-T-1 改 build_agent 工厂方式**（避免 import main reload 翻车）
> 2. **MR-T-6 deferred**（session TTL 砍）
> 3. **MR-T-8-1 真实 tier enum**：`tier='l1'`（不是 `'preference'`）；schema migration 翻译表自动转 category=preference
> 4. **MR-T-10 改为直接 unregister 验证**（不是 deprecation handler）
> 5. **MR-T-11 改 replace_allowed opt-in**（不是一刀切抛错）
> 6. **MR-T-13 默认 strict**（关掉就是关掉）
**执行**: windows-mcp / computer-use 实机操作 DeskPet dev 实例 + sqlite3 查表 + 后端日志 + metrics.jsonl tail
**定位**: 自动化测试（TDD）验证"代码行为正确"；本文验证"**用户体感上 last-mile 真接电了** + **6 stubs 替换真返真数据** + **零回归**"。
**核心红线**: **MR-T-1 (VerifyGate 真接电) 是 last-mile P0-1 的兜底防线 —— 必须真跑桌宠 + mock LLM fake-completion 跑通 + metrics.jsonl 真出现 verify event 才算过**。

---

## 0. 测试环境

1. 在主 checkout 启动 dev 实例 —— `powershell -File scripts/dev.ps1`（backend 8100 / vite 5173）
2. 后端"已连接"（toolbar 绿色徽章）；需要**支持 function-calling 的 LLM**（推荐 the relay/deepseek-chat）
3. DPI 150%：坐标换算 逻辑=物理/1.5；中文输入用剪贴板 `Set-Clipboard` + Ctrl+V
4. 查后端表：`sqlite3 <user_data>/data/state.db`
5. 查 metrics：`tail -f <user_data>/observability/metrics.jsonl | jq .`
6. 查 dashboard：`python -m backend.scripts.metrics.dashboard --watch`

**flag 矩阵**（`config.toml`）：
- `[tools.verifier] enabled = true, verify_gate_mode = "shadow", max_verify_nudges = 2`
- `[tools] disabled_toolsets = [], disabled_toolsets_strict = [], dangerous_tools_allowlist = [], default_timeout_seconds = 60.0, strict_unknown_toolset = false`
- `[memory.v2] facts_extract = true`（memory_tools 依赖）

> 环境障碍（坐标/输入法/端口/LLM 不可用）绕过并记录，不算功能 bug。

---

## 1. 测试用例

### MR-T-0 · 零回归（all flags OFF）— 一票否决 ★

| 步骤 | 操作 | 预期 |
|---|---|---|
| **MR-T-0-0** | **三方 merge 顺序确认**（R-MISS-3）：先合 tool-last-mile-upgrade → master；再 rebase feat/memory-stage2；最后基于 master 起 feat/tool-layer-optimization-v3 | 三个分支顺序无冲突；git log 清晰 |
| MR-T-0-1 | `[tools.verifier] enabled = false`；`[tools]` 全空 | 后端正常启动；boot 日志**无** `p4_verify_gate_ready` |
| MR-T-0-2 | 正常聊天 3 轮 | 行为与 last-mile 合 master 后**字节级一致**（无新 metric event）|
| MR-T-0-3 | 调一个 tool（如 `file_read`） | receipt 仍 emit；duration_ms > 0（WI-T2.3 修复） |
| MR-T-0-4 | backend pytest 全套 + cargo test | 0 fail（含 last-mile 1931 + 本期 ~50 新增 ≈ 2000）|

**通过条件**：all flags OFF 时与 last-mile 合 master 后行为字节级一致。

### MR-T-1 · VerifyGate 真接电（WI-T2.1）★★ last-mile P0-1 兜底

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-1-1 | `[tools.verifier] enabled = true, verify_gate_mode = "shadow"` 启动 | boot 日志含 `p4_verify_gate_ready mode=shadow extractor=RegexExtractor patterns=N` |
| MR-T-1-2 | `tail -f metrics.jsonl` | 启动 30 秒内出现 verify_* 或 tool_registry.ready event |
| MR-T-1-3 | 使用 dev mock LLM provider（新建 `backend/scripts/dev/mock_llm.py` 或 monkey-patch `llm_call_func`）让 LLM 强制返回 "已生成 ai.pptx" 但**无任何 tool_call**；用户消息触发"请帮我生成 PPT" | metrics.jsonl 出现 `verify.fake_completion_detected` 或 `verify_extractor.fallback_used` event；shadow 模式记录不拦 |
| MR-T-1-4 | dashboard 看 `verify_extractor.fallback_used` 计数 | ≥ 1 |
| MR-T-1-5 | 切 `verify_gate_mode = "strict"` 重启 + 重跑 MR-T-1-3 | assistant 收到 D8 rebound message；iteration += 1 |
| MR-T-1-6 | `[tools.verifier] enabled = false` 重启 + 重跑 MR-T-1-3 | metrics.jsonl **无** verify event；assistant 正常返"已生成"假成功（regression guard）|
| MR-T-1-7 | claim_patterns.yaml 文件损坏（手工改坏一行）启动 | boot warn；cfg.tools.verifier.enabled auto-False；不崩 |
| MR-T-1-8 | LLM 真调用 ppt_create 成功 + 返"已生成 a.pptx" | verify_gate 看到 receipt → 通过；不拦 |

**通过条件**：MR-T-1-1 / 1-3 / 1-5 / 1-8 必须通过（核心接电证据）。MR-T-1-6 是 regression 守护。

### MR-T-2 · retention 30 天生效（WI-T2.2）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-2-1 | config 写 `artifact_dir_retention_days = 30` 启动 | `_receipt_store_box[0].retention_days == 30` |
| MR-T-2-2 | 手工造 3 个 receipt：1d / 10d / 35d | `cleanup_expired()` 后只删 35d；保留 10d 和 1d |
| MR-T-2-3 | 改 `retention = 7` 重启 + 同 MR-T-2-2 | 10d + 35d 都删；1d 保留 |

### MR-T-3 · duration_ms 真 > 0（WI-T2.3）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-3-1 | 让 LLM 调 file_read 大文件（~5MB）| metrics.jsonl tool.execute event duration_ms ≥ 20 |
| MR-T-3-2 | 调 web_fetch | duration_ms 与 wall time 大致符合 |
| MR-T-3-3 | 调一个抛 exception 的 mock tool | receipt 仍 emit；duration > 0；ok=False |

### MR-T-4 · Tauri cargo test 真过（WI-T2.4）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-4-1 | `cargo test --manifest-path tauri-app/src-tauri/Cargo.toml --lib` | ≥ 4 用例 pass |
| MR-T-4-2 | 故意改坏 canonicalize_path 重跑 | 测试 fail（证明真在跑）|
| MR-T-4-3 | PR 触发 GitHub Actions | cargo test job 在 PR check 中可见 |

### MR-T-5 · vitest CI 真跑（WI-T2.5）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-5-1 | `python -m backend.scripts.last_mile_smoke`（无 --no-vitest） | 含 `npm test ... 9/9 passed`；exit 0 |
| MR-T-5-2 | 故意改坏一个 vitest 测试 | smoke 脚本 exit != 0；明确报哪个 test fail |
| MR-T-5-3 | PR 触发 | frontend tests job 可见 |

### MR-T-6 ★v2 · ~~session_iteration 清理~~ deferred

按 round1 评审 P1-2：70KB/周非 leak，本期 deferred。无人工测试用例。
24h 长跑健康度验证移至 MR-T-16。

### ~~MR-T-6 v1 (deprecated)~~ · session_iteration 清理（不实施）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-6-1 | 启动 + 跑 3 个不同 code session（生成 3 个 session_id） | `_session_iteration` size = 3 |
| MR-T-6-2 | 桌宠运行 8 天后（伪造 `_session_last_access` 时间戳）+ TTL 定时任务跑 | 所有 stale session 被清；log `registry: TTL cleaned N stale sessions` |
| MR-T-6-3 | 多次跑 cleanup 幂等性 | 第 2 次返回 0 |

### MR-T-7 · metrics dashboard 真输出（WI-T2.7）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-7-1 | `python -m backend.scripts.metrics.dashboard` | rich table，含 5+ event_name 行 |
| MR-T-7-2 | `--since 1h` | 仅统计最近 1 小时 |
| MR-T-7-3 | `--watch` | 实时刷新 |
| MR-T-7-4 | `--alert verify.fake_completion_caught:>0.5` 超阈值 | exit != 0；stderr 含 ALERT |
| MR-T-7-5 | mock 卸载 rich | 降级 plain text；不崩 |
| MR-T-7-6 | `--report-json` | 输出 valid JSON；含 generated_at / window_seconds / events 字段 |

### MR-T-8 · memory_* 双注册（WI-T3.1）★

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-8-1 ★v2 | **老 schema 兼容**：LLM 调 `memory_write(text='我家猫叫旺财', tier='l1', salience=0.9)`（**v2 修正**：tier 真实 enum 是 `l1/l2/l3/auto`，不是 `preference`）| schema migration helper 自动翻译 `l1 → category=preference`；facts 表新增；key 自动 slugify；value='我家猫叫旺财'；ok=True |
| MR-T-8-2 | **新 schema**：LLM 调 `memory_v2_write(key='pet_name', value='旺财', category='preference', subject='user')` | facts 表新增；key='pet_name' 显式 |
| MR-T-8-3 | "我家猫叫什么？" → LLM 调 `memory_v2_read(subject='user', key='pet_name')` | 返 `{ok:true, fact:{value:'旺财',...}}` |
| MR-T-8-4 | "搜索关于宠物的记忆" → `memory_search(query='宠物')` | 返 hits 列表含旺财 fact |
| MR-T-8-5 | `[memory.v2] facts_extract = false` 重启 | memory_tools 不 bind；handler 返 not_bound error |
| MR-T-8-6 | grep stubs.py 中 memory_* 注册 | 0 行 active register（注释 `# REPLACED by memory_tools.py`）|
| MR-T-8-7 | 双注册并存检查 | registry.list_tools() 同时含 'memory_write' + 'memory_v2_write' |

### MR-T-9 · skill_invoke 真接电（WI-T3.2）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-9-1 | LLM 调 `skill_invoke(name='ppt_create_pro', args={...})` | SkillLoader.execute_skill 真触发；返 result |
| MR-T-9-2 | 调不存在 skill | 返 error |

### MR-T-10 ★v2 · mcp_call / delegate 直接删（WI-T3.3 D10 v2）

按 round1 评审 P1-4：mcp_call/delegate **无真用户 caller**（grep skill_packs/ + docs/ + plugins/ = 0）。直接 unregister，**不 ship deprecation handler**。

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-10-1 ★v2 | `python -c "from deskpet.tools.registry import registry; print('mcp_call' in registry.list_tools(), 'delegate' in registry.list_tools())"` | **False, False**（v2 直接删，不保留注册）|
| MR-T-10-2 ★v2 | LLM schemas 输出 | **不含** mcp_call / delegate（LLM 看不到）|
| MR-T-10-3 ★v2 | 显式 `registry.execute_tool("mcp_call", {})` | 返 `unknown_tool` error |
| MR-T-10-4 ★v2 | grep skill_packs/ + docs/ + plugins/ 引用 mcp_call/delegate | 0 命中（无真 caller，删除安全）|
| MR-T-10-5 ★v2 | stubs.py 中的 mcp_call/delegate 注册 | **已移除**（注释保留 schema 定义作历史 reference）|

### MR-T-11 ★v2 · ToolNameConflictError + replace_allowed opt-in（WI-T4.1 D11 v2）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-11-1 ★v2 | 故意 file_tools.py 末尾追加 `registry.register("file_read", ..., replace_allowed=False)` 重启（**双方未 opt-in replace**）| backend 启动失败；traceback 含 `ToolNameConflictError: ...replace_allowed=True on BOTH register calls.` |
| MR-T-11-1b ★v2 | 双 builtin 同名但**双方都 replace_allowed=True**（stubs.py 已开 + 真实现也开 opt-in）| warn + 覆盖；不 raise |
| MR-T-11-2 | plugin 同名 builtin (plugin source=`plugin:p1` 注册 `file_read`) | plugin 自动加前缀 `p1:file_read`；不冲突 |
| MR-T-11-3 | plugin:p1:iid_a + plugin:p1:iid_b 同前缀同名 | 第二次 raise `ToolNameConflictError` |
| MR-T-11-4 | plugin:p1:iid_a + plugin:p1:iid_a reload | warn + 覆盖；不 raise |
| MR-T-11-5 | MCP server reconnect 注册 `mcp__server__tool` 重复 | warn + 覆盖，不 raise |
| MR-T-11-6 ★v2 | 验证 stubs.py + memory_tools.py + skill_tools.py 都已加 `replace_allowed=True` | backend 启动 0 ToolNameConflictError；正常起 |

### MR-T-12 · plugin 自动前缀（WI-T4.2）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-12-1 | `registry.register("greet", ..., source="plugin:my_plugin")` | 实际注册名 `my_plugin:greet` |
| MR-T-12-2 | 同上 source=plugin:my_plugin:iid_1 | 实际注册名 `my_plugin:greet`（instance_id 不进 name）|
| MR-T-12-3 | source="builtin" | 名字不变 |

### MR-T-13 · _config.py 扩展（WI-T5.1）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-13-1 ★v2 | `[tools] disabled_toolsets = ["computer_use"]` 启动（**v2 默认 strict**）+ LLM 询问"截图" | LLM schemas 不含 screen_capture；**execute_tool 同时拒绝**（v2 默认双层挡）；LLM 用其他工具或承认无法截图 |
| MR-T-13-2 ★v2 | `disabled_toolsets_schema_only = ["computer_use"]`（**opt-in 边缘场景**）+ 程序内强制 `execute_tool("screen_capture", {})` | execute_tool **允许调**（这是 schema_only 的语义）；schemas() 仍不含 |
| MR-T-13-3 | `dangerous_tools_allowlist = ["run_shell"]` | 仅 run_shell 在 schemas |
| MR-T-13-4 | `dangerous_tools_allowlist = []` 默认 | 现状（沿用 UI 确认） |
| MR-T-13-5 | `disabled_toolsets = ["typo_xxx"]` + `strict_unknown_toolset = false` | boot warn `unknown toolset`；不崩 |
| MR-T-13-6 | 同 13-5 + `strict_unknown_toolset = true` | backend 启动失败（fail-fast）|
| MR-T-13-7 | `default_timeout_seconds = 30.0` + 未显式 timeout 工具 | 该工具 timeout 30 秒 |

### MR-T-14 · OpenSpec tasks 回填（WI-T6.1/T6.2）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-14-1 | `openspec list` | 不再显示 p4-poseidon-agent-harness 为 active |
| MR-T-14-2 | tasks.md 勾选率 | ≥ 150/161 [x]；剩 < 11 条空白有明确 `# TODO post-stage` 注释 |

### MR-T-15 · flag 一键回退（综合）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-15-1 | 全开 → 改 `verify_gate_mode = "off"` 或 `enabled=false` 重启 | verify_gate 干净关闭；AgentLoop 走老路径 |
| MR-T-15-2 | `disabled_toolsets = ["memory"]` 重启 | memory_* / memory_v2_* 从 LLM schemas 摘掉 |
| MR-T-15-3 | 全关 + Stage 1/last-mile 全开 重启 | 完全回到 last-mile 合 master 后的行为（同 MR-T-0）|

### MR-T-16 · 24h 持续运行健康度

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-T-16-1 | 桌宠 24h 连续运行 + 正常使用 | metrics.jsonl 体积合理（< 100MB/day）|
| MR-T-16-2 | 看进程内存变化（task manager） | 无明显内存爬升（验证 session_iteration TTL 清理）|
| MR-T-16-3 | dashboard 24h 后看 verify metrics | shadow 模式 fake_completion 次数 / 总 turn 数 = fake-completion rate；记下用于决定升 strict |

---

## 2. 结果回报格式

```markdown
| 用例 | 通过 | 失败 | 环境受限未测 |
（MR-T-0 ~ MR-T-16 逐条）

功能 bug 列表（若有）：
- [MR-T-x-y] 现象 / 复现 / 期望 vs 实际 / 证据(metrics.jsonl 行 / 后端日志 / 截图)

关键接电证据（必须有）：
- p4_verify_gate_ready 日志：✅ / ❌
- metrics.jsonl 含 verify event count：N（≥ 1）
- p4_memory_tools_bound 日志：✅ / ❌
- duration_ms > 0 比例：N%（typical workload 应 ≥ 99%）

环境障碍（已绕过，非 bug）：
- ...

结论: Go / No-Go
```

证据：metrics.jsonl 抓 100 行；boot log 截图；sqlite3 dump 关键表；截图存
`plans/2026-05-24-tool-layer-optimization-v3/screenshots/`。

---

## 3. 通过标准

- **MR-T-0（零回归一票否决）必须通过 ★**
- **MR-T-1 (VerifyGate 真接电) 核心场景 1/3/5/8 必须通过 ★★（last-mile P0-1 兜底）**
- **MR-T-8 (memory_* 双注册) 1/2/3 必须通过（stubs 替换核心）**
- MR-T-2 ~ MR-T-16 全部通过，或仅因环境受限（无 LLM / cargo 不可用）未测且降级正确
- 功能 bug = 0

---

## 4. 实测结果（动工后回填）

| 用例 | 结果 |
|---|---|
| MR-T-0 零回归（含 0-0 三方 merge）★ | ⬜ |
| MR-T-1 VerifyGate 真接电（mock LLM）★★ | ⬜ |
| MR-T-2 retention 30 天 | ⬜ |
| MR-T-3 duration_ms > 0 | ⬜ |
| MR-T-4 Tauri cargo test | ⬜ |
| MR-T-5 vitest CI | ⬜ |
| MR-T-6 session_iteration TTL | ⬜ |
| MR-T-7 metrics dashboard + report-json | ⬜ |
| MR-T-8 memory_* 双注册 ★ | ⬜ |
| MR-T-9 skill_invoke 真接电 | ⬜ |
| MR-T-10 mcp_call/delegate deprecation | ⬜ |
| MR-T-11 ToolNameConflictError（含 plugin/plugin） | ⬜ |
| MR-T-12 plugin 自动前缀 | ⬜ |
| MR-T-13 _config.py 扩展（含 strict） | ⬜ |
| MR-T-14 OpenSpec tasks 回填 | ⬜ |
| MR-T-15 flag 一键回退 | ⬜ |
| MR-T-16 24h 运行健康度 | ⬜ |

### 待动工。
