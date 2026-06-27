# Sprint Contract Template — 子代理派单标准三件套

每个 agent 派单时复制本模板填空。三件套 = task / handoff / status。

---

## task.md（输入给子代理）

```markdown
# Sprint Contract — <task-name>
**日期**: <YYYY-MM-DD>
**主线程**: claude-sonnet-4.7
**子代理**: <codex / claude-general-purpose / opus-4.7>
**worktree**: <absolute path>

## 输入
- spec 路径: <path>
- plan 路径: <path>
- 现有代码锚点（必读）:
  - <file:line> — <为什么相关>
  - ...

## 输出（验收标准）

### 必须生成 / 改动文件
- <path/file1.py> — <做什么>
- <path/file2.py> — <做什么>

### 必须通过的测试命令
```bash
cd <worktree path>
.venv/Scripts/python.exe -m pytest tests/test_X.py -v  # 必须 0 failed
```

### 验收锚点（主线程 Review 时检查）
- 文件 X 必须含 `<符号 / 字符串>`
- 测试 Y 必须 ≥ N 个用例
- 不允许出现 <反模式 / 禁用 API>

## 边界（不准动）
- 不准改: <list>
- 不准 push 到 origin（主线程统一 merge + push）
- 不准 git commit --no-verify
- 不准 git push --force
- 不准 rm -rf 任何目录
- 不准修改其他 worktree 的文件

## 失败上报
任何环境不就位 / 测试不绿 / 边界冲突 → 立即报：

```markdown
# BLOCKED
- 原因: <具体原因>
- 影响: <哪些验收点过不了>
- 建议: <主线程介入 / 改 spec / 换 agent>
- 已尝试的 workaround: <list>
```

退出。不允许 "我尽量" / "差不多" / "应该可以"。

## 时间预算
本任务预期 <N> 分钟。超出 <N×1.5> 必须报 STALLED 让主线程介入。
```

---

## handoff.md（子代理交回主线程）

子代理完成时必须写：

```markdown
# Handoff — <task-name>
**状态**: SUCCESS / BLOCKED / STALLED
**子代理**: <type>
**用时**: <minutes>

## 改动文件
- <path/file1.py> +<N> -<M>
- <path/file2.py> +<N> -<M>

## 测试输出（原文末 10 行，不是总结）
```
========================================
<pytest 真实 stdout 末 10 行>
========================================
```

## 边界守护记录
- 考虑过改的但没改: <list + 理由>
- 越界尝试: <list + 主线程批准状态>

## 已知遗留
- <未完成的子目标> — <原因 + 建议>

## 给主线程的 review 重点
- <file:line> — <为什么这块有风险，请重点看>
```

---

## status.json（机器可读）

```json
{
  "task_id": "<id>",
  "agent_type": "codex" | "claude-general-purpose" | "opus-4.7",
  "status": "success" | "blocked" | "stalled",
  "started_at": "<ISO 8601>",
  "ended_at": "<ISO 8601>",
  "duration_ms": <int>,
  "files_changed": ["path/a.py", "path/b.py"],
  "test_command": "...",
  "test_exit_code": 0,
  "test_passed": <N>,
  "test_failed": 0,
  "blocked_reason": null,
  "handoff_path": "/tmp/codex-handoff-<id>.md"
}
```
