-- 004_p4s24_reasoning_content.sql — Thinking-mode reasoning round-trip (P4-S24)
--
-- Some OpenAI-compatible providers (DeepSeek V4 Pro, Qwen3 thinking,
-- GLM-4.5, OpenAI o-series proxies through your-llm-relay.example.com etc.) return a
-- separate `reasoning_content` field on assistant messages — the
-- chain-of-thought trace the model produces before its visible answer.
--
-- These APIs enforce "round-trip" of reasoning_content: the next request
-- in the same conversation MUST include the prior assistant message's
-- reasoning_content verbatim, or they reject with HTTP 400:
--
--     {"error": {
--        "message": "The `reasoning_content` in the thinking mode must
--                    be passed back to the API.",
--        "code": "invalid_request_error"}}
--
-- DeskPet observed this against your-llm-relay.example.com/deepseek-v4-pro on 2026-05-08:
-- multi-turn chats failed on the second user message because the history
-- rebuilt from SessionDB lost reasoning_content. We add a column so
-- assistant rows can store it; non-thinking providers (Ollama gemma,
-- plain OpenAI GPT-4o without o-series) leave it empty/NULL — the
-- round-trip code path skips it then so payloads stay clean.
--
-- Forward-compat: column is NULL-able and additive; old rows stay
-- valid, old code paths that don't read it just see no value.

ALTER TABLE messages ADD COLUMN reasoning_content TEXT;

PRAGMA user_version = 12;
