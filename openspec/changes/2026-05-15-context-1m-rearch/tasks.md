# Tasks — Context System Re-architecture

> 3 Phase，每 Phase 独立 ship + 独立 live E2E。勾选规则：单测绿 + 真 E2E 截图存 `evidence/` 才算 done。

## Phase 1 — 地基 + 止血（~1 sprint）

### 1.1 per-model context map + 三层配置（3 天）

- [ ] 1.1.1 写 `backend/tests/test_model_info.py`：BUILTIN 查表、缺失 model 走 `_default`、三层覆盖深合并、project 覆盖 > global > builtin（TDD 先行）
- [ ] 1.1.2 实现 `backend/llm/model_info.py`：`ModelContextInfo` dataclass + `BUILTIN` + `load_global_overrides()` + `load_project_overrides(root)` + `resolve(model, root)`
- [ ] 1.1.3 `ContextConfig` 改为注入 `model_info`，阈值变 `@property` 比例计算（D2）；保留 `v2_enabled=false` 旧路径
- [ ] 1.1.4 `CodeModeManager` 进 code session 时 `resolve(model, project_root)` 注入 ContextManager；非 code mode 走两层
- [ ] 1.1.5 启动 + 每次 resolve 落日志 `model_context_resolved model=.. window=.. source=..`
- [ ] 1.1.6 `SettingsPanel.tsx` 加"模型上下文"卡片：显示 resolved window + 来源链，可就地编辑写回 global/project TOML
- [ ] 1.1.7 单测全绿 + 真 E2E：切 deepseek↔claude-sonnet 观察 window 自动变（截图 + 日志存 evidence/）

### 1.2 File-read dedup（1.5 天）

- [ ] 1.2.1 写测试：同 path 读 3 次，断言历史里前 2 条被替换成 superseded marker，最后 1 条保留，message 下标不变
- [ ] 1.2.2 `ContextManager` 加 `_read_path_seen: dict[str,int]`；`record_tool_result` 检测读取类工具同 path 重复 → 原地替换旧 content（D3）
- [ ] 1.2.3 path 规范化（Windows 盘符大小写 / 正反斜杠）单测
- [ ] 1.2.4 真 E2E：复现今天 read-loop 任务（扫描+改 5 文件），断言 ≤30 轮收敛（对比改前 50 轮爆），截图存 evidence/

### 1.3 4-breakpoint prompt cache（2 天）

- [ ] 1.3.1 固定 messages 装配顺序（system→tools→memory→history→last-user-pre）单测
- [ ] 1.3.2 `openai_compatible.py`：前缀稳定纪律 — assistant tool-call-only 也带稳定 placeholder（D4）
- [ ] 1.3.3 Anthropic adapter 打 `cache_control`；OpenAI-compat 验证前缀字节稳定
- [ ] 1.3.4 加 cache 命中率日志（provider 回包的 cached_tokens 字段）
- [ ] 1.3.5 真 E2E：连续 5 轮对话，日志验证 cache 命中率 ≥50%，截图存 evidence/

## Phase 2 — 长跑架构（~1 sprint）

### 2.1 SHA-256 内容寻址 tool_result（3 天）

- [ ] 2.1.1 测试：≥1024 chars 落盘 `%APPDATA%/deskpet/tool_results/{sha}.txt`；同 content 二次出现复用同 sha；<1024 inline
- [ ] 2.1.2 `tool_result_truncator.py`：随机 ref_id → `sha256[:16]` 落盘 + 内存索引（D5）
- [ ] 2.1.3 `fetch_tool_result` 工具支持 `ref="sha:xxxx"` + byte range；落盘失败 fallback inline（绝不悬空引用）
- [ ] 2.1.4 旧随机 ref_id code path 保留标 deprecated + 单测覆盖兼容
- [ ] 2.1.5 真 E2E：同文件读 5 次，日志验证只占 1 份磁盘 + context 不增长，截图

### 2.2 Checkpoint-Restart Cycle（5 天）

- [ ] 2.2.1 migration：`context_cycles(sid, cycle_n, jsonl, ts)` 表 + FTS5 虚表
- [ ] 2.2.2 测试：估算 token ≥ `compact_at_tokens` 触发；归档完整；新 cycle 含 system+tools+结构化状态+briefing
- [ ] 2.2.3 `ContextManager` cycle 引擎：归档 → 模型自写 `<carry_forward>` briefing（模板 `prompts/cycle_briefing.md`）→ 重启（D6）
- [ ] 2.2.4 结构化状态采集：复用 TodoList / WorkingSet / PlanState
- [ ] 2.2.5 `recall_archived_cycle(sid, query)` 工具：SessionDB FTS5 BM25
- [ ] 2.2.6 supervisor max_iter rescue 在 cycle 边界注入 hint 的集成测试
- [ ] 2.2.7 真 E2E：2 小时长跑 supervisor 会话，断言 Checkpoint-Restart 触发 ≥1 次且任务继续不爆，截图 + cycle 归档文件存 evidence/

## Phase 3 — 治本（~1 sprint）

### 3.1 Aider repo-map（5 天）

- [ ] 3.1.1 加 tree-sitter + python/typescript/tsx grammar 依赖（pyproject ABI pin，参考 FlagEmbedding 踩坑经验）
- [ ] 3.1.2 测试：抽取 class/def/interface/type/export 签名（不含 body）；PageRank focus×50 / mention×10 / sqrt(refs)
- [ ] 3.1.3 `repo_map.py`：`build_repo_map(root, focus)` ≤ window*0.01 token（D7）
- [ ] 3.1.4 接入 ContextAssembler 的 code policy（吃 D4 cache breakpoint 3）
- [ ] 3.1.5 真 E2E：跨前后端任务（改后端 API + 前端调用），断言 agent 第一轮就引用对面层接口（[feedback_cross_layer_contract] 场景），截图

## 收尾

- [ ] 全 Phase 完成后跑全套 `cd backend && .venv/Scripts/python.exe -m pytest`
- [ ] `scripts/e2e_context_*.py` 全绿
- [ ] 更新 `docs/INDEX.md` + CHANGELOG
- [ ] `openspec archive 2026-05-15-context-1m-rearch`
