# DeskPet 内测版 feature flag 审计表（WI-11）

**日期**: 2026-05-22
**性质**: 审计文档——核对新功能在内测版里的开关状态是否符合预期
**核对依据**: 实际读取了 `backend/config.py`、`config.toml`、
`backend/deskpet/memory/` 与 `backend/deskpet/skills/builtin/` 下的代码

> 目的：确认这两个月新加的 memory-v2、ppt-generate、deep-research 等功能在内测版
> 里处于**预期的**开关状态，不会意外向用户暴露半成品。

---

## 1. 审计总表

| 功能 | 默认状态 | 内测期望状态 | 实际配置位置 | 备注 |
|---|---|---|---|---|
| memory-v2 · facts（事实抽取） | **OFF** | OFF | `config.memory.facts.enabled`（即 `config.raw["memory"]["facts"]`） | Strangler-Fig；`config.toml` 无 `[memory.facts]` 段 → 默认关 |
| memory-v2 · eval（评估/反馈） | **OFF** | OFF | `config.raw["memory"]` 下的 eval 相关段 | `eval/` 子模块（feedback/metrics/qaset）按需建表，未启用即不跑 |
| memory-v2 · rerank（重排序） | **OFF** | OFF | `config.raw["memory"]` 下 rerank 相关段 | `BGEReranker`，缺权重/未启用时降级 `MockReranker` |
| memory-v2 · workspace（工作区记忆） | **OFF** | OFF | `config.raw["memory"]` 下 workspace 相关段 | `workspace.py`；未启用不参与召回 |
| memory-v2 · reflection（反思） | **OFF** | OFF | `config.raw["memory"]` 下 reflection 相关段 | `reflection.py` 注释明确「feature-flagged off by default」 |
| skill · ppt-generate | 已安装/存在 | **放出** | `backend/deskpet/skills/builtin/ppt-generate/SKILL.md` | 靠 `task_types` 触发，见第 3 节 |
| skill · deep-research | 已安装/存在 | **放出** | `backend/deskpet/skills/builtin/deep-research/SKILL.md` | 靠 `task_types` 触发，见第 3 节 |
| Code Mode | OFF（每会话） | 放出（用户自行开启） | `config.raw` 无全局开关；`CodeModeManager` 按会话维护 | 见第 4 节 |
| supervisor（桌宠看护） | **ON** | ON | `config.toml` 的 `[supervisor] enabled = true` | 见第 4 节 |
| P6 agent-loop gate | **ON** | ON | 环境变量 `P6_ENABLE_GATE`（未设即 ON） | 见第 4 节 |
| sanitize_inline_cot_dsml | **ON** | ON | `config.toml` 的 `[llm] sanitize_inline_cot_dsml = true` | 见第 4 节 |

---

## 2. memory-v2 五个 flag（核对：默认 OFF）

memory-v2（Phase A-E）引入了五个子能力：**facts / eval / rerank / workspace /
reflection**。审计结论：**五个在内测版里全部默认 OFF**，符合预期。

**核对到的事实**：

1. 这五个能力都不是旧 `MemoryConfig` dataclass 的字段。`config.py` 里
   `MemoryConfig` 只声明了 `db_path` 和 `embedding_model` 两个字段。memory-v2
   的子配置全部通过 `AppConfig.raw["memory"]` 这个原始 dict 直读。

2. `config.py` 的 `_KNOWN_EXTRAS_BY_DATACLASS` 把 MemoryConfig 的「已知额外段」
   登记为 `frozenset({"l1", "l2", "l3", "rrf"})`——这是为了让三层记忆的子段
   不触发「未知 key」警告。memory-v2 各能力的启用与否，由各自模块读
   `config.memory.<能力>.enabled` 决定（例如 `facts.py` 注释明确说事实抽取
   「only when `config.memory.facts.enabled` is true」）。

3. **当前仓库的 `config.toml` 里没有任何 `[memory.facts]` / `[memory.eval]` /
   `[memory.rerank]` / `[memory.workspace]` / `[memory.reflection]` 段**——
   配置缺段，启用值就取默认（关）。因此五个 flag 在内测版里**默认关闭**。

