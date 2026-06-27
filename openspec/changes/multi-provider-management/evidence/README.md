# Evidence — multi-provider-management

每完成一个 phase（pytest/vitest 全绿）就在这里放一份 evidence 文档证明跑过。

格式：`<phase-or-task-id>-<slug>.md`

每份至少包含：
1. **场景** — 测了哪个 case（plain English）
2. **操作步骤** — 别人能重现的 step-by-step
3. **抓到的 log/截图** — 关键日志行（脱敏 secret）+ 截图
4. **结论** — ✅ / ❌ 相对预期是否通过

无 evidence 的 phase 不算闭环，不能划掉 tasks.md 里的 phase 验收 task。

## Phase → evidence 文件映射

- Phase 0 SessionDB → `0.8-db-tests.md`
- Phase 1 Registry → `1.18-registry-tests.md`
- Phase 2 IPC → `2.14-ipc-tests.md`
- Phase 3 AgentLoop chain → `3.16-chain-tests.md`
- Phase 4 Settings UI → `4.15-settings-ui.md`
- Phase 5 Code Panel UI → `5.13-code-panel-ui.md`
- Phase 6 Live E2E → `6.X-live-e2e.md`（多个截图）
- Phase 7 Archive → 不需 evidence，archive 即收尾
