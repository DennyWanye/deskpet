# 2026-05-15 — Context System Re-architecture for the 1M-Token Era

> **Status**: Proposed · **Effort**: 3 sprints (phased, each ships independently) · **Risk**: medium (AgentLoop hot path; mitigated by Strangler-Fig + TDD + per-phase live E2E)

## Why

deskpet 的智能上下文系统（ContextAssembler P4-S7 + ContextManager P6）设计于 32K–200K context 时代。`deepseek-v4-pro` 现在是 1M context，但**所有阈值都是写死的绝对值**，导致已观测的真实故障：

1. **code-mode 任务 50 轮爆 14 次/天**（今天 sid=code-rkjdd9vo）——LLM 看 head+tail 觉得"懂了"→ 读下个文件 → 又被 4KB 切 → read 循环不收敛
2. **拿到 1M 的车按 200K 限速跑**——浪费 80% 容量；切到 claude-sonnet（200K）又会爆，因为是全局单值
3. **反复 in-place summarize 把历史搞成"半原文半摘要的 Frankenstein"**——破 prefix cache，丢细节
4. **同文件读 5 次占 5 份 context**——每次 read 生成新随机 ref_id，无内容寻址
5. **每轮重发全历史无 cache 标记**——prefix-cache miss，the relay/deepseek 输入 token 成本全价
6. **pytest+tsc 都过但前后端字段不一致**（[feedback_cross_layer_contract]）——code mode 看不到对面层接口全景

调研了 6 个最先进系统（Claude Code / OpenAI Codex / Aider / Cline / Hermes / DeepSeek-TUI）。**关键反直觉发现**：DeepSeek-TUI 引论文 Figure 9——deepseek-v4 在 256K 召回率 0.76，1M 仅 0.59。**context 越大利用率越差**，所以 DeepSeek-TUI 在 768K（75%）就 Checkpoint-Restart 而非用满 1M。目标不是"用满 1M"，而是"把有效上下文控制在召回甜点区，靠 retrieval + checkpoint 维持长跑"。

## What Changes

> 优先级已从用户原排序重排（理由见下）。用户原序：Checkpoint-Restart → SHA-256 → per-model → file-dedup → cache → repo-map。**重排理由**：per-model map 是地基——Checkpoint-Restart 触发线 / compaction 比例 / budget 全要 per-model window 才有意义，先做 Checkpoint-Restart 等于又一次 hardcode；file-dedup 是今天痛点最便宜的解药（~1.5 天）；prompt-cache 是立刻省钱的机械改动。

**Phase 1 — 地基 + 止血（1 sprint）**
- **NEW** `backend/llm/model_info.py`：per-model `ModelContextInfo` 内置表 + 三层 override 解析（内置 ← `%APPDATA%/deskpet/model_overrides.toml` ← `<project_root>/.deskpet/context.toml`）。抄 Codex `models-manager/src/model_info.rs`
- **MODIFIED** `ContextConfig`：阈值从绝对值常量改为 resolved-window 比例 `@property`；`[context.manager].v2_enabled=false` 回退闸
- **MODIFIED** `CodeModeManager`：进 code session 时按 `project_root` resolve 注入 ContextManager
- **NEW** File-read dedup：同 path 重复 read，历史里旧 tool_result 替换为 superseded marker（抄 Cline `contextHistoryUpdates`）
- **MODIFIED** `openai_compatible.py`：4-breakpoint prompt cache + 前缀稳定纪律（抄 Claude Code）
- **NEW** `SettingsPanel.tsx` 模型上下文配置卡片

**Phase 2 — 长跑架构（1 sprint）**
- **MODIFIED** `tool_result_truncator.py`：随机 ref_id → SHA-256 内容寻址 + 落盘 `%APPDATA%/deskpet/tool_results/`（抄 DeepSeek-TUI `chat.rs:924`）
- **NEW** Checkpoint-Restart Cycle：替代 B2 in-place summarize；归档旧 cycle JSONL + 结构化状态 + 模型自写 briefing 重启；`recall_archived_cycle` BM25 查归档（抄 DeepSeek-TUI `cycle_manager.rs`）
- **NEW** SessionDB migration：`context_cycles` 表 + FTS5

