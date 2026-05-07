-- 002_p4s20_summarize_archive.sql — 自动总结 + 归档老对话 (P4-S20-D)
--
-- 给 messages 表加两个字段：
--   is_summary       BOOL  — 1 表示这条本身是 LLM 总结
--   summary_of       TEXT  — JSON 数组，列出被总结的原 message id (仅 is_summary=1 时有意义)
--
-- 加一张 messages_archive 表保留原文：summary 入主表后，原文被搬到这里。
-- archive 里的内容**不参与**召回 (Retriever 不查这张表)，但 IPC 可查可
-- 恢复，用户能看见真历史。
--
-- 设计依据：
--   docs/EVIDENCE/skill-platform-v1.md (P4-S20)
--   spec "Memory hierarchy: short -> long -> archived"
--
-- 不在本 SQL 里做的事 (Python 侧做)：
--   * summary 的向量化 — vector_worker.enqueue 把 summary 也 embed 进
--     messages_vec, 让召回能命中 (类似 4881 条原文)
--   * 触发条件 + LLM 调用 — summarizer.py 模块负责

-- ---------------------------------------------------------------------
-- 给 messages 表加 summary 标记
-- SQLite ALTER TABLE ADD COLUMN 是 idempotent-friendly 的, 但 IF NOT
-- EXISTS 不支持, 所以用兼容写法 (重复跑会失败，但 schema_migrations
-- 表确保只跑一次)。
-- ---------------------------------------------------------------------
ALTER TABLE messages ADD COLUMN is_summary INTEGER DEFAULT 0;
ALTER TABLE messages ADD COLUMN summary_of TEXT;

-- 索引: 让 summarizer 快速找出"哪些 session 已总结过"
CREATE INDEX IF NOT EXISTS idx_messages_is_summary ON messages(is_summary);

-- ---------------------------------------------------------------------
-- messages_archive: 被搬走的原文。Schema 同 messages 主体, 多两个字段:
--   archived_at        REAL    NOT NULL    — 归档时间
--   archived_into_id   INTEGER             — 关联的 summary message id
-- 不建 FTS5 / vec 索引 — archive 不参与召回, 节省空间 + 加速归档
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages_archive (
    -- 原 messages 列
    id               INTEGER PRIMARY KEY,
    session_id       TEXT    NOT NULL,
    role             TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    created_at       REAL    NOT NULL,
    embedding        BLOB,
    salience         REAL,
    decay_last_touch REAL,
    user_emotion     TEXT,
    audio_file_path  TEXT,
    tool_call_id     TEXT,
    tool_calls       TEXT,
    -- archive 元信息
    archived_at      REAL    NOT NULL,
    archived_into_id INTEGER             -- summary message id, NULL 表示用户主动归档无 summary
);

CREATE INDEX IF NOT EXISTS idx_archive_session_time
    ON messages_archive(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_archive_into_id
    ON messages_archive(archived_into_id);

-- ---------------------------------------------------------------------
-- 标记 schema 版本到 10
-- ---------------------------------------------------------------------
PRAGMA user_version = 10;
