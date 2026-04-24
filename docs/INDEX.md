# DeskPet 文档索引

**最后更新**: 2026-04-24
**用途**: 新会话快速定位当前工作文档

---

## 当前阶段

- **已 ship**: `v0.5.0-phase3-rc1`（内部 RC，带 smoke 缺口）
- **下一阶段**: Phase 4 — Agent Harness + Long-term Memory（代号 **Poseidon**）
- **目标版本**: `v0.6.0-phase4`

---

## Phase 4 — 规划阶段（当前主工作流）

| 文档 | 角色 | 状态 |
|---|---|---|
| [`P4-agent-harness-prd.md`](./P4-agent-harness-prd.md) | **主 PRD**（13 节，~1200 行）— 架构、拿来映射、11 slice 计划、性能预算、ContextAssembler 智能上下文组装器 | **Draft**，待用户签字后走 `openspec propose` |

**PRD 关键要点**：
- **拿来源**：Hermes（MIT，直接 lift 60% 代码）+ Claude-Code-Best（无 license，pattern clean-room 重写 20%）+ 自研 20%
- **三层记忆**：L1 文件（MEMORY.md/USER.md）+ L2 SessionDB（SQLite + FTS5）+ L3 向量（sqlite-vec + BGE-M3）
- **ContextAssembler（原创）**：agent loop 之前的智能组装层，按任务类型挑选组件
- **12 slice × ~1.3d = 16 人日**，4 周日历
- **10 个开放决策点**（§9）需用户签字

---

## Phase 3 — 遗产（rc1 已 ship，rc2 未启动）

| 文档 | 角色 |
|---|---|
| [`P3-S10-installer-smoke-runbook.md`](./P3-S10-installer-smoke-runbook.md) | Installer smoke 测试 runbook（T0-T8 全表）|
| [`P3-S10-smoke-report-rc1.md`](./P3-S10-smoke-report-rc1.md) | rc1 实测报告，记录 VirtualBox GPU passthrough 结构性限制导致的 smoke 缺口 |
| [`P3-rc2-backlog.md`](./P3-rc2-backlog.md) | rc2 TODO：MSI 补齐、真机 smoke、非 admin 验证、updater 签名 — GA 前必办 |

---

## 通用 / 跨阶段

| 文档 | 角色 |
|---|---|
| [`PACKAGING.md`](./PACKAGING.md) | 打包流程 |
| [`RELEASE.md`](./RELEASE.md) | 发布流程 |
| [`PERFORMANCE.md`](./PERFORMANCE.md) | 性能基线 |
| [`P2-2-realtime-duplex-voice-architecture.md`](./P2-2-realtime-duplex-voice-architecture.md) | P2 实时双工语音架构参考 |

---

## 决策待办（对 @owner）

在进入 P4-S1 之前，PRD §9 的 10 个问题需要拍板：

1. LLM provider 默认值
2. 本地 LLM fallback
3. Skill 热加载策略
4. 记忆加密默认值
5. Web 搜索 provider
6. 子 agent 记忆共享模式
7. Embedding 不可用降级
8. TaskClassifier 第 3 层 LLM 默认开关
9. AssemblyPolicy 用户覆盖仲裁规则
10. Assembler feedback 在线学习时机

---

## 相关 commit

- `c3cba05` docs(P4): ContextAssembler 智能上下文组装器并入 PRD
- `09abdac` docs(P4): agent harness + memory PRD (Poseidon)
- `8db3c25` docs(P3): rc2 backlog tracking rc1 smoke gaps
- `f63b4bc` docs(P3-S10): smoke report for v0.5.0-phase3-rc1
