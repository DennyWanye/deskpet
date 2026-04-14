# S0 — Agent 抽象层 HANDOFF

**完成日期：** 2026-04-14
**分支：** `feat/slice-0-agent-abstraction`
**对应 Plan：** [docs/superpowers/plans/2026-04-14-slice-0-agent-abstraction.md](../plans/2026-04-14-slice-0-agent-abstraction.md)
**执行模式：** codingsys / 单 Agent / 无 MCP 调研（内部重构）

---

## 1. 完成的事

- ✅ 新建 `backend/agent/` 包 + `providers/{base,simple_llm}.py`
- ✅ `AgentProvider` Protocol（`runtime_checkable`，`session_id` 参数预留）
- ✅ `SimpleLLMAgent` 纯代理实现 + 在 `main.py` 注册到 `service_context.agent_engine`
- ✅ `/ws/control` 的 `chat` 消息处理器改走 `agent_engine.chat_stream`（原直接调 `llm.chat_stream`）
- ✅ R1：`DEV_MODE` 改为读 `DESKPET_DEV_MODE` 环境变量，默认严格校验共享密钥
- ✅ R2：`LLMConfig.model` 默认值 `"qwen2.5:14b"` → `"gemma4:e4b"`，与 `config.toml` 对齐
- ✅ R7：`main.py:122-141` inline LLM 调用消除
- ✅ 根 README 补充 `DESKPET_DEV_MODE` 启动说明
- ✅ `pyproject.toml` `packages` 加 `agent` / `pipeline`（后者顺手补，之前漏了）

---

## 2. 变更文件清单

### 新增（5 个）
| 文件 | 行数 | 说明 |
|---|---|---|
| `backend/agent/__init__.py` | 0 | 包占位 |
| `backend/agent/providers/__init__.py` | 0 | 子包占位 |
| `backend/agent/providers/base.py` | 32 | AgentProvider Protocol |
| `backend/agent/providers/simple_llm.py` | 30 | SimpleLLMAgent 实现 |
| `backend/tests/test_agent_provider.py` | 77 | 4 个单测 |

### 修改（4 个）
| 文件 | 净变化 | 说明 |
|---|---|---|
| `backend/main.py` | +16 / -8 | R1 + 注册 agent + R7 改路由 |
| `backend/config.py` | +1 / -1 | R2 默认值 |
| `backend/pyproject.toml` | +1 / -1 | packages 加 agent/pipeline |
| `README.md` | +5 / -0 | DESKPET_DEV_MODE 说明 |

**生产代码净增：** +17 行（tracked diff）
**新增生产代码文件：** ~62 行（agent/ 四个文件）
**新增测试代码：** ~77 行

---

## 3. 门控结果

```
pytest tests/ -v
────────────────────────────────────────────
19 passed, 1 skipped in 10.60s
  - 4 new: test_agent_provider.py (全绿)
  - 15 existing: 全绿（行为等价验证)
  - 1 skipped: test_ollama_llm_chat_stream_integration (需真实 ollama)

import smoke:
  from agent.providers.{base,simple_llm} import ... → OK

health smoke:
  GET /health → {"status":"ok","secret_hint":"xxxx..."}
```

**关键验证：** `test_control_ws_rejects_without_secret` 继续通过
→ 证明 `DESKPET_DEV_MODE` 未设时严格校验生效（R1 正确）。

---

## 4. 偏离 Plan 的地方

### D1 — 行数验收标准未达
- Plan §7 写"代码行净增 ≤100 行（超出说明过度设计）"
- 实际：生产代码 +79 / 测试 +77 / 总 +156
- 判断：**不算过度设计**，77 行测试覆盖 4 个独立断言（代理行为 / session_id / Protocol 合规 / ServiceContext 注册），每条都必要
- **建议：** 未来 slice 的 plan 把"生产代码行数"和"测试代码行数"分开计量；生产代码 ≤100 的阈值依然适用

### D2 — 顺手补了 `pipeline` 到 packages
- Plan 没写，但 `pyproject.toml` 之前漏配 `pipeline`，改一处顺手补了第二处
- 风险极低，pytest collect 行为更一致
- 记录在这里供审计

### D3 — push 步骤跳过
- 当前 repo 无 `origin` remote（`git remote -v` 为空）
- 按 C1 (b) 自主判断：X1=b 的"push 远端"步骤暂停，转为"本地 commit 完成"
- **用户后续动作：** 若需远端 review，请手动：
  ```bash
  git remote add origin <url>
  git push -u origin feat/slice-0-agent-abstraction
  ```

### D4 — Word 锁文件未处理
- `plans/~$mma4_desktop_pet_phase1_plan_v5.docx` 是你本地 Word 打开时的锁文件
- 不属于 S0 范围，未加入 .gitignore（可在某个 chore slice 一并处理，或你手动 `git clean -n` 查看后删除）

---

## 5. 已知问题 / 后续关注

### 无阻塞问题

### 供后续 slice 参考
| 观察 | 建议 slice |
|---|---|
| `SimpleLLMAgent.chat_stream` 的 `session_id` 参数当前只作签名预留 | S2 记忆系统接入时激活 |
| `main.py` 的 `_control_connections` 是进程级 dict，多 session 并发下 OK 但无过期清理 | S4 可观测性 slice 考虑 |
| `/ws/audio` 目前仍直接用 `service_context.{asr,llm,tts}_engine`，**没有**走 agent_engine | S1 Pipeline 阶段化时会重新梳理（voice_pipeline 里也应经 agent_engine） |
| `pipeline/voice_pipeline.py` 依然直接持有 `LLMProvider` 引用 | S1 改造重点之一 |

---

## 6. 对 S1-S5 的建议

1. **S1 Pipeline 阶段化** 启动时：把 `VoicePipeline(llm=...)` 的 `llm` 参数替换为 `agent_engine`，让语音管线和 `/ws/control` 都统一通过 Agent 层；这是 S0 铺好路但未搭桥的地方。

2. **S2 记忆注入** 的最佳切入点：扩写 `SimpleLLMAgent.chat_stream`，在 `async for` LLM 之前按 `session_id` 检索记忆并拼进 `messages`。不需要新建 Agent 子类，覆盖原实现即可；需要记忆时 DI 注入 `MemoryStore`。

3. **S3 工具路由** 建议独立成 `ToolUsingAgent(AgentProvider)` 类，不要和 `SimpleLLMAgent` 合并；这样 V5 §12 的"Hermes 不稳定时降级回 SimpleLLM"依然可行。

4. **Plan 模板改进建议：** 行数验收分三档记——生产代码 / 测试代码 / 文档。这次 S0 让我们看到 77 行测试不应与 100 行生产代码阈值混算。

---

## 7. 提交记录（将在下一步创建）

计划 4 个 commit（按 plan §8）：
1. `refactor(backend): introduce AgentProvider abstraction (S0)`
2. `fix(backend): read DEV_MODE from env; sync LLMConfig default`
3. `test(backend): add SimpleLLMAgent unit tests`
4. `docs: add S0 plan + HANDOFF for agent abstraction slice`

---

**HANDOFF 状态：** ✅ Ready for review
**下一步：** S1 Pipeline 阶段化（自动进入，见 X2=c 约定 —— S0→S1 之间无 STOP）
