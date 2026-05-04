# DeskPet Skill Platform v1 — real-test evidence

Every Stage of the P4-S20 skill-platform-v1 change must include
real-test evidence per [`feedback_real_test.md`](../../C:/Users/24378/.claude/projects/G--projects-deskpet/memory/MEMORY.md).
This file is the rolling log of those proofs.

---

## Stage A — tool_use loop + 7 OS tools + permission gate

**Status:** ✅ End-to-end live demo PASS (2026-05-04)

### Wave 2c — live LLM E2E

Script: [`backend/scripts/e2e_stage_a_full.py`](../../backend/scripts/e2e_stage_a_full.py)

What it exercises:
- **Live** local Ollama LLM (`gemma4:e4b` via `http://localhost:11434/v1`)
- `OpenAICompatibleProvider.chat_with_tools()` (P4-S20 new method)
- `OpenAICompatibleAgentLLM` shim → `AgentLoop`
- `ToolRegistry` v2 with the 7 OS tools registered
- `PermissionGate` with the auto-approve test responder
- Real `desktop_create_file` handler writing UTF-8 bytes
- Hermetic `$USERPROFILE/Desktop` (no actual user-desktop pollution)

Evidence (single run on the dev machine):

```
[e2e-full] hermetic Desktop: C:\Users\...\Temp\deskpet_e2e_full_px8wcm8u\fakeuser\Desktop
[e2e-full] provider: http://localhost:11434/v1 model= gemma4:e4b
[e2e-full] tool_call iter=1 desktop_create_file {'content': '吃饭买菜', 'name': 'todo.txt'}
[e2e-full] popup: category=desktop_write summary='Write to (4 bytes)' -> ALLOW
[e2e-full] tool_result iter=1 desktop_create_file {"ok": true, "result": ...}
[e2e-full] FINAL:  iters=2
[e2e-full] PASS: live LLM -> tool_use -> permission -> file written
[e2e-full] artifact: C:\Users\...\Temp\deskpet_e2e_full_px8wcm8u\fakeuser\Desktop\todo.txt (12 bytes)
```

Verified properties:
1. ✅ Live LLM emits OpenAI `tool_calls` (not regex `<tool>` fallback)
2. ✅ AgentLoop dispatches concurrent tool_calls via `execute_tool`
3. ✅ PermissionGate fires `permission_request` exactly once with
   `category=desktop_write`
4. ✅ `desktop_create_file` writes 12 UTF-8 bytes (4 Chinese chars)
5. ✅ Loop runs 2 turns: tool call → tool result → final assistant message
6. ✅ Hermetic — no real desktop modified

### Backend tests

```
backend/tests/
├── test_p4s20_permission_gate.py        10 PASS
├── test_p4s20_tool_registry_v2.py       13 PASS
├── test_p4s20_os_tools.py               17 PASS
├── test_p4s20_agent_loop_tool_use.py     4 PASS
└── test_p4s20_chat_with_tools.py         3 PASS

P4-S20 subtotal:   47 PASS
Baseline:          693 PASS
Total:             740 PASS, 1 skipped, 0 failed
```

### Commits

```
cc915b2 feat(p4-s20 wave 2): chat_v2 IPC + tool-use shim + Stage A E2E smoke
32b26b8 feat(p4-s20 wave 1c): frontend PermissionPopup + control-WS IPC wiring
42f1cbe feat(p4-s20 wave 1b): agent loop routes through ToolRegistry.execute_tool
4a1a18d feat(p4-s20 wave 1a): 7 OS tools — read/write/edit/list/shell/web/desktop_create
06f9343 feat(p4-s20 wave 0): foundation contracts — shared types + PermissionGate + ToolRegistry v2
5f23aa0 docs(p4-s20): OpenSpec proposal — deskpet-skill-platform v1
```

### TODO before declaring Stage A done

- [ ] UI screenshot evidence (Tauri shell driving `chat_v2` IPC,
      PermissionPopup visible). Deferred to Wave 6 once the chat panel
      grows a "use new agent loop" toggle. The plumbing is verified
      via the script above.

---

## Stage B — SKILL.md parser + dual loader

(pending)

## Stage C — Marketplace UI + safety

(pending)

## Stage D — Plugin system

(pending)
