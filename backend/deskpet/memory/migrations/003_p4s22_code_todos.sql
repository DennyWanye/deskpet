-- 003_p4s22_code_todos.sql — Code mode task tracker (P4-S22)
--
-- TodoWrite tool持久化任务列表的存储位置。每个 code session
-- (一个项目根目录对应一个 session) 维护自己的 todo list。
--
-- 设计要点：
--   * 一次 TodoWrite 调用替换整个列表 (idempotent semantics, 跟 Claude
--     Code 的 TodoWrite 一致)
--   * 不参与 BGE-M3 向量召回 (这些是工具状态, 不是对话内容)
--   * 不归档/不清理 — code session 数量不大, 即使长期使用也不会爆
--   * 前端通过 control WS `code_todo_update` 实时订阅
--
-- 删除整个 session 的 todo 列表用 DELETE WHERE session_id; 替换语义在
-- Python 侧实现 (DELETE + INSERT in single transaction)。

CREATE TABLE IF NOT EXISTS code_todos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    -- "Implement A; run B; verify C" — what the user sees as the task line
    content      TEXT NOT NULL,
    -- "Implementing A; running B; verifying C" — present-continuous form
    -- shown while the task is currently in_progress (mirrors Claude Code)
    active_form  TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (
        status IN ('pending', 'in_progress', 'completed')
    ),
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   REAL    NOT NULL DEFAULT (julianday('now')),
    updated_at   REAL    NOT NULL DEFAULT (julianday('now'))
);

-- Per-session ordered fetch is the only access pattern — covers the
-- common "render this session's todos" + "replace all for session" ops.
CREATE INDEX IF NOT EXISTS idx_code_todos_session
    ON code_todos(session_id, sort_order);

PRAGMA user_version = 11;
