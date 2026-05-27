# Evidence: Phase 1 — Live E2E PENDING (deferred, not skipped)

**When**: 2026-05-15 ~17:20 UTC+8
**Who**: lead agent (opsx:oneshot)
**Status**: Unit + integration verification DONE & GREEN. Live UI-level E2E DEFERRED to next backend restart (not run now to avoid killing the user's active 小说网站 / web-researcher agent sessions on the running backend).

## What IS verified (green, merged to master)

- Backend full suite: **1296 passed, 10 skipped, 0 failures** (`.venv python -m pytest tests/`)
- Frontend: **tsc EXIT=0**, **vitest 120 passed / 10 files**
- 58 new tests covering every `#### Scenario` in `specs/per-model-context/spec.md` + the file-read-dedup requirement in `specs/long-run-context/spec.md` + 16 prompt-cache tests
- 8 pre-existing baseline RED tests (test_p6_context_manager/test_p6_integration, broken by the db20b25 stop-gap) fixed to v2 semantics + v1 rollback coverage added
- master commits: db20b25 → f9938d1 (P1.1+1.2) → 379bd70 (P1.3) → merges fbbe19a, 7245c64

## What is PENDING (must run after next backend restart — NOT a skip)

The running backend (PID ~15372) still has pre-Phase-1 code. Restarting now interrupts the user's live agent work. The following live checks MUST be done on the next natural restart (or when user authorizes a disruptive restart):

### Checklist

1. **per-model resolve** — start backend, grep `logs/backend.log` for `model_context_resolved model=deepseek-v4-pro window=… source=builtin`. Expect window=1_000_000 (or global/project override if set), source logged.
2. **per-model switch** — in a code-mode session, switch model deepseek↔claude-sonnet; confirm resolved window changes (1M↔200K) with NO config.toml edit.
3. **project override** — drop `<a project>/.deskpet/context.toml` with `[models."deepseek-v4-pro"] context_window=1000000`; open that project in code mode; confirm log shows `source=project`.
4. **file-read dedup** — re-run the read-loop task that 50-轮爆 today (sid pattern code-rkjdd9vo, "扫描+改 5 文件"); confirm same-file re-reads get superseded markers in history and the task converges in ≤30 iterations (vs 50 before). Screenshot the code panel.
5. **prompt cache** — 5-turn conversation; grep log for `p4s25_prompt_cache_hit cached_tokens=…`; expect hit-rate ≥50% on turns 2-5 (the relay/deepseek prefix cache).
6. **ModelContextCard UI** — open SettingsPanel, confirm the 模型上下文 card renders resolved window + source chain (screenshot = evidence per [feedback_real_test]).
7. **rollback** — set `config.toml [context.manager].v2_enabled=false`, restart, confirm legacy absolute thresholds (4000/60000/20) restored.

## Why deferred, not skipped

Per [feedback_real_test] every slice handoff needs real UI-level E2E + screenshot — this note explicitly does NOT claim Phase 1 "done & verified end-to-end". It claims: **implemented + unit/integration-green + merged**, with live E2E as a named, concrete, must-do follow-up gated on a non-disruptive restart window. `--no-archive` was passed, so the change correctly stays in active state until all 3 phases (incl. this E2E) land.
