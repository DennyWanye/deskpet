# 2026-05-16 — Companion-Session Context Isolation & Capability Gate

> **Status**: Proposed · **Effort**: 1 sprint · **Risk**: medium (touches retriever RRF + chat entry; mitigated by feature flag + TDD) · **Trigger**: real-world bug 2026-05-16 01:02

## Why

实测严重 bug（2026-05-16 01:02，铁证在 `backend/userdata/data/state.db`）：

- 用户在 **`default` 陪伴 session** 说："你能帮我生成一个海报图片嘛？"
- Agent 第一个动作 `list_directory .`，第二个 `memory_search {"query":"VPN Python CLI scaffold project"}`，然后 `mkdir backend/vpn-cli` 一路建了 17 个文件
- 用户 3 分钟后被迫"请停止"

根因三连：

1. **跨 session 记忆污染（主因）**：8 天前用户在 code session `code-tyfbt62t` 让做"C 端 VPN"。ContextAssembler 的 L1/L2/**L3 BGE-M3** 召回**不按 session 隔离**（设计如此 — 跨项目记忆）。`default` 陪伴 session 的"画图"请求被 8 天前最突出的 VPN 项目记忆劫持。**这是 BGE-M3 mock→真 修复（2026-05-15）引入的回归**：mock 召回是噪声反而安全；真召回强到能盖过当前无关请求。
2. **无能力请求无 graceful refuse**：deskpet 没有图像生成工具。LLM 拿到无法完成的请求，没有合法动作可做 → 漂移到记忆里最"可执行"的旧项目。
3. **companion session 无写盘 scope**：`default` 陪伴 session 直接 `mkdir G:\projects\deskpet\backend\vpn-cli` 往代码仓库写 17 个文件，没有任何 scope 约束。

这正是 real-E2E 的价值：单测全绿（context-1m-rearch 1304 passed）但真实交互暴露跨层 bug（呼应 [feedback_cross_layer_contract] / [feedback_real_test]）。

## What Changes

- **MODIFIED** `backend/memory/retriever.py`：RRF 召回加 **session-affinity 信号**。当前 session 的记忆原权重；其它 session 的记忆按"陪伴 vs code"关系降权。companion (`default`) session 召回 code-session 的强项目记忆时大幅降权，避免无关强记忆盖过当前请求。保留跨 session "桌宠记得你"能力（人物/偏好类记忆不降权，只降"项目任务"类）。
- **NEW** `backend/agent/capability_gate.py`：请求进 agent loop 前的能力门。识别"明显需要 deskpet 没有的能力"的请求（图像/视频/音频生成等），直接 graceful refuse + 建议替代，不进 code/task 漂移。**不是沙箱** — 是"做不到就老实说"，单机桌宠的诚实护栏（符合 [feedback_no_sandbox_constraints]：不加确认弹窗，只防漂移）。
- **MODIFIED** chat handler：`default`/companion session 的文件写入工具默认限定在 workspace 根（`%APPDATA%/deskpet/workspace` 或配置项），不允许写任意仓库路径。code session 不受影响（它本来就绑定 project_root）。feature flag `[companion].write_scope_enforced`（默认 true，可回退）。
- **NEW** `config.toml [companion]` 段：`memory_cross_session_decay`、`write_scope_enforced`、`capability_gate_enabled` 三个开关。

**非目标**：不改 L3 BGE-M3 本身（它是对的）；不加权限确认弹窗；不做通用沙箱；不引入图像生成能力（那是单独功能，不在本 change）。

## Capabilities

### New Capabilities

- `capability-gate`: agent loop 前置能力门。把"deskpet 无对应工具能完成"的请求在进 loop 前 graceful refuse，杜绝"无法完成 → 漂移到记忆里的旧项目"。

### Modified Capabilities

- `memory-recall`: retriever RRF 增加 session-affinity 维度；companion session 对 code-session 的项目类记忆降权，保留人物/偏好类跨 session 记忆。可经 `[companion].memory_cross_session_decay` 调节，=1.0 时退回旧行为（Strangler-Fig）。
- `agent-loop`: companion session 写盘工具 scope 限定（feature-flagged，code session 不受影响）。

## Impact

### 代码影响
- 后端：~300 行（retriever session-affinity ~120 + capability_gate.py ~100 + chat handler write-scope ~50 + config ~30）
- 测试：新增 retriever 跨 session 降权单测、capability_gate 单测、companion write-scope 单测；复现 2026-05-16 bug 的回归测试（"画图"请求在 default session 不得触发 code 漂移）
- 配置：`config.toml` 新增 `[companion]` 段
- 数据库：无新表（messages 已有 session_id，retriever 改查询权重即可）

### 运行时影响
- retriever RRF：每次召回多算一个 session-affinity 权重，O(召回条数)，可忽略
- capability_gate：每请求一次轻量分类（rule-first，命中才 LLM 兜底），<5ms 常路
- write-scope：companion session 写盘工具加一次 path 前缀检查，O(1)

### 兼容性
- `memory_cross_session_decay=1.0` → 退回旧召回行为
- `write_scope_enforced=false` → 退回旧自由写盘
- `capability_gate_enabled=false` → 退回旧"啥都进 loop"
- code session 全程不受影响（本 change 只动 companion/default 路径）
