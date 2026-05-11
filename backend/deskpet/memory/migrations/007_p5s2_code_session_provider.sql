-- 007_p5s2_code_session_provider.sql — P5-S2 multi-provider-management
--
-- 引入 code 模式下"每会话可绑定 provider/model"的持久化能力。companion
-- 模式永远用全局 chain，不写这张表。
--
-- 设计依据：
--   openspec/changes/multi-provider-management/specs/code-session-provider-binding/spec.md
--     Requirement "SessionDB persists per-session provider override"
--     Requirement "SessionDB migration adds the table"
--
-- 字段语义：
--   base_session_id — code 模式的 base session id（PK）；companion sid
--                     永远不写这张表
--   provider_id     — NULL 表示"用全局 chain"；非 NULL 表示"只用这个
--                     provider，无 fallback"
--   preferred_model — NULL 表示"用 provider 默认 model"；非 NULL 表示
--                     "无论选哪个 provider，都用这个 model"
--   updated_at      — julianday，便于排序最近改动的会话
--
-- 注意：本迁移**不**做引用完整性约束（没有 FK 到 sessions 表）—— 因为
--   code 会话生命周期由前端管理，base_session_id 在 SessionDB 里不一定
--   有对应 sessions 行。下线清理由应用层做（删 code session 时同步删
--   这条 binding）。

CREATE TABLE IF NOT EXISTS code_session_provider (
    base_session_id TEXT PRIMARY KEY,
    provider_id     TEXT,
    preferred_model TEXT,
    updated_at      REAL NOT NULL DEFAULT (julianday('now'))
);

PRAGMA user_version = 15;
