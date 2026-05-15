# Design — Context System Re-architecture

> 配套 proposal.md。本文只写关键设计决策（Dx）+ 数据结构 + 集成点，不重复 proposal 的调研。

## D1 — per-model 三层配置解析（Phase 1.1）

**决策**：built-in dataclass ← 全局 TOML ← 项目 TOML，深合并，后者只覆盖出现的字段。

```
backend/llm/model_info.py
  BUILTIN: dict[str, ModelContextInfo]            # 代码内置
  load_global_overrides() -> dict                 # %APPDATA%/deskpet/model_overrides.toml
  load_project_overrides(project_root) -> dict     # <root>/.deskpet/context.toml
  resolve(model: str, project_root: Path|None) -> ModelContextInfo
```

- `resolve()` 纯函数、无副作用、可单测（fixture 传假路径）
- code mode：`CodeModeManager.get_state(sid).project_root` → 传给 `resolve()`
- 非 code mode：`project_root=None`，只走前两层
- 启动 + 每次 resolve 落 INFO 日志：`model_context_resolved model=%s window=%d source=%s`（source ∈ builtin/global/project）

**为什么 TOML 不 JSON**：与 config.toml 一致；用户手编友好；项目级 `.deskpet/context.toml` 可进项目 git 让团队共享。

**为什么不进 config.toml 的 [agent] 段**：config.toml 是 app 级单值，per-model 是矩阵，混在一起会逼用户在 app 配置里写一堆模型。独立文件 + 独立 UI 卡片更清晰。

## D2 — ContextManager 读 resolved window 而非 hardcode（Phase 1.1）

现状 `ContextConfig` 的阈值是 `@dataclass` 默认绝对值。改为：

```python
@dataclass
class ContextConfig:
    model_info: ModelContextInfo            # 注入，不再 hardcode
    @property
    def compact_at_tokens(self) -> int:
        return int(self.model_info.context_window * self.model_info.compact_at_pct)
    @property
    def tool_result_threshold(self) -> int:
        # 按 window 比例，不再死 16K。1M → ~40K，200K → ~8K
        return max(8_000, self.model_info.context_window // 25)
```

`budget_warn_pct / budget_block_pct` 保留（0.80/0.95 已和业界一致），分母换成 `model_info.context_window * effective_pct`。

向后兼容：`[context.manager].v2_enabled=false` 时退回旧的绝对值 ContextConfig（Strangler-Fig 回退闸）。

## D3 — File-read dedup（Phase 1.2，最便宜的解药）

**决策**：在 `ContextManager.record_tool_result()` 维护 `dict[normalized_path, last_iter]`。当同一 path 再次被 `read_file`/`mcp_filesystem_read_text_file` 读取，把**历史中旧的那条 tool_result** 原地替换成：

```
<file {path} was re-read at iteration {N}; superseded — see the later read>
```

- 只对"读取类"工具生效（read_file / mcp_filesystem_read_text_file / 可配白名单），不动 write/edit/run_shell
- path 规范化：`Path(p).resolve()` 统一大小写盘符（Windows）
- 不删消息（保持 message 数组下标稳定，避免 tool_call/tool_result 配对错位），只换 content
- 与 SHA-256（Phase 2.1）的关系：dedup 是"语义层"（同文件最新版赢），SHA 是"字节层"（任意重复内容去重）。两者叠加，Phase 1.2 先上 dedup 因为它直击 read-loop。

**为什么这个最便宜又最有效**：今天 sid=code-rkjdd9vo 的 50 轮爆，日志显示反复 `read_file Home.jsx` / `fetch_tool_result` 同一 ref，dedup 直接把"看 v1 代码做决策"的病根切掉。

## D4 — 4-breakpoint prompt cache（Phase 1.3）

参考 Claude Code 的 4 个 breakpoint。OpenAI-compat（chinzy/deepseek）走 **prefix cache**，命中条件是 history 前缀字节稳定。

**决策**：把 messages 装配顺序固定为稳定→易变，并在 provider 层对前缀打 cache 标记（Anthropic `cache_control`；OpenAI-compat 靠前缀稳定自动命中）：

```
[0] system (persona + 静态指令)          ← 永不变，breakpoint 1
[1] tools schema                          ← 版本内不变，breakpoint 2
[2] memory/repo-map block (ContextAssembler 产出) ← 每会话稳定，breakpoint 3
[3..] 对话历史 + tool_results              ← 易变
[last] 最近 user turn 之前                  ← breakpoint 4
```

纪律（抄 DeepSeek-TUI）：**绝不改写历史前缀**。`assistant` 消息即使是 tool-call only 也要带稳定 placeholder（不要某轮有 reasoning_content 某轮没有，会破字节哈希）。这条纪律也是 Checkpoint-Restart（Phase 2.2）选择"归档+重启"而非"in-place 改写"的根本原因。

