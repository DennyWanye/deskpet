# HANDOFF Format — 子代理 ↔ 主线程交接

## 核心原则

handoff **不是总结**，是**证据+下一步建议**。主线程读 handoff 必须能：
1. 验证子代理真做了声称的事（凭原文输出，不是子代理自我评价）
2. 知道哪里要 review 重点（子代理自己标记的风险点）
3. 决定下一步：merge / 改 / 重派

---

## 必含 6 节

### 1. 状态头
```
**状态**: SUCCESS / BLOCKED / STALLED / NEEDS_REVIEW
**用时**: 实际花的分钟数（不是子代理估算）
**Agent type + Model**: codex / claude-general-purpose / opus-4.7
```

### 2. 改动清单（diff stat）
不写"我修改了一些文件"，写：
```
backend/foo.py +52 -18
backend/tests/test_foo.py +89 -0 (new)
backend/main.py +3 -1
```

### 3. 测试输出原文（末 10 行，不总结）
**不是**："所有测试通过"
**而是**：pytest 真实 stdout 末 10 行复制粘贴

### 4. 边界守护记录
列出："本来可能要改 X，但 Sprint Contract 说不准动，所以我做了 Y workaround"
帮主线程发现 spec 不完整或边界设错。

### 5. 已知遗留
诚实列没做完的：
- 子目标 Z 因 <原因> 未完成
- 建议: <下次怎么做 / 主线程怎么补>

### 6. Review 重点
子代理主动指：
- `<file:line>` — <这里我不太确定 / 用了 ugly hack / 性能可能有问题>

---

## 反模式

| 错误 handoff | 修正 |
|------------|------|
| "全部完成 ✅" | 列具体 commit hash + 测试输出 |
| "测试通过" | 贴 pytest 末 10 行原文 |
| "代码质量好" | 让主线程自己判断；只报事实 |
| "应该没问题" | 列 review 重点 + 已知风险 |
| 不写 BLOCKED 状态 | 卡了就报，不要假装做完 |

---

## 主线程读 handoff 的姿势

1. **Skim 状态头** — SUCCESS / BLOCKED 决定后续动作
2. **Read 改动清单** — 跟 Sprint Contract file scope 对账，看有没有越界
3. **Verify 测试输出原文** — 不信"我跑过了"，看真 stdout
4. **Skim Review 重点 + 已知遗留** — 决定要不要二次 review
5. **跑 sp-verification-before-completion** — 主线程自己再跑一次测试不盲信

---

## 多 agent 并行时

每个 agent 一份独立 handoff，主线程**单独读**。不要让 agent A 看到 agent B 的
handoff — 会污染独立性 + 互相抄。

主线程合并视角时，写自己的 **integration-handoff.md** 汇总所有子任务。
