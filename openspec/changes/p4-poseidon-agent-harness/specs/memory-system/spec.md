# Spec: memory-system

## ADDED Requirements

### Requirement: Three-Layer Memory Architecture

系统 SHALL 提供三层记忆架构：L1 文件记忆（MEMORY.md + USER.md）、L2 会话数据库（SQLite + FTS5）、L3 向量记忆（sqlite-vec + BGE-M3 embedding）。三层通过统一的 `MemoryManager` 对外暴露，各层可独立降级失败。

#### Scenario: All three layers queried on recall

- **WHEN** ContextAssembler 调用 `memory_manager.recall(query, policy)` 且 policy 指定 `l1=snapshot, l2.top_k=10, l3.top_k=10`
- **THEN** MemoryManager MUST 并行查询三层，融合后返回不超过 policy 指定的总条数

#### Scenario: L3 failure degrades gracefully

- **WHEN** L3 向量索引不可用（BGE-M3 未加载完、sqlite-vec 扩展加载失败）
- **THEN** recall MUST 返回仅 L1 + L2 的结果，不得抛异常中断主流程；error 记入日志

### Requirement: File Memory (L1) — MEMORY.md and USER.md

系统 MUST 维护两个 markdown 文件作为 L1 记忆：`MEMORY.md`（桌宠观察）和 `USER.md`（主人画像）。条目用 `\n§\n` 分隔。MEMORY.md MUST ≤ 50KB，USER.md MUST ≤ 20KB，超过时按 salience 淘汰最低项。

#### Scenario: memory tool add writes to file

- **WHEN** agent 调用 `memory(action="add", content="主人晚上 9 点后不喜欢高音", target="MEMORY.md")`
- **THEN** 系统 MUST 把新条目 append 到文件末尾，用 `§` 分隔，立即落盘

#### Scenario: Size cap eviction

- **WHEN** MEMORY.md 写入后体积 > 50KB
- **THEN** 系统 MUST 按 salience（或默认写入顺序）淘汰最旧/最低项直到 ≤ 50KB

### Requirement: Frozen Snapshot Pattern for Prompt Cache

Agent 启动新 session 时 MUST 读取当前 MEMORY.md + USER.md 内容注入 system prompt 作为 frozen snapshot。session 进行中的 memory 写入 MUST 立即落盘但 NOT 修改当前 session 的 system prompt（保护 prompt cache）。下次新 session 开始时才读取新版本。

#### Scenario: Mid-session write does not invalidate cache

- **WHEN** session 进行中 agent 调用 `memory(action="add", ...)`
- **THEN** 文件 MUST 立即写入，但当前 session 的 system prompt 不变；后续 LLM 调用 MUST 仍命中 prompt cache

#### Scenario: New session reads updated files

- **WHEN** 前一次 session 结束后启动新 session
- **THEN** 新 session 的 system prompt MUST 反映最新的 MEMORY.md + USER.md 内容

### Requirement: Session Database (L2) — SQLite with FTS5

系统 MUST 使用 SQLite 数据库存储 sessions 和 messages 表，支持 FTS5 全文索引。数据库 MUST 启用 WAL 模式。应用层 MUST 实现 retry with jitter 应对多进程并发写。

#### Scenario: FTS5 search on message content

- **WHEN** 查询 `SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'python 错误' ORDER BY rank LIMIT 20`
- **THEN** 系统 MUST 返回相关 message id 列表，p95 延迟 < 5ms（10 万条量级）

#### Scenario: Concurrent write with retry

- **WHEN** 两个进程（backend + task worker）同时写 messages 表触发 SQLITE_BUSY
- **THEN** 系统 MUST 在应用层重试（最多 5 次，jitter exponential backoff），最终成功写入或返回明确错误

### Requirement: Vector Memory (L3) — sqlite-vec + BGE-M3

系统 SHALL 使用 `sqlite-vec` 扩展存储 1024 维 BGE-M3 INT8 embedding。每条 user / assistant message MUST 异步计算 embedding（批量 8 条或 2s flush）。embedding 失败 MUST NOT 阻塞 message 主写入。

#### Scenario: Async embedding write

- **WHEN** assistant message 写入 messages 表
- **THEN** 系统 MUST 将 message_id 丢进 embedding queue，worker 周期（每 2s 或每 8 条）批量计算 embedding 后写 `messages_vec` 表

#### Scenario: Vector search on 100K messages

- **WHEN** 数据库有 10 万条 messages，查询 `SELECT message_id, distance FROM messages_vec WHERE embedding MATCH ? ORDER BY distance LIMIT 20`
- **THEN** 返回 p95 < 20ms

### Requirement: Hybrid Retrieval with RRF

MemoryManager.recall MUST 融合向量召回 + FTS5 召回 + recency + salience 四维度，使用 Reciprocal Rank Fusion 重排。权重配置可调，默认 `{vec: 0.5, fts: 0.3, recency: 0.15, salience: 0.05}`。

#### Scenario: Semantic query matches non-literal match

- **WHEN** 用户问 "我上次讲脚上穿的那个", 历史中原始文本是 "我喜欢红色袜子"
- **THEN** recall MUST 通过 L3 向量层召回该条（语义相似），即使 FTS5 无字面匹配

#### Scenario: Recent items boosted

- **WHEN** 两条 message 向量相似度相同，一条 1 天前、一条 30 天前
- **THEN** recency 维度 MUST 给 1 天前的条目更高分，最终排名靠前

### Requirement: Memory Decay and Salience

每条 message MUST 有 `salience REAL DEFAULT 0.5` 和 `decay_last_touch REAL` 列。每次被 recall 返回 MUST 更新 `decay_last_touch=now()` 并 `salience += config.memory.salience_boost_on_recall`。长期未触碰的 message salience MUST 按 `decay_lambda` 衰减。

#### Scenario: Recall boosts salience

- **WHEN** 一条 message salience=0.5 被 recall 返回
- **THEN** 系统 MUST 更新 `salience=0.55, decay_last_touch=now()`（boost 默认 0.05）

#### Scenario: Daily decay on idle memory

- **WHEN** 一条 message 30 天未被 recall，`decay_lambda=0.02`
- **THEN** 衰减后 salience ≈ `0.5 * exp(-0.02 * 30) ≈ 0.27`

### Requirement: Schema Migration v8 → v9

系统 MUST 在启动时检测 `PRAGMA user_version` 并按需执行迁移。v8 → v9 迁移 MUST：(1) `.bak` 备份数据库文件；(2) `ALTER TABLE messages ADD COLUMN embedding BLOB, salience REAL DEFAULT 0.5, decay_last_touch REAL, user_emotion TEXT, audio_file_path TEXT`；(3) 创建 `messages_vec` 虚拟表；(4) 启动后台 backfill embedding task；(5) `PRAGMA user_version=9`。

#### Scenario: Successful migration on rc1 upgrade

- **WHEN** rc1 用户启动 v0.6.0-phase4，state.db 原为 v8
- **THEN** 系统 MUST 自动备份 `.bak`、ALTER TABLE、建 messages_vec、启动后台 backfill，用户对话体验不受影响

#### Scenario: Migration failure rollback

- **WHEN** ALTER TABLE 失败（磁盘满、权限等）
- **THEN** 系统 MUST 从 `.bak` 还原、log error、以降级模式启动（无 L3 层），通知前端显示错误卡
