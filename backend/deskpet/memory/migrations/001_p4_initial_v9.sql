-- 001_p4_initial_v9.sql — P4 Poseidon initial schema (user_version=9)
--
-- DeskPet clean-room rewrite. 没有真实的 v8→v9 增量：初版桌宠直接
-- 以 v9 schema 起手。这里一次性建齐 sessions / messages / FTS5 索引，
-- 并在末尾把 user_version 标记成 9，让启动守门知道"库已经是目标版本"。
--
-- 设计依据：
--   openspec/changes/p4-poseidon-agent-harness/specs/memory-system/spec.md
--     Requirement: "Session Database (L2) — SQLite with FTS5"
--     Requirement: "Schema Migration v8 → v9"
--   design.md: D-ARCH-1 三层记忆、D-MIGRATE-1 schema 升级、R12 迁移失败回滚
--
-- 不在本 SQL 里做的事（Python 侧做）：
--   * messages_vec 虚拟表（sqlite-vec vec0）——需要运行时 load_extension，
--     放在 SessionDB.initialize() 里，允许 sqlite-vec 缺失时降级启动。
--   * embedding backfill —— P4-S2 (embedder.py) 的异步任务。

-- ---------------------------------------------------------------------
-- schema_migrations：记录已应用的迁移文件名，避免重复执行。
-- 结构与 P3 backend/memory/migrator.py 一致；独立表但约定相同。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at REAL NOT NULL
);

-- ---------------------------------------------------------------------
-- sessions：每个 agent 会话一条记录。Hermes AIAgent 每次 run_conversation
-- 前都需要一个 session_id；DeskPet 单用户，不设 tenant/user 字段。
--   id        UUID 字符串，Python 侧 uuid.uuid4() 生成，便于跨进程传递
--   metadata  可选 JSON（未来存"启动来源 / locale / agent role"等）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    metadata   TEXT
);

-- ---------------------------------------------------------------------
-- messages：agent 对话历史主表。
--
-- 基础列（会话检索所需）：
--   id            自增主键，同时作为 FTS5 / vec 的外键
--   session_id    归属 session
--   role          'user' | 'assistant' | 'system' | 'tool'
--   content       纯文本正文，FTS5 同步对象
--   created_at    UNIX 时间戳（float），用 ORDER BY 做 recency 排序
--
-- P4 新增列（见 spec "Schema Migration v8 → v9"）：
--   embedding         BLOB，sqlite-vec INT8 1024 维，由 P4-S2 异步回填
--   salience          REAL，重要性分，默认 0.5，recall 命中 +0.05
--   decay_last_touch  REAL，上次被 recall 的时间，用于 decay 衰减公式
--   user_emotion      TEXT，情感标签（P4-S4 之后的扩展）
--   audio_file_path   TEXT，若该 message 伴随 TTS/ASR 音频则指向文件
--
-- Tool-calling 列（Hermes 风格）：
--   tool_call_id      role='tool' 时回指 assistant 的 tool_calls 中的 id
--   tool_calls        role='assistant' 时保存 JSON array（OpenAI 格式）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT    NOT NULL,
    role             TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    created_at       REAL    NOT NULL,
    embedding        BLOB,
    salience         REAL    DEFAULT 0.5,
    decay_last_touch REAL,
    user_emotion     TEXT,
    audio_file_path  TEXT,
    tool_call_id     TEXT,
    tool_calls       TEXT
);

-- idx_messages_session_time：支持 "该 session 最近 N 条" 这类最常见查询。
CREATE INDEX IF NOT EXISTS idx_messages_session_time
    ON messages(session_id, created_at);

-- idx_messages_salience：daily decay 任务 + salience-boost recall 要扫低分项。
CREATE INDEX IF NOT EXISTS idx_messages_salience
    ON messages(salience);

-- ---------------------------------------------------------------------
-- messages_fts：FTS5 外部 content 虚拟表。
--   * content='messages'，content_rowid='id' → 不占双份存储，rowid 直接等于
--     messages.id，联表无损。
--   * tokenizer 选 'trigram'（SQLite 3.34+）：
--       对 CJK 最友好的内建 tokenizer——把文本切成连续 3 字符的 n-gram，
--       任意 ≥3 字符的子串都能 MATCH 命中，中英文混排都适用。
--       unicode61 对中文是按"整块连续 CJK 字符"切 token，子串无法命中，
--       对 agent recall 场景不够用（参考 temp/test_fts5_tokenizer.py 的探测）。
--       trade-off：≤2 字符的查询 MATCH 不到——但对话场景下不算痛点；
--       更精细的中文分词（jieba-sqlite）留到 P5 再说。
-- ---------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='id',
    tokenize='trigram'
);

-- ---------------------------------------------------------------------
-- FTS5 同步触发器（external content 模式下必须手动维护）：
--   messages_ai: after insert — 新 message 自动进 FTS 索引
--   messages_ad: after delete — 同步从 FTS 删除（delete 伪行）
--   messages_au: after update — 先 delete 再 insert 覆盖
--
-- 参考 SQLite FTS5 官方文档 "External content tables"。
-- ---------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

-- ---------------------------------------------------------------------
-- 标记库版本 = 9。下一次启动时 ensure_v9() 读到 9 就认为"就位"，跳过迁移。
-- ---------------------------------------------------------------------
PRAGMA user_version = 9;
