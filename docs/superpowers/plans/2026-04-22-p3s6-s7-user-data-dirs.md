# P3-S6 + P3-S7 — 用户数据目录 & 模型外置

**Date**: 2026-04-22
**Scope**: 把 P3-S6（模型纳入 bundle）与 P3-S7（AppData 迁移）合并为单个 slice 交付。
**Depends on**: P3-S5 (3e31a89) 已合并。
**Status**: IN PROGRESS

---

## 0. 背景与转向（critical pivot）

原 roadmap P3-S6 设想 **"模型全塞进 installer"**（D3-0b = A）。
P3-S5 合并后实测体积：

```
backend/models/faster-whisper-large-v3-turbo   2.7 GB
backend/models/cosyvoice2                       5.3 GB
                                         合计 ≈ 8.0 GB
```

而 **P3-G2 体积门 = 3.5 GB**，硬超 2.3×。即使压缩也不可能塞进 installer。
所以必须放弃"一包全塞"，改为 **installer 只装 ~1.5 GB 运行时 + 模型放用户目录按需下载**。

这跟原 roadmap 的 D3-0b = A 冲突，但 roadmap 成文时没算过 cosyvoice2 的真实体积。
此 slice **技术决策变更**：D3-0b 修订为 B'（**首启由外部脚本/下载器放置模型**，
UI 仍保持"开箱即用"感——前提是 dev 环境已有模型，junction 到 user_data_dir）。