4. 这是有意的 **Strangler-Fig 策略**：新记忆能力代码已经合入，但默认不接管
   线上行为，靠显式 flag 才生效。`reflection.py` 模块注释直接写明
   「Both pieces are feature-flagged off by default」。`memory_v2_schema.py`
   的 `ensure_memory_v2_tables` 是「按需建表」——只有相应能力被调用时才会
   创建 memory-v2 的表，未启用时连表都不建。

**结论**：memory-v2 五个能力**确认默认 OFF**，内测版不会暴露这部分半成品。
若内测期想开启某一项做灰度，在 `config.toml` 里加对应的
`[memory.<能力>] enabled = true` 段即可。

---

## 3. 两个 builtin skill：ppt-generate / deep-research

两个 skill 都**存在**于 `backend/deskpet/skills/builtin/` 下，各有一份
`SKILL.md`。它们随 `deskpet.skills.builtin` 包数据打包，由 SkillLoader 加载。

**触发机制（核对自两份 SKILL.md 的 frontmatter）**：

| skill | `task_types`（决定何时被选用） |
|---|---|
| ppt-generate | `[task, plan, code]` |
| deep-research | `[recall, web_search, task]` |

它们不是「常驻开关」式的功能，而是**按任务类型触发**——用户的输入被分类成
某个 task_type，匹配到 skill 的 `task_types` 时该 skill 才会被纳入。

**内测期望状态：放出。** 理由：

- 两者都是面向用户的正向能力（生成 PPT、做调研报告），不是半成品。
- ppt-generate 依赖 `python-pptx`（纯 Python，已在 `pyproject.toml`），
  且 SKILL.md 设计了「python-pptx 缺失时降级为 markdown 大纲」的兜底——
  即使打包遗漏也不会硬崩。
- deep-research 走 web 工具，SKILL.md 强制「不接受无来源结论」，行为可控。

**放行前置动作**：内测构建出来后，对这两个 skill 各走一遍人工冒烟
（让桌宠生成一份 PPT、做一次 deep-research），确认在 frozen MSI 环境里
真能跑通——这是 WI-11 验收标准第 3 条要求的。

---

## 4. 既有高级功能的内测可见性

| 功能 | 状态核对 | 内测处理 |
|---|---|---|
| **Code Mode** | `config.toml` 里**没有**全局开关；`CodeModeManager`（`deskpet.code_mode`）按「base session」维护一张启用映射表。即它是**每会话的运行时切换**，由用户在 UI 里开/关，不是配置 flag。 | 放出。默认每个新会话是关闭态，用户主动开启才进 Code Mode。supervisor（见下）专门看护 Code Mode 卡死的会话。 |
| **supervisor** | `config.toml` 的 `[supervisor] enabled = true`——**默认开启**。它是 Watchdog + LLM 自检，扫描卡住的 Code-mode 会话（`scan_interval_seconds=60`、`stuck_threshold_seconds=900`）。 | 保持 ON。这是为 Code Mode 长任务兜底的看护机制，开着对内测有利。`llm_provider="default"` 复用主 LLM。 |
| **P6 agent-loop gate** | `config.py` 的 `is_p6_gate_enabled()`：环境变量 `P6_ENABLE_GATE` 未设时**返回 True**（默认 ON）。新的 AgentLoop/ContextManager 路径是唯一路径，旧路径已移除。 | 保持 ON（默认即是）。内测**不要**设 `P6_ENABLE_GATE=0`——那会禁用默认构造，属未定义行为。 |
| **sanitize_inline_cot_dsml** | `config.toml` 的 `[llm] sanitize_inline_cot_dsml = true`——**默认开启**。剥离某些云端模型内联进 content 的思维链 / DSML 工具协议，防止污染 `write_file` 输出。 | 保持 ON。关掉会导致 Code Mode 写文件时被思维链文本污染。 |

---

## 5. 放行结论

- **memory-v2 五个 flag**：确认默认 OFF（Strangler-Fig），内测版不暴露半成品 —— ✅ 符合预期
- **ppt-generate / deep-research**：决定放出；放行前需各跑一遍 frozen 环境人工冒烟 —— ⚠️ 待冒烟
- **Code Mode**：每会话运行时切换，默认关、用户自启 —— ✅ 符合预期
- **supervisor / P6 gate / sanitize_inline_cot_dsml**：默认 ON，保持 ON —— ✅ 符合预期

本表作为内测放行依据。若发现某 flag 默认值与预期不符，应在放量前修正
`config.toml` 或对应代码，而非带病放行。
