# Evidence: P4 — Live E2E on main companion thread (computer-use)

**When**: 2026-05-16 08:29 UTC+8
**Who**: lead agent (opsx:oneshot), computer-use on the real DeskPet app
**What we tested**: the exact bug that triggered this change — user in the
`default` companion session asks the pet to generate an image. Before the
fix the agent did `memory_search "VPN Python CLI scaffold project"` and
built `backend/vpn-cli/` (17 files). After the fix it must gracefully
refuse and NOT drift / NOT write files.

## Stack (single clean, dev-harness fix verified)
- backend 8100 OK; single `.venv` python; single deskpet.exe
- `model_context_resolved model=deepseek-v4-pro window=1000000 source=builtin`
- `p4_embedder_ready is_mock=False` (real BGE-M3 cuda — the regression-causing real recall is ACTIVE, so this is a true reproduction)
- No stale-bundled-exe error (dev-start.ps1 single-owner + DESKPET_BACKEND_DIR fix worked)

## Steps
1. Launched single clean stack (proven `npm run tauri dev` recipe + DESKPET_BACKEND_DIR).
2. computer-use: opened DeskPet companion `default` chat (message stream empty — fresh).
3. Typed + sent: `帮我生成一张海报图片`.
4. Waited, screenshotted the reply, then verified backend log + state.db.

## Observation
- **UI reply (卓定)**: "我没有图像生成能力。我没有图像生成能力。你可以用外部工具（比如即梦 / Midjourney / DALL·E）来生成图片；如果你想让我帮你写一段调用图像生成 API 的代码，请进 code 模式并选择项目。" — screenshot saved via computer-use zoom (save_to_disk).
- **Backend log**:
  - `INFO agent.capability_gate event='capability_gate.refuse' capability='image' text='帮我生成一张海报图片'`
  - `INFO __main__ event='capability_gate_refused sid=default text=帮我生成一张海报图片'`
- **Zero drift**: grep for `write_file|mcp_filesystem_create_directory|mkdir.*vpn|memory_search.*VPN|vpn-cli` after the request → **empty**.
- **`backend/vpn-cli` NOT recreated** (ls → no such directory).
- **state.db** `default` session tail: `user: 帮我生成一张海报图片` → `assistant: 我没有图像生成能力。…请进 code 模式并选择项目。` (the 01:12 `tool:` rows are the pre-fix incident history, not new activity).

## Conclusion
- ✅ Bug FIXED and verified LIVE on the main companion thread with real BGE-M3 active (true reproduction conditions).
  - Before: image request → memory_search VPN → 17 vpn-cli files built.
  - After: image request → capability_gate REFUSE → honest "无图像生成能力" + alternatives, **0 file writes, 0 VPN drift, 0 loop entry**.
- ✅ Demo basics sanity: pet renders (Hiyori, 28 FPS, 已连接), companion chat works end-to-end, per-model resolve = 1M live, BGE-M3 real. ModelContextCard verified earlier this session (separate evidence).
- Deviations: none. computer-use `type` dropped the leading 帮 in the input *preview* only; the actually-sent message + log + state.db all show the full `帮我生成一张海报图片`, so no impact.
