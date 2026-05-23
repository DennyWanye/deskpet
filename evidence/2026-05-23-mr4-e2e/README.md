# MR-S2-6 / WI-V.1 — workspace memory 端到端联调 evidence

**关联**: `plans/2026-05-23-memory-system-stage2/02-manual-test-cases.md` §MR-S2-6
+ `plans/2026-05-23-memory-system-stage2/01-TDD.md` §A5

## 运行方式（主 checkout，需 venv + 真 LLM）

```powershell
# 1) 准备主 checkout backend/.venv（先装 torch / sqlite_vec 等）
cd G:\projects\deskpet
.\backend\.venv\Scripts\python.exe -m pip install -e backend

# 2) 启 backend（worktree 端口 8201 / 主 checkout 8100）
# 修改 config.toml 把 [memory.v2] workspace_memory 设 false 跑一次
$env:DESKPET_SHARED_SECRET="..."   # 同 backend 启动参数
.\backend\.venv\Scripts\python.exe -m scripts.e2e_workspace_memory --flag off --db-path "..."

# 3) 改 config.toml workspace_memory=true 重启 backend
.\backend\.venv\Scripts\python.exe -m scripts.e2e_workspace_memory --flag on --db-path "..."

# 4) compare 模式（提示用户中途切 flag）
.\backend\.venv\Scripts\python.exe -m scripts.e2e_workspace_memory --compare
```

## 产物

- `compare-report.json` — flag off vs on 的 file_read / workspace_recall 计数
- `single-{on|off}-{sid}.json` — 单次跑的 metric + workspace_state dump
- 录屏 / 截图 应另外用 OBS / windows-mcp 在跑的时候捕获

## DoD（PRD §4.6）

- ✅ 第二轮 agent prompt 中含工作记忆段（log 抓"workspace_recall"或 prompt dump）
- ✅ flag on 时 `file_read_count` ≤ flag off
- ✅ 证据归档到本目录

## 当前状态

- M5 实施清单完成（脚本 + evidence dir）
- 真实主 checkout 跑 + 录屏待用户/后续手测 round 介入（需 GUI + 真 LLM provider）