产品影响：v1.0.0-ga 之前的 installer 暂不含模型下载 UX，依赖 `scripts/setup_user_data.ps1`
把已有仓库的 `backend/models/` junction 到 `%LocalAppData%\deskpet\models\`。
真正的 "下载器首启 UX" 推到 Phase 4（或 P3 的 bonus slice），不阻塞 S7 交付。

---

## 1. 目录布局（本 slice 锁定）

| 路径 | 类型 | 内容 | 生命周期 |
|---|---|---|---|
| `%AppData%\deskpet\config.toml` | roaming | 用户配置（覆盖 bundle 默认值） | 卸载保留 |
| `%AppData%\deskpet\data\memory.db` | roaming | 对话记忆 SQLite | 卸载保留 |
| `%AppData%\deskpet\data\billing.db` | roaming | BillingLedger SQLite | 卸载保留 |
| `%AppData%\deskpet\logs\` | roaming | 结构化日志 | 卸载保留（logrotate 另议） |
| `%LocalAppData%\deskpet\models\faster-whisper-large-v3-turbo\` | non-roaming | ASR checkpoint | 卸载**可选**清理 |
| `%LocalAppData%\deskpet\models\cosyvoice2\` | non-roaming | TTS checkpoint | 卸载**可选**清理 |
| `%LocalAppData%\deskpet\cache\` | non-roaming | HF cache / 临时文件 | 随时可删 |

roaming vs local 划分遵循 Windows 惯例：**用户写的数据** → roaming（跟着账号走），
**体积大 / 可重新获取** → local（不上传漫游）。`platformdirs` 会给我们正确的
AppData/LocalAppData 路径，跨 OS 自动切到 XDG。

---

## 2. 解析优先级（两者都沿用同一 3-tier 套路）

### 2.1 模型根目录（`paths.model_root()` 扩展）

1. `$DESKPET_MODEL_ROOT` 环境变量（CI/debug/E2E 脚本用）
2. `%LocalAppData%\deskpet\models\`（**新：生产路径**）
3. `sys._MEIPASS/models/`（冻结 bundle 内置——保留，但 S6 后通常为空）
4. 开发回退：`backend/models/`（相对此文件）

> 为什么 LocalAppData 优先于 _MEIPASS？因为 S6 之后 bundle 通常**不再**打包大模型，
> 用户目录里的才是"官方"权重。保留 _MEIPASS 通道是给小模型（silero_vad 之类，
> 由 pip 包自带）留后路，不让回退链断。

### 2.2 用户数据根目录（新：`paths.user_data_dir()` / `paths.user_cache_dir()`）

1. `$DESKPET_USER_DATA_DIR` / `$DESKPET_USER_CACHE_DIR`（env 覆盖）
2. `platformdirs.user_data_dir("deskpet", appauthor=False, roaming=True)`
3. `platformdirs.user_cache_dir("deskpet", appauthor=False)`

在 frozen 模式下强制使用 platformdirs；dev 模式默认也走 platformdirs，但通过
env var 可以指到仓库根 `./data/` 便于老工作流。

### 2.3 config.toml 解析（`_find_config_toml()` 升级）

1. `$DESKPET_CONFIG`（E2E 脚本显式注入）
2. `%AppData%\deskpet\config.toml`（**新：用户配置**）
3. bundle 默认（frozen: `<exe_dir>/config.toml`；dev: 仓库根 `config.toml`）

**首启 seeding**：若 `%AppData%\deskpet\config.toml` 不存在，从 bundle 默认**拷贝**
一份过去——之后用户编辑 AppData 副本，升级 installer 不覆盖。

---

## 3. 代码改动清单

### 3.1 `backend/paths.py`
- 新增 `user_data_dir()`、`user_cache_dir()`、`user_log_dir()`
- 扩展 `model_root()`：在 `_MEIPASS` 之前插入 `user_data_dir() / "models"` 检查
- 新增 `ensure_user_dirs()`：`mkdir(parents=True, exist_ok=True)` 一组标准子目录

### 3.2 `backend/config.py`
- `MemoryConfig.db_path` 默认改为 `str(user_data_dir() / "data" / "memory.db")`（lazy）
- `BillingConfig.from_toml(data, db_dir)` 保持签名，**调用方**传入 user_data_dir
- 新增 `_find_config_toml()`（从 `main.py` 提上来，集中化）
- 新增 `seed_user_config_if_missing(bundle_default: Path, user_target: Path)`
- `load_config()` 开头：若显式 path 不存在，检查 AppData 副本；都不在就 seed

### 3.3 `backend/main.py`
- 启动最顶端调用 `paths.ensure_user_dirs()`
- 删除 `_find_config_toml()`（迁到 config.py）
- 使用新 `config.py::resolve_config_path()`
- Memory/Billing 构造时，`db_path` 走相对路径的继续允许（legacy），但
  默认通过 AppData 解析

### 3.4 `backend/memory/conversation.py` / billing
- 无改动——它们已经 `mkdir(parents=True, exist_ok=True)` 容忍任意路径

### 3.5 `config.toml`（仓库根，作为 bundle 默认）
- `memory.db_path` 从 `./data/memory.db` 改为注释 + 空串（让解析器走 AppData 默认）

### 3.6 `scripts/setup_user_data.ps1`（新）
做 4 件事，幂等可重入：
1. `mkdir %AppData%\deskpet\data`, `%AppData%\deskpet\logs`
2. `mkdir %LocalAppData%\deskpet\cache`
3. 若 repo `backend/models/` 存在，junction 到 `%LocalAppData%\deskpet\models`
4. 若 repo `config.toml` 存在且 AppData 里没有，复制过去

### 3.7 `scripts/e2e_frozen_tauri.ps1`
- 删掉 `DESKPET_MODEL_ROOT` 强制注入（让生产路径生效）
- 仍注入 `DESKPET_FFMPEG`（这是运行时依赖，不是数据目录问题）
- 跑前先调 `setup_user_data.ps1` 确保 junction 到位

### 3.8 `backend/pyproject.toml`
- 添加 `platformdirs>=4.0`（纯 Python，~20KB，PyInstaller 打包友好）

### 3.9 `backend/deskpet-backend.spec`
- `hiddenimports += ["platformdirs"]`（防御性，collect_submodules 通常能抓到）

---

## 4. 测试

### 4.1 单元测试
- `backend/tests/test_paths_user_dirs.py`
  - env override 生效
  - frozen 模式（monkey-patch `sys.frozen`、`sys._MEIPASS`）
  - dev 模式回退
  - `ensure_user_dirs` 幂等
- `backend/tests/test_config_resolution.py`
  - AppData 优先于 bundle 默认
  - 首启 seed 逻辑：源存在 → 拷贝；源不存在 → 降级到 AppConfig()
  - `db_path` 默认解析为 AppData

### 4.2 冒烟
- `scripts/smoke_backend.py` 跑 dev 模式 → /health ok
- `scripts/setup_user_data.ps1` 幂等跑两次不报错
- `scripts/e2e_frozen_tauri.ps1` 跑 frozen bundle → /health ok，log 显示
  `model_root` 指向 LocalAppData，`config_path` 指向 AppData

### 4.3 人工 E2E（最后一并做）
- 卸载 AppData/LocalAppData 里的 deskpet 目录，重跑 e2e script
- 确认自动 seed 了 config
- 说一句话，ASR + LLM + TTS 完整回合

---

## 5. 不做的事

- **不做**首启 GUI 下载器：Phase 4（或 bonus slice）
- **不做**日志 logrotate：另开 slice
- **不做**跨 OS 实测：Windows 优先，macOS/Linux 走 platformdirs 天然支持、
  只靠单测覆盖
- **不改动** P3-S5 的 CUDA DLL 重打包（已验证工作）

---

## 6. 验收

- [ ] `pytest backend/tests/test_paths_user_dirs.py backend/tests/test_config_resolution.py` 全绿
- [ ] `scripts/smoke_backend.py` /health ok
- [ ] `powershell scripts/setup_user_data.ps1` 幂等
- [ ] `powershell scripts/build_backend.ps1` 产物仍 ≤ 2 GB（不退化）
- [ ] `powershell scripts/e2e_frozen_tauri.ps1` /health ok + 日志显示走 AppData
- [ ] 用户手动 E2E：删除 AppData → 启动 → 自动 seed → 讲话 → 听到回复
- [ ] STATE.md 标 P3-S6 + P3-S7 done，PACKAGING.md 加 §5 用户数据目录章节
