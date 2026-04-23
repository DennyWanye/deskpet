# HANDOFF — P3-S6 + P3-S7 (用户数据目录 + 模型外置)

**Date**: 2026-04-23
**Branch**: `p3-s6-s7-user-data-dirs`
**Status**: smoke PASS；待用户 UI E2E 后 merge 到 master

---

## 1. 一句话

把模型从 "installer 内置" 换成 "`%LocalAppData%\deskpet\models\` 外置"，把用户数据
（config/DB/logs）从 "仓库相对路径" 换成 "`%AppData%\deskpet\`"——一次性覆盖
原 roadmap 的 P3-S6（模型）+ P3-S7（AppData）两个 slice。

## 2. 背景（critical pivot）

原 P3-S6 roadmap 写着 **"模型全塞进 installer"**。P3-S5 合并后实测模型尺寸：

- `backend/models/faster-whisper-large-v3-turbo` — 2.7 GB
- `backend/models/cosyvoice2` — 5.3 GB
- **合计 ≈ 9 GB**

而 P3-G2 体积门 = **3.5 GB**，硬超 2.5 倍。方案调整：

| 之前设想 | 现在 |
|---|---|
| installer 全塞 2.5-3.5 GB 含模型 | installer 只装 1.5 GB 运行时 |
| 双击即用（含模型） | 双击启动，模型按需放到 `%LocalAppData%` |
| D3-0b = A | D3-0b 修订为 B'：installer 外模型（首启下载器留给 Phase 4） |

现阶段 dev / 灰度用户走 `scripts/setup_user_data.ps1` 一键 junction 仓库模型目录；
installer 首启 GUI 下载器是 Phase 4 的事。

## 3. 改动清单

### 3.1 `backend/paths.py`
新增：`user_data_dir()`（roaming AppData）、`user_cache_dir()`、`user_models_dir()`
（LocalAppData，非 roaming）、`user_log_dir()`、`ensure_user_dirs()`（mkdir -p）。
`model_root()` 解析优先级变为 env → `user_models_dir()`（若存在） → `_MEIPASS` →
dev `backend/models/`。每个 user 目录都接受对应 `DESKPET_USER_*` env var 覆盖。

### 3.2 `backend/config.py`
- 新增 `resolve_config_path()`：env (`DESKPET_CONFIG`) → `<user_data>/config.toml`
  → bundle 默认。
- 新增 `seed_user_config_if_missing()`：首启把 bundle 默认 config.toml 拷到
  `%AppData%\deskpet\config.toml`，已存在则 no-op。
- 新增 `_resolve_memory_db_path(raw)`：空串 / 相对路径 → anchor 到
  `user_data_dir()/data/`；绝对路径原样。
- `MemoryConfig.db_path` 默认从 `"./data/memory.db"` 改为 `""`（auto）。
- `load_config()` 末尾总是走 `_resolve_memory_db_path` + 同步 `billing.db_path` 到
  同目录——哪怕没有 `[memory]` 节也落到 AppData，不再 CWD-relative。
- `load_config(missing_path)` 也会返回默认 AppConfig() 但 db_path 已 resolve。

### 3.3 `backend/main.py`
删掉本地 `_find_config_toml()`，改用 `config.resolve_config_path()`；启动首屏调
`paths.ensure_user_dirs()`；`config_loaded` 日志多带 `user_data_dir` /
`user_models_dir` / `model_root` 三字段。

### 3.4 `backend/pyproject.toml`
加 `platformdirs>=4.0`（纯 Python 21KB）。

### 3.5 `backend/deskpet-backend.spec`
`hiddenimports += ["platformdirs"]`（防 PyInstaller 漏挖）。

### 3.6 `config.toml`（仓库根 = bundle 默认）
`[memory].db_path = ""` + 注释说明空串 = AppData 自动路径。

### 3.7 `scripts/setup_user_data.ps1`（新）
幂等 provisioner：(1) mkdir AppData/LocalAppData 目录树；(2) 首次把 repo
`config.toml` 拷到 `%AppData%`；(3) 若 `%LocalAppData%\deskpet\models\` 为空且
repo 有 `backend/models/`，`mklink /J` junction。

### 3.8 `scripts/e2e_frozen_tauri.ps1` + `scripts/smoke_frozen_backend.py`
不再注入 `DESKPET_MODEL_ROOT` / `DESKPET_CONFIG`。两者先调 `setup_user_data.ps1`
或检查 `%LocalAppData%\deskpet\models\`，backend 自己从 AppData 解析。

### 3.9 测试
- `backend/tests/test_paths.py` — 16 条测试（原 6 条 + 10 条新增），覆盖
  `user_data_dir` / `user_cache_dir` / `user_models_dir` / `ensure_user_dirs`，
  用 `clean_env` fixture 避免 dev 机 LocalAppData 污染。
- `backend/tests/test_config_resolution.py`（新）— 15 条测试，覆盖
  `resolve_config_path` 三级优先、`seed_user_config_if_missing` 幂等、
  `load_config` 空串 / 相对 / 绝对 db_path 分别的行为。

### 3.10 `docs/PACKAGING.md`
加 §6b "用户数据目录 (P3-S6 + P3-S7)"：目录约定 + 解析优先级 + 首启流程 +
dev `setup_user_data.ps1` 使用说明 + Phase 4 TODO。

## 4. 验收结果

| 验收点 | 结果 |
|---|---|
| 全量 pytest | 322 passed, 4 skipped ✅ |
| `setup_user_data.ps1` 幂等 | 多次运行无报错 ✅ |
| frozen backend rebuild | 73.6s，产物 **1524.7 MB**（P3-G2 预算 3.5 GB，远低于）✅ |
| `smoke_frozen_backend.py` | boot **2.9s**，/health ok，startup_errors=[] ✅ |
| 日志显示 AppData 路径 | `config_loaded` 打印 `path=...\AppData\Roaming\deskpet\config.toml` ✅ |
| billing.db 落在 AppData | `billing_ledger_ready db_path=...\AppData\Roaming\deskpet\data\billing.db` ✅ |
| ASR 走 CUDA float16 | `faster-whisper loaded device='cuda' compute_type='float16'` ✅ |
| UI 级 E2E（麦、ASR、TTS） | ⏳ **待用户本地跑** `scripts/e2e_frozen_tauri.ps1` 后手测 |

## 5. 用户待办

先跑一次（若 `%LocalAppData%\deskpet\models\` 已存在可跳过）：

```powershell
powershell scripts/setup_user_data.ps1
```

然后：

```powershell
powershell scripts/e2e_frozen_tauri.ps1
```

Tauri 窗口打开后：
1. 窗口是否出现；
2. 点麦、说一句话（例如"你好"），验证 ASR 识别 + AI 回复 + TTS 播放；
3. 截图窗口并贴回 session。

确认无误即 merge 此 branch 到 master。

## 6. 不做 / 推迟

- ❌ 首启 GUI 下载器 — Phase 4 或 P3 bonus slice
- ❌ logrotate — 另开 slice
- ❌ macOS/Linux 实测 — platformdirs 天然支持，靠单测覆盖，跨 OS 手测推迟
- ❌ WiX 卸载清理 — 留给 P3-S9

## 7. 合并顺序

smoke PASS 已满足本地技术验收。用户 UI E2E 通过后：

```bash
git checkout master
git merge --no-ff p3-s6-s7-user-data-dirs
git push origin master
git worktree remove .claude/worktrees/p3-s6-s7-user-data-dirs
```