## D5 — SHA-256 内容寻址（Phase 2.1，升级现有 ref_id）

现状 `tool_result_truncator.py` 用 `secrets.token_urlsafe` 随机 ref_id + 内存 LRU 256。改为：

```
content ≥ 1024 chars:
  sha = sha256(content).hexdigest()[:16]
  落盘 %APPDATA%/deskpet/tool_results/{sha}.txt（不进项目树）
  history 里只放 <TOOL_RESULT_REF sha="..." bytes=N retrieve via fetch_tool_result(ref="sha:...")>
  二次出现同 content → 同 sha → 自动复用，零额外占用
```

- `fetch_tool_result` 工具扩展：支持 `ref="sha:xxxx"` 形式，按 byte range 读盘
- LRU 改成磁盘 + 内存索引（落盘后内存零占用）
- 跨会话/重启可 retrieve（现状 LRU 重启即丢）
- 兼容旧随机 ref_id：保留旧 code path，标 deprecated

## D6 — Checkpoint-Restart Cycle（Phase 2.2，核心架构）

**决策**：用 cycle 替代 B2 的 in-place summarize。

```
ContextManager 维护 current_cycle: list[Message]
触发：estimated_tokens >= model_info.compact_at_tokens（per-model！依赖 D1）
动作：
  1. 归档 current_cycle 整段 → SessionDB.context_cycles(sid, cycle_n, jsonl, ts)
  2. 让模型自写 <carry_forward> briefing（≤ window*0.01 token，模板见 prompts/cycle_briefing.md）
  3. 新 cycle = [system] + [tools] + [结构化状态: TodoList/PlanState/WorkingSet] + [briefing]
  4. 旧 ref_id/sha 不失效（D5 落盘的还能 fetch）
查询归档：新工具 recall_archived_cycle(sid, query) → SessionDB FTS5 BM25
```

- 结构化状态来源：复用现有 `todo_write` 的 TodoList、code_mode 的 WorkingSet、plan.py 的 PlanState
- 与 supervisor 的关系：supervisor max_iter rescue 可在 cycle 边界注入 hint（自然的"喘息点"）
- 为什么 > in-place summarize：homogeneous fresh context，无 Frankenstein，prefix cache 干净（呼应 D4 纪律）

## D7 — Aider repo-map（Phase 3.1）

```
backend/deskpet/code_mode/repo_map.py
  build_repo_map(project_root, focus_files: set[Path]) -> str  # ≤ window*0.01 token
```

- tree-sitter grammar：python + typescript + tsx（覆盖 deskpet 自身 backend+frontend，也覆盖用户多数项目）
- 抽取：class/def/interface/type/exported-const 签名（不含 body）
- PageRank 排序（抄 Aider）：focus_files ×50 / 对话提到的标识符 ×10 / 引用计数 sqrt 去霸榜
- 注入位置：ContextAssembler 的 code policy，作为 memory block 一部分（吃 D4 breakpoint 3 的 cache）
- 首版全量重算（项目 <5k 文件，<2s）；增量索引留后续

## 集成点总表

| 文件 | Phase | 改动 |
|---|---|---|
| `backend/llm/model_info.py` (新) | 1.1 | ModelContextInfo + 三层 resolve |
| `backend/agent/context_manager.py` | 1.1/1.2/2.1/2.2 | ContextConfig 读 model_info；dedup；SHA；cycle |
| `backend/deskpet/code_mode/state.py` | 1.1 | project_root → resolve() 注入 |
| `backend/providers/openai_compatible.py` | 1.3 | cache breakpoint / 前缀稳定纪律 |
| `backend/agent/tool_result_truncator.py` | 2.1 | 随机 ref_id → SHA-256 落盘 |
| `backend/deskpet/memory/migrations/*.sql` (新) | 2.2 | context_cycles 表 + FTS5 |
| `backend/deskpet/code_mode/repo_map.py` (新) | 3.1 | tree-sitter + PageRank |
| `tauri-app/src/components/SettingsPanel.tsx` | 1.1 | 模型上下文配置卡片 |
| `config.toml` | 1.1 | `[context.manager].v2_enabled` 回退闸 |

## 测试策略

- 纯函数（resolve / repo_map ranking / sha dedup）→ 密集单测
- ContextManager 行为 → 单测 + `scripts/e2e_context_*.py` 真 backend smoke
- 每 Phase 终验：computer-use UI-level E2E + 截图（[feedback_real_test]）
- 跨层场景（Phase 3）：`scripts/e2e_*.py` 覆盖 frontend↔backend（[feedback_cross_layer_contract]）
