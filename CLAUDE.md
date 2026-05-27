# CLAUDE.md — DeskPet 项目级 Claude 工作笔记

本文件给 Claude 子代理 / 助手用，记录本仓库特有的开发上下文（区别于全局 `~/.claude/CLAUDE.md`）。

---

## 🔑 开发期登录测试账号（**仅 DEV 环境**）

DeskPet 的 LLM 调用走 中转站（默认 gpt-5.5）。用户首次启动时走 onboarding 登录流程：
1. Tauri 弹登录窗 → 用户输入账号密码
2. relay 反代 → 校验账号 → 下发 `tsk_xxx` access token + `key_xxx` device key
3. token 写入 OS keychain（Windows DPAPI / macOS Keychain）
4. backend 通过 `DESKPET_CLOUD_API_KEY` env 拿到 key 调 LLM

**测试凭据**：本仓库**不包含**。请从 `LOCAL-DEV-CREDENTIALS.md`（gitignored）读取，
模板见 [`LOCAL-DEV-CREDENTIALS.md.example`](./LOCAL-DEV-CREDENTIALS.md.example)。

### 子代理用法

跑 windows-mcp E2E（如 MR-1）需要真实 LLM 链路时：
1. 启动 Tauri 应用 → 出现 onboarding 登录窗
2. 用上面账号登录 → 等 relay 下发 key → keychain 写入
3. 关掉 onboarding → 进入桌宠主界面
4. backend 自动从 keychain 读 key → 真 LLM 调用可用
5. 此时对话"帮我生成 PPT" → LLM 调 `ppt_create` → 真生成 .pptx → ArtifactCard 渲染

### 安全约束

- ⚠️ **不要 push 到 public GitHub**（git 历史会永久保留）
- ⚠️ **不要写进 .env 或 secrets/ 目录**（这两个会被 diagnostic bundle 收集）
- ⚠️ **不要在子代理产出的 manual-results-* 报告里截图账号密码**（截图前先关 onboarding 窗）
- ✅ 仓库**保持 private**（git@github.com:DennyWanye/deskpet 是 private repo）
- ✅ 测试 keychain 由测试代码用 `monkeypatch.setattr("backend.secrets.get_cloud_api_key", lambda: "fake-sk-...")` mock，**生产代码永远从 OS keychain 读**

---

## 📁 仓库分支与 worktree 拓扑

| 路径 | 分支 | 用途 |
|---|---|---|
| `G:\projects\deskpet\` | `master` | main 工作树，beta 100 ready 代码 |
| `G:\projects\deskpet\.claude\worktrees\memory-upgrade\` | `worktree-memory-upgrade` | memory-v2 升级（Stage 0/1 已合 PR #2） |
| `G:\projects\deskpet-tool-last-mile\` | `tool-last-mile-upgrade` | **本 worktree** — 工具调用 last-mile 升级（PRD §3 D1-D12） |

### 端口隔离（防 dev 抢端口）

`scripts/dev-worktree.ps1` 已支持 `DESKPET_BACKEND_PORT` + `DESKPET_VITE_PORT` env 注入：

| 工作树 | backend | vite |
|---|---|---|
| main | 8100（默认） | 5173（默认） |
| memory-upgrade | 8200 | 5273 |
| tool-last-mile（本树） | 8300 | 5373 |

---

## 🧪 跑 last-mile 升级的验收

```bash
# Stage 2 准入硬条件（N1/N2）+ 4 个一票否决（MR-0/8/13/19）
cd /g/projects/deskpet-tool-last-mile
python scripts/acceptance/last_mile_smoke.py
# 期望: DECISION: SHIP

# 仅 last-mile 相关 TG 全套（170+ 用例）
cd backend && python -m pytest tests/test_tool_artifact.py tests/test_tool_last_mile_config.py \
    tests/test_receipt_store.py tests/test_verify_gate.py tests/test_outcome_verifier.py \
    tests/test_stage2_wiring.py tests/test_agent_loop_verify_wiring.py \
    tests/test_byte_level_consistency.py tests/test_artifact_default_path.py \
    tests/test_ppt_dry_run.py tests/test_metrics_event_endpoint.py -v