**Phase 3 — 治本（1 sprint）**
- **NEW** `backend/deskpet/code_mode/repo_map.py`：tree-sitter（Python+TS+TSX）抽接口签名 + PageRank 排序 → ≤1% window token 全仓概览（抄 Aider `repomap.html`）

**非目标**：不引入 sandbox（违反 [feedback_no_sandbox_constraints]）；不引入 Hermes pluggable ContextEngine ABC（单机桌宠过重）；不做 ACE 三角色（太重）；repo-map 首版不做增量索引。

## Capabilities

### New Capabilities

- `per-model-context`: per-model 上下文窗口 + compaction 阈值的内置表 + 三层（内置/全局/项目）override 解析。code mode 按 project_root 取项目级，非 code mode 走两层。是新的"上下文预算大脑"，独立于 ContextAssembler 的 task_type policy。
- `long-run-context`: 长跑上下文维持机制——file-read dedup（语义层去重）+ SHA-256 内容寻址（字节层去重）+ Checkpoint-Restart Cycle（替代持续摘要）。三者叠加解决 read 循环 / 重复内容 / Frankenstein 历史。
- `code-repo-map`: code mode 的 codebase 接口全景（tree-sitter + PageRank），治本解决 cross-layer contract drift。

### Modified Capabilities

- `agent-loop`: ContextManager 阈值改为 per-model 比例；prompt cache breakpoint 装配顺序固定 + 前缀稳定纪律。
- `context-manager`: 三件套（B1 truncate / B2 compact / B3 budget）阈值全部 per-model 化；B2 in-place summarize 被 Checkpoint-Restart 替代（v2_enabled 闸控）。
- `frontend-ipc-surface`: 新增模型上下文配置卡片相关 ws 消息（list/get/set per-model override，global + project 两级）。

## Impact

### 代码影响

- 后端 Phase 1：~400 行（model_info.py ~150 + ContextConfig 改造 ~80 + dedup ~80 + cache breakpoint ~60 + IPC ~30）
- 后端 Phase 2：~500 行（SHA truncator ~150 + cycle 引擎 ~250 + migration + recall 工具 ~100）
- 后端 Phase 3：~400 行（repo_map.py ~300 + ContextAssembler 接入 ~100）
- 前端：~200 行（SettingsPanel 模型上下文卡片 ~150 + ws dispatch ~50）
- 数据库：SessionDB 新增 `context_cycles` 表 + FTS5（Phase 2）
- 依赖：Phase 3 加 tree-sitter + grammars（pyproject ABI pin，参考 FlagEmbedding 踩坑——解除 pin 前必须 dev venv 跑通 import）

### 运行时影响

- per-model resolve：启动 + 切模型时一次 TOML 读 + 深合并，O(1)，可忽略；启动落 `model_context_resolved` 日志
- file-dedup：每次 record_tool_result 一次 dict lookup + 可能一次 content 替换，O(1)
- SHA 内容寻址：≥1024 chars 多一次 sha256 + 落盘；命中复用零额外开销；落盘失败 fallback inline（绝不悬空引用）
- Checkpoint-Restart：触发时一次归档写 + 一次 LLM briefing 调用（~1 轮成本），换掉原本每次压缩的 LLM summarize，净持平或更省
- repo-map：每 code 会话首轮一次全量 tree-sitter（<5k 文件 <2s），结果吃 prompt-cache breakpoint 3

### 兼容性

- `[context.manager].v2_enabled=false` 一键回退旧绝对值逻辑（Strangler-Fig）
- 旧随机 ref_id code path 保留标 deprecated，过渡期双轨
- 无 override 文件时全走内置默认，老用户无感
- 2026-05-15 已落地的临时配置（config.toml 800K / ContextConfig 16K/300K）会被 Phase 1.1 的 per-model 化**取代**——届时 config.toml `[agent].context_window_tokens` 降级为 fallback only
