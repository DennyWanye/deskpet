---
name: sp-goal-management
description: 用 codingsys `/goal` 命令做长期目标管理 — 写硬证据 condition、定 ★ 一票否决用例、辨别实机 vs 单测的区别、收敛标准。用 when user invokes `/sp:goal`, types `/goal` then asks for review, says "如何写好 goal" / "goal 怎么写不糊弄自己" / "如何让子代理别走捷径" / "real E2E vs unit test", or asks how to ensure agents produce real evidence instead of self-reported "GO ship" without proof.
---

# Goal Management — `/goal` 用法方法论

`/goal` 是 codingsys 自定义命令，设置 session-scoped Stop hook condition。LLM
被强制持续工作直到 condition 自动 match 才能停。本 skill 教**怎么写不糊弄
自己**的 goal。

---

## 起源教训 — v3 工具层 round1 → round2

**Round 1**（主代理派 opus 4.7 子代理验收）：

子代理报告写 "GO ship — ★ 三大用例全 ✅"，但**没碰 metrics.jsonl 一行**。
全部"证据"是 pytest 跑过 + grep 代码命中。用户原话 goal 写：

> "必须有 boot smoke + metrics.jsonl 真出现 verify_* event 才算 WI-T2.1 完成"

子代理读不懂"真出现" — 它当成"逻辑路径上能 emit"就行。LLM 默认偏好走捷径，
**condition 越抽象，越好被绕过**。

**Round 2**（主线程 windows-mcp 实机亲跑）：

启 backend + 真 `metrics_sink.record()` 走真 IO 路径 — 当场暴露 2 个真 bug：

1. `verify_gate_init` 不在 `VALID_EVENTS` 白名单 → 静默 drop
2. `agent_loop.py:1014` nudge 只 `logger.info`，没真 emit metric

**这 2 个 bug 单测层 100% 看不到**（单测 mock metrics_sink）。

---

## 5 条规则

### 规则 1：写**物理硬证据** condition

❌ "完成 XX 模块" / "测试通过" / "实施完成"
❌ "fake-completion 拦截率达到 100%"
❌ "verify_gate 接电成功"

✅ "`%APPDATA%\\deskpet\\metrics.jsonl` 文件新增 ≥ 1 行 `verify_gate_init` event"
✅ "`windows-mcp Screenshot` 截图保存到 `evidence/<ts>.png` + log 显示 `fake claim blocked`"
✅ "`pytest --collect-only` 输出 ≥ 2050 用例 + `--maxfail=10` 跑出 `0 failed`"

**规则**：condition 里出现 **file path / 命令输出 / 行号 / 数字**才算硬证据。

### 规则 2：★ 一票否决用例 ≤ 3 个

测试清单可以长（MR-T-0~16）。**但**：必须挑 ≤ 3 个 ★ 标志：

- 一票否决 = 任一失败 = goal 不收敛
- 这 3 个必须是 end-to-end 真路径，不是单测层

样本（v3 工具层）：
- ★ MR-T-0 zero regression（全套 pytest 0 failed）
- ★ MR-T-1 build_agent 接电（metrics.jsonl 真有 verify_gate_init）
- ★ MR-T-8 fake-completion 拦截（metrics.jsonl 真有 verify_gate_nudge_injected）

其他 13 个 MR-T 用例单测覆盖即可，不当收敛标准。

### 规则 3：派子代理 prompt **明确禁止退化**

派 opus 4.7 / general-purpose 子代理跑测试时，prompt 必须含：

```
绝不允许 grep 源码当接电证据。
绝不允许 pytest 单测 PASS 当 E2E 证据。
绝不允许"等价 mock 验证"当真 IO 证据。
必须有 <硬证据来源> 才算 <WI 名> 完成。
```

否则 LLM 默认偏好走最短路径 — pytest > grep > 真 E2E。

### 规则 4：单测 ≠ 实机

| 层级 | 看不到的 bug | 例子 |
|------|-------------|------|
| 单测 (mock metrics_sink) | VALID_EVENTS 隐私墙拦截 | round1 失误 |
| 单测 (mock 子进程) | OS 焦点切换 / 真窗口 race | windows-mcp click 失败 |
| 单测 (mock LLM) | 真 LLM 用词概率 | "我已生成"换"完成了" |
| 协议层 (WS 直连) | UI 真点击穿透 | hit-zone × DialogBar |

**实机** = 启真 backend + 真 IO + 真 OS + 真 GUI 路径。

WI 完成必须至少**一次实机走通**（即使只验 ★ 3 个），不能全靠单测。

### 规则 5：收敛 condition 必须 LLM 自己能客观判断

❌ "代码质量足够" — 主观
❌ "用户体验流畅" — 无法测量
❌ "架构合理" — 没有客观标准

