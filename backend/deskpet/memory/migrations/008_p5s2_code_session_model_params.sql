-- 008_p5s2_code_session_model_params.sql — code-session-model-params
--
-- 在 007 的 code_session_provider 上加 model_params(JSON) 列，承载
-- Cursor 式每会话模型参数：{"thinking":bool,"fast":bool,
-- "context":"300k|1m","effort":"low|medium|high|extra_high|max"}。
--
-- 设计依据：
--   openspec/changes/code-session-model-params/specs/code-session-model-params/spec.md
--     Requirement "Per-code-session model+params binding is persisted"
--
-- 字段语义：
--   model_params — NULL/缺省 表示"用 provider 默认参数"(向后兼容 007
--                  既有行：升级后旧行该列为 NULL → resolution 走默认)。
--
-- 幂等：migrator 用 schema_migrations 记录已应用文件，本 ALTER 只跑一次
-- (SQLite ALTER ADD COLUMN 重跑会报错，但 migrator 保证不重跑)。

ALTER TABLE code_session_provider ADD COLUMN model_params TEXT;

PRAGMA user_version = 16;
