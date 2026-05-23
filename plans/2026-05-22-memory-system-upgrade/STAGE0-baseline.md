# Stage 0 — 第一代召回 baseline 度量报告（WI-M0.2）

**日期**: 2026-05-23
**方法**: 中文回测集 fixture（`deskpet/memory/eval/zh_fixture.py`，40 条
固化中文对话 + 35 条 `(query, expected_msg_id)` 配对）→ `MetricsRunner`
回放第一代 `Retriever`（RRF: vec + FTS5 + recency + salience）。

## baseline 指标（钉死在 `deskpet/memory/eval/zh_baseline.json`）

| 指标 | 值 |
|---|---|
| qa_set_size | 35 |
| hit@1 | 0.0000 |
| hit@5 | 0.1143 |
| hit@10 | 0.2000 |
| MRR | 0.0620 |
| token_per_query | 198.23 |

## 度量口径

- **token_per_query**（PRD §3 D11）：召回结果渲染进 system prompt 的
  L3/facts 文本段 token 估算 —— 复刻 `assembler/components/memory.py` 的
  `_render_l3_only` 渲染 + `_approx_tokens`（1 token ≈ 4 chars），不含
  L1 静态档案、不含对话历史。`EvalReport` 已新增该字段。
- **回测集来源**：worktree `.dev-userdata` 是空库造不出回测集；主 checkout
  真实库不可复现。故 WI-M0.2 采用**人工固化的中文 fixture** 作为可复现
  稳定基准（PRD §4.1 WI-M0.2 第二落点）。fixture 是确定性的 —— 任何机器
  `seed_zh_fixture()` 得到逐字节一致的回测集。

## 环境约束（影响绝对值，不影响回归检测）

- **无 BGE-M3 权重**：本机未安装 `bge-m3-int8` 模型，eval 走 **mock
  embedder**（hash 向量，确定性但语义无关）。vec 召回路因此是噪声，真实
  召回信号仅来自 FTS5 + recency。
- **FTS5 中文分词**：默认 `unicode61` tokenizer 把连续中文当单 token，
  query「我对什么食物过敏」与消息「我对花生过敏」无共享 token 串 → FTS
  难命中。这是 hit@5 偏低（0.11）的主因。
- **结论**：absolute hit@5 偏低是环境受限（无模型 + CJK 分词），**不是**
  第一代召回的真实水平。baseline 的价值是**可复现的回归参照** —— eval
  门控据此检测「flag 全关 0 回归」与「token 增幅 ≤ +30%」，绝对值高低
  不影响门控有效性。装上 BGE-M3 后重跑 `--update-baseline` 可得真实基线。

## eval 门控（PRD §3 D8）

`backend/scripts/eval_gate.py` —— pre-merge 自动化门控：

```
cd backend
python -m scripts.eval_gate                  # 回归则 exit 1
python -m scripts.eval_gate --update-baseline # 重钉 baseline
python -m scripts.eval_gate --json            # 只出 JSON
```

判定：`hit@5` 不得回归（容差 0.02）；`token_per_query` 增幅 ≤ +30%。
脚本固定用 mock embedder + 钉死 fixture，确定性可复现，不依赖模型权重。
已验证：连续两次跑结果一致、门控 PASS。