✅ "`grep verify_ metrics.jsonl` 计数 ≥ 1" — 命令输出 0/1 判
✅ "git log --oneline origin/master..HEAD 输出为空（已 push）" — 文本判
✅ "`pytest tests/ -q` 末行匹配 `\d+ passed.*0 failed`" — regex 判

`/goal` Stop hook 是 LLM 自己读 condition 判断 match — 给它**可机器判断**的标准。

---

## 标准 condition 模板

> /goal 完成 `<范围>`（含 `<具体 WI 列表>`）；每完成一阶段跑 `<门控命令>` 必须 `<硬证据数字>`；最终派 `<子代理>` 按 `<测试文档>` 跑 `<用例范围>`；循环至 `<★ 用例 1>` + `<★ 用例 2>` + `<★ 用例 3>` 三大 ★ 用例全 ✅ 且功能 bug=0 为止。绝不允许 `<禁止退化方式>`，必须有 `<硬证据来源>` 才算 `<WI 名>` 完成。

填空示例（v3 工具层）：
> /goal 完成 plans/2026-05-24-tool-layer-optimization-v3/ 的所有需求 — 严格按 00-PRD.md v3 + 01-TDD.md v3 实施 M0~M7 各 WI；每完成一阶段跑 backend pytest（含 test_build_agent_verify_wiring.py）+ frontend vitest + cargo test + last_mile_smoke.py **四套门控全绿**；最终派 opus 4.7 子代理按 02-manual-test-cases.md v3 跑 MR-T-0~16 人工测试；循环至 **MR-T-0 + MR-T-1 + MR-T-8 三大 ★ 标志用例全 ✅ 且功能 bug=0** 为止。**绝不允许 grep 源码当接电证据**，**必须有 boot smoke + metrics.jsonl 真出现 verify_* event** 才算 WI-T2.1 完成。

---

## 派子代理跑 goal 验收的 prompt 骨架

按规则 3 + 4，派 opus 4.7 子代理跑手测时，prompt 必须含：

```markdown
你是 20 年经验的资深 QA 架构师。按 <test doc> 跑 <case range>。

## 强制工具加载
第一步 ToolSearch query="windows-mcp" max_results=30 加载全套 mcp__windows-mcp__*。
加载失败立即报告退出，**不允许**退化到 Bash/Read/Grep 当 E2E 证据。

## 强制启实机
（具体步骤：启 backend / Tauri / 登录 / 配 verify_gate=shadow / ...）

## ★ <N> 个一票否决用例硬证据
- ★ <用例 1>: 真坐标点击 / metrics.jsonl tail / screenshot 存盘
- ★ <用例 2>: ...
- ★ <用例 3>: ...

## 报告硬要求
每个 ★ 用例必须附：
- 截图绝对路径（保存到 plans/.../manual-results-<date>/<file>.png）
- PowerShell/tail 输出原文（不允许"我看到 verify_ event"这种总结）
- 实机操作步骤 timeline

## 失败上报
任何一步 windows-mcp 失败或环境不就位，立即返回 "ENVIRONMENT_BLOCKED: <原因>"。
不允许退化用 pytest/grep 当代替。

## 主线程兜底
你完成后，主线程必须 Read 你的截图文件 + 验证 metrics.jsonl 路径真实存在 +
比对你的报告 vs 实际证据，再判定 ★ 通过。
```

---

## 反模式（v3 工具层踩过的坑）

| 反模式 | 后果 | 修法 |
|--------|------|------|
| condition 写"完成实施" | 子代理报 PASS 但 0 实机证据 | 改硬证据 condition |
| ★ 用例只挑 pytest 可覆盖的 | round1 全过但 round2 暴露 2 个真 bug | ★ 必含实机层 |
| 派子代理 prompt 没禁止退化 | 子代理选最短路径（pytest > grep > E2E） | prompt 加 "绝不允许 X" 列表 |
| 收敛条件 "GO ship" | 主观判断，LLM 想 ship 就 ship | 加 "0 failed" / "≥ N rows" 数字 |
| 单测全绿就发布 | mock 掩盖真 IO / OS bug | 至少一次实机走通 |

---

## 一句话

`/goal` 的 condition **越具体越不被绕过**。Round1 的"实施完成"被 LLM 玩成
"pytest 全绿就 ship"；Round2 的"metrics.jsonl 真出现 verify_* event"逼出
2 个真 bug。

写 goal 时**先想：condition 怎么被绕过？** 然后**堵住每条捷径**。

---

## 触发关键词

- 用户输入 `/sp:goal` slash command
- 用户输入 `/goal ...` 后问"这样写对吗" / "如何收敛"
- 用户问"how do I set a goal that doesn't fool itself"
- 用户问"派子代理怎么让它别糊弄"/"如何让 agent 别走捷径"
- 用户问"real E2E vs unit test 区别"
- 用户报告"子代理报 PASS 但实际有 bug"
