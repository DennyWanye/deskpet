# DeskPet 文档索引

**最后更新**: 2026-04-27
**用途**: 新会话快速定位当前工作文档

---

## 当前阶段

- **已 tag**: `v0.6.0-phase4-rc2`（P4 Poseidon 全栈集成完成 + S16 EmbedderStatusCard）
- **下一步**: P4 GA（验收 BGE-M3 真模型加载 + 全链 SLO + 真机 E2E smoke）→ 启动 P5 规划

### Phase 进度速览

| Phase | 状态 | tag |
|---|---|---|
| **P1** 基础壳 | ✅ GA | `v0.1.0-phase1` |
| **P2** 云端 + 路由 + 计费 | ✅ 已合 | — |
| **P3** 语音管线 + 启动加固 | ⚠️ rc1 ship · rc2 backlog 未清 | `v0.5.0-phase3-rc1` |
| **P4** Poseidon agent harness | ✅ rc2 全栈集成 | `v0.6.0-phase4-rc2` |
| **P5** 待规划 | ❓ | — |

---

## P4 Poseidon — 16 个 Slice 完成情况（rc2）

**核心成果**：三层记忆栈（L1 文件 + L2 SessionDB+FTS5 + L3 sqlite-vec/BGE-M3）
全部接通主线程，每回合聊天走 ContextAssembler，决策落 ContextTracePanel。

| Slice | 内容 | Commit |
|---|---|---|
| S0–S2 | 依赖、骨架、`v8→v9` 迁移、L2/L3 schema | (历史) |
| S3 | L3 RRF Retriever（vec+fts+recency+salience） | `8a28947` |
| S4 | L1 FileMemory（MEMORY.md / USER.md） | `c571ad1` |
| S5 | Embedder（BGE-M3 + mock fallback）+ VectorWorker | `18aa826` |
| S6 | LLM 多 provider | `47a19cd` |
| S7 | ContextAssembler v1（6 组件 + classifier + budget） | (历史) |
| S8 | ContextCompressor | `2cc9601` |
| S9 | MCPManager | `f10d3b9` |
| S10 | SkillLoader + 3 内置技能 | `f10d3b9` |
| S11 | 前端 MemoryPanel + ContextTracePanel + IPC | `f5eaa9b` |
| S12 rc1 | bench + SLO + tag | `e33507a` |
| S13 | 只读 wire-in | `bb2eb91` |
| S14 | ContextAssembler 接 chat handler | `a474ca4` |
| S15 rc2 | Embedder + Retriever + Dual-write + MCP | `8dd6185` |
| S16 | EmbedderStatusCard（前端可见 mock vs real） | `049d0d1` |

### 当前 P4 SLO 实测

| 指标 | 实测 | SLO | 状态 |
|---|---|---|---|
| 冷启动（mock embedder） | 98ms | <5s | ✅ |
| FileMemory.read_snapshot p95 | 0.21ms | <10ms | ✅ |
| MemoryManager.recall(L1+L2) p95 | 4.87ms | <30ms | ✅ |
| ContextAssembler.assemble p95 | 48ms | <370ms | ✅ |
| 冷启动（真 BGE-M3） | **未测** | <90s | ⏳ 进行中 |
| 首字延迟 p50（真 LLM） | **未测** | <1100ms | ⏳ |
| Prompt cache hit rate | **未测** | ≥80% | ⏳ |

### P4 仍未完成的尾巴

- **真 BGE-M3 激活**: 模型已下载（2.3GB），torch 升级到 ≥2.6 进行中（cu124 wheel 网络抖动重试）
- **OpenSpec archive**: 等真机 E2E 后 `/opsx:archive` 归档 P4 change
- **真机 Tauri E2E smoke**: Preview MCP 渲染 0×0 viewport，需要在真 Windows 机跑

---

## P3 rc2 backlog ⚠️（GA 前必办）

| 编号 | 任务 |
|---|---|
| **rc2-T1** | MSI 变体补齐 |
| **rc2-T2** | 真机 runbook 全量 smoke（T0-T8） |
| **rc2-T3** | 非 admin 用户安装验证（P3-G5） |
| **rc2-T4** | Updater endpoint 签名校验 |

详见 [`P3-rc2-backlog.md`](./P3-rc2-backlog.md)。

---

## 关键文档

| 文档 | 角色 |
|---|---|
| [`P4-agent-harness-prd.md`](./P4-agent-harness-prd.md) | P4 主 PRD（已签字，已落地） |
| [`P3-S10-installer-smoke-runbook.md`](./P3-S10-installer-smoke-runbook.md) | P3 installer smoke 测试 runbook |
| [`P3-S10-smoke-report-rc1.md`](./P3-S10-smoke-report-rc1.md) | P3 rc1 实测报告 |
| [`P3-rc2-backlog.md`](./P3-rc2-backlog.md) | P3 rc2 GA 前必办清单 |
| [`PACKAGING.md`](./PACKAGING.md) | 打包流程 |
| [`RELEASE.md`](./RELEASE.md) | 发布流程 |
| [`PERFORMANCE.md`](./PERFORMANCE.md) | 性能基线 |
| [`P2-2-realtime-duplex-voice-architecture.md`](./P2-2-realtime-duplex-voice-architecture.md) | P2 实时双工语音架构参考 |

---

## 当前已知技术债

| 项 | 严重度 | 说明 |
|---|---|---|
| dev 环境缺 faster_whisper | 中 | 本地 `python -c "import main"` 失败；prod PyInstaller 打包正常 |
| VectorWorker 时序 flake | 低 | 隔离跑必过；并发跑里偶发挂 |
| AppConfig 启动 warning noise | 低 | `[memory.l1/l2/l3/rrf]` 段被旧 dataclass 路径警告（已被 `config.raw` 兜底） |
| dual-write SQLite 双写成本 | 低 | 每条 chat 写 memory.db + state.db，I/O ×2 |
| BGE-M3 真模型未激活 | 中 | 已下 2.3GB；待 torch ≥2.6 升级（进行中） |

---

## 相关 commit

- `049d0d1` feat(p4-s16): EmbedderStatusCard
- `8dd6185` feat(p4-s15): full-stack integration — Embedder/Retriever/MCP
- `a474ca4` feat(p4-s14): wire ContextAssembler into chat handler
- `bb2eb91` feat(p4-s13): read-only P4 wire-in
- `e33507a` feat(p4-s12): phase-4 rc1 bench + SLO
- `f5eaa9b` feat(p4-s11): MemoryPanel/ContextTrace UI + IPC
- `f10d3b9` feat(p4-s9+s10): MCP client + Skill system