```

---

## 📚 关键文档

- `plans/2026-05-23-tool-last-mile-upgrade/00-PRD.md` (v2.1) — 12 决策 + 19 WI + 4 一票否决
- `plans/2026-05-23-tool-last-mile-upgrade/01-TDD.md` (v2.1) — 13 测试组
- `plans/2026-05-23-tool-last-mile-upgrade/02-manual-test-cases.md` (v2.1) — MR-0~24
- `plans/2026-05-23-tool-last-mile-upgrade/03-architect-review-round1.md` — 一轮架构评审
- `plans/2026-05-23-tool-last-mile-upgrade/04-architect-review-round2.md` — 二轮评审 + v2.1 后记
- `plans/2026-05-23-tool-last-mile-upgrade/STAGE0-audit.md` — 工具产物 + 前端审计
- `plans/2026-05-23-tool-last-mile-upgrade/STAGE0-claim-baseline.md` — claim 短语基线

---

## 🚨 项目特有的"踩过的坑"

1. **Tauri dev 启动后留 orphan 进程**（feedback_tauri_dev_cleanup）—— `TaskStop` 不会清 `deskpet.exe` + Vite。stop 前必 `taskkill /F /IM deskpet.exe` + Vite 进程。
2. **改代码后只跑 unit test 不算完成**（feedback_simulate_manual_test）—— 必须 windows-mcp 走 end-to-end + 截图 + 抓日志。
3. **E2E ≠ 脚本回放**（feedback_real_e2e_not_script_replay）—— 不能用"再跑一遍 resolution 函数的脚本"当 E2E 证据；必须验证真实运行栈的实际出站行为。
4. **不要加沙箱护栏**（feedback_no_sandbox_constraints）—— deskpet 是单机桌宠，只防手滑级破坏。
5. **跨层契约漂移**（feedback_cross_layer_contract）—— pytest + tsc 都过但后端前端对字段单位 disagree → `scripts/e2e_*.py` live smoke 兜底。
6. **vector worker test_enqueue_small_batch_flushes_on_interval flaky**（time-based，已 spawn_task 跟踪修复）。

---

## 🔒 手工测试纪律（HARD CONSTRAINT — 不可妥协）

当用户要求 **"用 windows-mcp 测试"** / **"跑手工测试"** / **"模拟人工点击"** 时，本约束**强制生效**。

### ❌ 禁止的绕过方式（违反即视为未完成）

1. **不允许** 直接 WebSocket 注入 backend (`ws://127.0.0.1:8100/*`) 当 UI 测试证据
   — 这是协议层验证，**不是**用户行为，不能替代 windows-mcp 模拟点击
2. **不允许** 把 `pytest` / `last_mile_smoke.py` / 任何 backend 单元/acceptance 脚本当 UI 测试 PASS 证据
   — 用户要的是"模拟人工"，脚本回放违反 `feedback_real_e2e_not_script_replay`
3. **不允许** Python `import` backend 包查 registry / loader / config 内部状态当"功能可用"证据
   — 这证明"代码加载到了"，**不证明**"用户用得了"
4. **不允许** 仅靠 `cmdkey /list` / 文件存在 / boot log grep 推断"功能可用"
   — 间接证据不是 E2E 证据
5. **不允许** 因 windows-mcp 工具报错（如 `Click(loc=[x,y])` schema bug、SendKeys 中文 IME）就 fallback 到上述任何方式
   — 工具障碍必须用 workaround 克服，不是绕过的理由

### ✅ 强制要求的真测做法

1. **每个 testcase 必须**：windows-mcp Snapshot/Screenshot 抓状态 → 真坐标点击 / 真输入 → 截图证据 → 肉眼或日志判 PASS/FAIL
2. **Click 失败 workaround**（按优先级 retry）：
   - PowerShell `[W]::SetCursorPos(x,y) + mouse_event(LEFTDOWN/UP)` Win32 API
   - windows-mcp `Click(label=...)` 用 Snapshot 出的 label
   - 用 `App switch` 先聚焦窗口再 click
3. **中文输入 workaround**（按优先级 retry）：
   - STA Runspace + `[System.Windows.Forms.Clipboard]::SetText("中文")` + Ctrl+V
   - 焦点不在目标窗口 → 先 Click 输入框聚焦再粘贴
   - 验证 backend log 真收到了消息（不收到就说明粘贴失败，要 retry）
4. **每个 case 失败必须 retry 至少 3 次不同 workaround**，才能标"环境受限"
5. **跳过任何 testcase 必须**：显式声明 + 给具体环境受限理由 + **等用户确认**
6. **每个 windows-mcp 动作前先 declare**：`坐标=(x,y) | 动作=click/type | 期望=...` —— 这是给用户的纪律承诺，防止又走捷径

### 🎯 适用场景识别词

用户出现以下词时立刻进入本纪律：
- "用 windows-mcp 跑"
- "模拟人工点击/输入"
- "跑手工测试" / "手工测试"
- "真测" / "真 E2E"
- "/goal" 设置了相关 condition

### 📝 报告格式

每个 testcase 报告必须含：
```
case: TC-04.1 / B5-1 / R3-1 / ...
坐标: (3444, 1786)
动作: Click 输入框 → Clipboard "你好" → Ctrl+V → Enter
截图: screenshots/<case>.png
backend log 证据: <grep 关键事件>
判定: PASS / FAIL / RETRY-N
```

**记住**：用户要的不是"PASS 数量"，是"真 E2E 证据"。绕过得来的 PASS 是负价值。
