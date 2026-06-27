# max_tokens Truncation Fix (2026-05-14)

## 缘起

用户报告两个 code session 卡死。打开 Code Mode 看到顶栏红色横幅：
> 桌宠提醒 code-rkjdd9vo 编辑文件超时导致卡住了，需要我怎么做？
> [重试编辑] [取消任务]

两个项目都打 X 错误标记，循环 `permanent_tool_error` → `auto_resume_engaged`。

## 根因诊断（两次假设 + 一次真因）

### 假设 1（错）：LLM 转义错误 `\'react\'` 不合法 JSON

观察 log：
```
WARNING p5s2_tool_call_args_malformed
  args_full='{"path": "...", "content": "import { x } from \'react\';\\n..."'
  parse_error='Unterminated string starting at: line 1 column 80 (char 79)'
```

直觉：LLM 把 React import 里的 `'` 错误地转义成 `\'`，invalid JSON。

**修复 1**：写了 `\'` → `'` 的 repair pass + 3 个 unit test，pass，commit `0cc49b7` 后续。

**结果**：重启 deskpet 后 `p5s2_tool_call_args_repaired` count = **0**, malformed 仍然 fire。修复没生效。

### 假设 2（错）：repair 函数 bug

怀疑 repair 没正确部署。检查：
- pyc 文件 mtime > py 文件 mtime（已重编译）✓
- backend PID 已变（新进程）✓
- 直接用 Python 跑相同 args_buf 试 repair → **fail**

写更 aggressive 的 3-strategy repair：
1. `\'` → `'`
2. regex 删除所有非法 JSON escape `\X`（X ∉ `"\/bfnrtu`)
3. `strict=False`（允许 raw 控制字符）

加 7 个 unit test 全过。重启 deskpet。

**结果**：3 个 strategy 都 fail，dev log 还在出 `malformed`。

### 真因（对）：max_tokens 太小 → LLM 输出被截断

用 `scripts/debug_repair.py` 直接喂 log 里失败的 args，发现：
- raw args_buf 长 ~5KB 但 **JSON 字符串没闭合**
- 错误 `Unterminated string starting at column N` 指向 content 值的开 `"`
- LLM 写到一半 stream 就停了，content 字段没结束 — 不是 escape 问题

**真根因**：`max_tokens=2048` 默认太小。一个 6KB React 文件 ≈ 2000+ tokens，加上 path 字段、tool_call 包装、JSON 结构开销，**必然**超过 2048 token 限制，LLM 在生成 content 字符串中段被强制停止。`args_buf` 落地时缺尾，JSON 永远 parse 不了。

代码定位：
- `backend/providers/openai_compatible.py:128`：默认 2048
- `backend/agent/agent_loop.py:660`：调用 LLM 时也是默认 2048

### 真修复

1. **`max_tokens` 默认 2048 → 8192**（4×headroom，约支持 24KB 输出，足够大多数 write_file）
2. **agent_loop dispatch 端智能 hint**：检测到 `args_len > 3000 AND parse_error 含 "Unterminated string"` 时，给 LLM 的错误反馈从"长字符串里 \\n / \\\" / \\\\ 没正确转义"（误导）改为"输出被 max_tokens 截断了，拆短或分多次写"（准确）。新错误码 `tool_call_args_truncated_by_max_tokens`，区别于 `tool_call_args_malformed_json`。
3. **保留 3-strategy repair** 作为防御性兜底（虽然 max_tokens 已修，未来其他 escape bug 仍能被 catch）。

## Live 验证（2026-05-14）

### 测试方法

`backend/scripts/p6_large_write_test.py` — ws 客户端发 prompt 让 LLM 用 write_file 写一个 5000+ 字符的 React 组件（精准触发原 bug 路径）。

### 结果

```
Elapsed: 255.5s
Tool calls: 6
Saw final: True
Termination: end_turn        ← agent 自然完成
Saw truncation hint: False   ← 无截断发生
```

dev log（660s 监控期间）：

| 指标 | 修复前 (max=2048) | 修复后 (max=8192) |
|---|---|---|
| `p5s2_tool_call_args_malformed` | 1+（持续） | **0** |
| `p5s2_tool_call_args_repaired` | 0（repair fail） | 0（不需要） |
| `tool_call_args_truncated_by_max_tokens` | n/a | **0** |
| Traceback / Exception / hallucination / chat_v2_error / RuntimeError / TypeError / KeyError / AttributeError / permanent_tool_error / all_providers_failed / circuit_breaker_open | 持续触发 + auto_resume | **0** |

### 收益

- LLM 不再因 token 限制中途截断
- write_file 6KB+ React 文件一次性成功
- 没有触发新 hint（说明 8192 对当前 workload 充足）
- 0 真错误，agent 完整推进任务

## 改动文件

```
backend/providers/openai_compatible.py    +52/-7   (3-strategy repair + 8192 default)
backend/agent/agent_loop.py               +44/-7   (truncation-detection hint + 8192)
backend/tests/test_repair_apostrophe.py   +118     (NEW — 7 tests covering all 3 strategies)
backend/scripts/p6_large_write_test.py    +112     (NEW — large write E2E reproducer)
backend/scripts/debug_repair.py           +60      (NEW — repair-strategy debugging utility)
```

后端 baseline 全绿: **1236 passed / 10 skipped**.

## 经验总结

1. **从 UI 视觉验证 + 真实任务**才能发现"奇奇怪怪的问题"。之前 5 轮 ws 脚本压测都没触发 `max_tokens` 上限，因为 prompt 都是简单的探索任务，LLM 输出不大。
2. **看日志的 error 字符串不要被误导**。"Unterminated string ... 没正确转义" 这种 hint 把 LLM 引向错路。错误 hint 比无 hint 更糟。
3. **`max_tokens` 默认 2048 是 2024 年的设定**。2026 年的 thinking-mode 模型 + 复杂代码任务，2048 已经不够。8192 是当前合理 baseline。
4. **多层防御不能掩盖根因**。repair pass 失败时不要硬加更多 repair，应该回去找 root cause。我加 3-strategy repair 后仍然 fail，逼我去看 args_buf 完整性 → 发现 truncation。
