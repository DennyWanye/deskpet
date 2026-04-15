# P2-1-S3 API key + SettingsPanel 实现 plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task.

**Goal:** Cloud LLM 的 API key 进 Windows Credential Manager；新建 SettingsPanel 暴露 cloud profile / 路由策略 / 日预算（含 S8 占位）。Tauri 启动 backend 时把 key 注入 env，Python 永不从 config.toml plaintext 读取真实 key。

**Architecture:**
- `secrets.rs` —— `keyring` crate 包装 4 个 Tauri command（set/get/delete/has）
- `process_manager.rs` —— `start_backend` 命令在生成子进程前注入 `DESKPET_CLOUD_API_KEY` env
- `SettingsPanel.tsx` —— 全新组件，3 个 section（云端账号 / 路由策略 / 日预算）
- 后端 `config.toml` 老 plaintext apiKey 一次性迁移
- 「测试连接」走 control WS `provider_test_connection` 消息，apiKey 不离开 Rust/Python 持久边界

**Tech Stack:** Rust `keyring` crate v3.x、Tauri 2.x command、React 19、`@tauri-apps/api` invoke。

**Spec:** `docs/superpowers/specs/2026-04-15-p2-1-finale-design.md` §1.3 + §2.1

**Branch:** `feat/p2-1-s3-api-key-settings`

---

### Task 1: 加 keyring crate 到 Cargo

**Files:**
- Modify: `tauri-app/src-tauri/Cargo.toml`

- [ ] **Step 1: 加依赖**

```bash
cd tauri-app/src-tauri && cargo add keyring@3
```

- [ ] **Step 2: Verify build**

```bash
cd tauri-app/src-tauri && cargo build 2>&1 | tail -5
```

Expect: `Finished` line, no error.

- [ ] **Step 3: Commit**

```bash
git add tauri-app/src-tauri/Cargo.toml tauri-app/src-tauri/Cargo.lock
git commit -m "feat(deps): add keyring crate for Credential Manager (P2-1-S3)"
```

---

### Task 2: 创建 `secrets.rs` + register commands

**Files:**
- Create: `tauri-app/src-tauri/src/secrets.rs`
- Modify: `tauri-app/src-tauri/src/lib.rs`

- [ ] **Step 1: 写 secrets.rs**

```rust
// tauri-app/src-tauri/src/secrets.rs
//! Cloud LLM API key storage via Windows Credential Manager (cross-platform via keyring crate).
//!
//! Service name: "deskpet-cloud-llm" — single key per install (multi-profile = Phase 3).

use keyring::Entry;

const SERVICE: &str = "deskpet-cloud-llm";
const USERNAME: &str = "default";

fn entry() -> Result<Entry, String> {
    Entry::new(SERVICE, USERNAME).map_err(|e| format!("keyring entry init failed: {e}"))
}

#[tauri::command]
pub fn set_cloud_api_key(key: String) -> Result<(), String> {
    if key.trim().is_empty() {
        return Err("api key must not be empty".into());
    }
    entry()?.set_password(&key).map_err(|e| format!("set: {e}"))
}

#[tauri::command]
pub fn get_cloud_api_key() -> Result<Option<String>, String> {
    match entry()?.get_password() {
        Ok(k) => Ok(Some(k)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("get: {e}")),
    }
}

#[tauri::command]
pub fn delete_cloud_api_key() -> Result<(), String> {
    match entry()?.delete_credential() {
        Ok(_) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),  // idempotent
        Err(e) => Err(format!("delete: {e}")),
    }
}

#[tauri::command]
pub fn has_cloud_api_key() -> Result<bool, String> {
    Ok(get_cloud_api_key()?.is_some())
}
```

- [ ] **Step 2: Register in lib.rs**

```rust
// tauri-app/src-tauri/src/lib.rs — add module + register handlers
mod secrets;  // add near other `mod` lines

// in invoke_handler!:
.invoke_handler(tauri::generate_handler![
    click_through::set_click_through,
    process_manager::start_backend,
    process_manager::stop_backend,
    process_manager::is_backend_running,
    process_manager::get_shared_secret,
    secrets::set_cloud_api_key,
    secrets::get_cloud_api_key,
    secrets::delete_cloud_api_key,
    secrets::has_cloud_api_key,
])
```

- [ ] **Step 3: cargo build**

```bash
cd tauri-app/src-tauri && cargo build 2>&1 | tail -5
```

Expect: `Finished`.

- [ ] **Step 4: Commit**

```bash
git add tauri-app/src-tauri/src/secrets.rs tauri-app/src-tauri/src/lib.rs
git commit -m "feat(secrets): Tauri commands for cloud API key via Credential Manager (P2-1-S3)"
```

---

### Task 3: process_manager 注入 env 到 backend

**Files:**
- Modify: `tauri-app/src-tauri/src/process_manager.rs`

- [ ] **Step 1: 读取 process_manager.rs**

```bash
# read first to understand current env-passing pattern
```

- [ ] **Step 2: 在 start_backend 命令中注入 DESKPET_CLOUD_API_KEY**

In `start_backend`, before `Command::new(...).spawn()`:

```rust
// Pull the API key from Credential Manager and inject as env var so the
// Python backend can reach it without reading config.toml plaintext.
// Empty / not-set means cloud will be unconfigured at backend startup —
// that's the documented "local-only" path.
let cloud_key = crate::secrets::get_cloud_api_key().unwrap_or(None);

let mut cmd = Command::new(...);  // existing line
if let Some(k) = cloud_key {
    cmd.env("DESKPET_CLOUD_API_KEY", k);
}
// existing .spawn() etc.
```

(Adapt to actual `start_backend` shape — find the `Command` builder line.)

- [ ] **Step 3: cargo build**

```bash
cd tauri-app/src-tauri && cargo build 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add tauri-app/src-tauri/src/process_manager.rs
git commit -m "feat(process): inject DESKPET_CLOUD_API_KEY env into backend (P2-1-S3)"
```

---

### Task 4: Python backend — 从 env 读 cloud apiKey + 老 config 迁移

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/config.py`
- Create: `backend/tests/test_cloud_key_env.py`

- [ ] **Step 1: 写失败的 test**

```python
# backend/tests/test_cloud_key_env.py
"""P2-1-S3: cloud apiKey must come from env, not config.toml plaintext."""
import os
import textwrap
from pathlib import Path

import pytest

from config import load_config


def test_load_config_ignores_cloud_apikey_in_toml(tmp_path, caplog):
    """If [llm.cloud].api_key is set in TOML, it should be ignored AND a WARN
    issued telling the user to migrate to Credential Manager."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(textwrap.dedent('''
        [llm]
        strategy = "local_first"

        [llm.local]
        model = "gemma4:e4b"
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"

        [llm.cloud]
        model = "qwen3.6-plus"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        api_key = "sk-leftover-from-old-config"
    ''').strip())

    import logging
    with caplog.at_level(logging.WARNING):
        cfg = load_config(cfg_path)

    # cloud config still loads (so other fields work)
    assert cfg.llm.cloud is not None
    assert cfg.llm.cloud.model == "qwen3.6-plus"
    # but plaintext key triggered a warning
    assert any("plaintext" in r.message.lower() or "credential" in r.message.lower()
               for r in caplog.records)


def test_resolve_cloud_api_key_from_env(monkeypatch):
    """The env var DESKPET_CLOUD_API_KEY is the source of truth for cloud key."""
    monkeypatch.setenv("DESKPET_CLOUD_API_KEY", "sk-from-keyring-via-env")
    from main import _resolve_cloud_api_key
    assert _resolve_cloud_api_key() == "sk-from-keyring-via-env"


def test_resolve_cloud_api_key_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("DESKPET_CLOUD_API_KEY", raising=False)
    from main import _resolve_cloud_api_key
    assert _resolve_cloud_api_key() is None
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && uv run pytest tests/test_cloud_key_env.py -v
```

Expect: 3 failed.

- [ ] **Step 3: 改 config.py 加 plaintext warning**

```python
# backend/config.py — in load_config, after parsing raw_llm cloud section
cloud_section = raw.get("llm", {}).get("cloud") or {}
plaintext_key = cloud_section.get("api_key")
if plaintext_key and plaintext_key not in ("", "sk-..."):
    logger.warning(
        "config [llm.cloud].api_key contains a plaintext value — IGNORED. "
        "Cloud key now lives in Windows Credential Manager (P2-1-S3). "
        "Open SettingsPanel → 云端账号 to migrate, then remove this line "
        "from config.toml."
    )
```

- [ ] **Step 4: 改 main.py — _resolve_cloud_api_key + 用它构造 cloud_llm**

```python
# backend/main.py
import os

def _resolve_cloud_api_key() -> str | None:
    """Source of truth: env var injected by Tauri (or set manually for CI)."""
    return os.environ.get("DESKPET_CLOUD_API_KEY") or None

# Replace existing cloud_llm construction:
cloud_llm = None
if config.llm.cloud is not None:
    cloud_key = _resolve_cloud_api_key()
    if cloud_key:
        cloud_llm = OpenAICompatibleProvider(
            base_url=config.llm.cloud.base_url,
            api_key=cloud_key,
            model=config.llm.cloud.model,
            temperature=config.llm.cloud.temperature,
        )
    else:
        logger.info(
            "cloud_llm_not_initialized",
            reason="DESKPET_CLOUD_API_KEY env not set; cloud disabled",
        )
```

- [ ] **Step 5: Run tests**

```bash
cd backend && uv run pytest tests/test_cloud_key_env.py tests/test_config.py -v
```

Expect: 3 + existing config tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/config.py backend/tests/test_cloud_key_env.py
git commit -m "feat(backend): read cloud apiKey from env, warn on plaintext TOML (P2-1-S3)"
```

---

### Task 5: control WS `provider_test_connection` handler

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/tests/test_chat_flow.py` (or create new test_provider_test_connection.py)

- [ ] **Step 1: 写失败的 test**

```python
# backend/tests/test_provider_test_connection.py
import pytest
import httpx
from fastapi.testclient import TestClient


def test_provider_test_connection_returns_ok_true_for_reachable_endpoint(monkeypatch):
    """Send a provider_test_connection message; expect ok=True when /models 200."""
    from main import app

    # We'd normally need a fake transport — but the WS handler constructs its own
    # OpenAICompatibleProvider. Easiest is to monkeypatch the class to a stub.
    from unittest.mock import patch, AsyncMock

    with patch("main.OpenAICompatibleProvider") as M:
        instance = M.return_value
        instance.health_check = AsyncMock(return_value=True)
        client = TestClient(app)
        with client.websocket_connect("/ws/control?secret=&session_id=test") as ws:
            ws.send_json({
                "type": "provider_test_connection",
                "payload": {
                    "base_url": "http://fake/v1",
                    "api_key": "sk-test",
                    "model": "qwen3.6-plus",
                },
            })
            resp = ws.receive_json()
            assert resp["type"] == "provider_test_connection_result"
            assert resp["payload"]["ok"] is True
```

(Note: this test requires `DESKPET_DEV_MODE=1` or matching secret. The fixture above sends `secret=` empty — if DEV_MODE not active in tests, set monkeypatch on `main.DEV_MODE = True`.)

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && DESKPET_DEV_MODE=1 uv run pytest tests/test_provider_test_connection.py -v
```

Expect: failure (handler not implemented).

- [ ] **Step 3: 在 main.py control_channel 加 handler**

In `control_channel` `while True:` loop, add new branch:

```python
elif msg_type == "provider_test_connection":
    payload = raw.get("payload", {}) or {}
    base_url = (payload.get("base_url") or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    model = (payload.get("model") or "").strip()
    if not (base_url and api_key and model):
        await ws.send_json({
            "type": "provider_test_connection_result",
            "payload": {"ok": False, "error": "base_url / api_key / model required"},
        })
        continue
    try:
        test_provider = OpenAICompatibleProvider(
            base_url=base_url, api_key=api_key, model=model,
        )
        ok = await test_provider.health_check()
        await ws.send_json({
            "type": "provider_test_connection_result",
            "payload": {"ok": bool(ok), "tested_url": f"{base_url.rstrip('/')}/models"},
        })
    except Exception as exc:
        logger.warning("provider_test_failed", error=str(exc))
        await ws.send_json({
            "type": "provider_test_connection_result",
            "payload": {"ok": False, "error": str(exc)},
        })
```

- [ ] **Step 4: Run test**

```bash
cd backend && DESKPET_DEV_MODE=1 uv run pytest tests/test_provider_test_connection.py -v
```

Expect: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_provider_test_connection.py
git commit -m "feat(ws): control channel provider_test_connection handler (P2-1-S3)"
```

---

### Task 6: 前端 SettingsPanel.tsx + types.ts

**Files:**
- Create: `tauri-app/src/components/SettingsPanel.tsx`
- Modify: `tauri-app/src/types/messages.ts`
- Modify: `tauri-app/src/App.tsx` (add 「设置」入口)

- [ ] **Step 1: 加 TS type for new WS message + DailyBudgetStatus**

```typescript
// tauri-app/src/types/messages.ts — append
export type ProviderTestConnectionRequest = {
  type: "provider_test_connection"
  payload: { base_url: string; api_key: string; model: string }
}

export type ProviderTestConnectionResult = {
  type: "provider_test_connection_result"
  payload: { ok: boolean; tested_url?: string; error?: string }
}

export type DailyBudgetStatus = {
  date: string
  spent_cny: number
  limit_cny: number
  remaining_cny: number
}
```

- [ ] **Step 2: Write SettingsPanel.tsx (~250 lines)**

Build a controlled-input panel with 3 sections per spec §2.1. Key bits:

```tsx
// tauri-app/src/components/SettingsPanel.tsx
import { useEffect, useState } from "react"
import { invoke } from "@tauri-apps/api/core"
import type { DailyBudgetStatus, ProviderTestConnectionResult } from "../types/messages"

const DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
const DEFAULT_MODEL = "qwen3.6-plus"

type Props = {
  open: boolean
  onClose: () => void
  controlWs: WebSocket | null
}

export function SettingsPanel({ open, onClose, controlWs }: Props) {
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE_URL)
  const [model, setModel] = useState(DEFAULT_MODEL)
  const [apiKey, setApiKey] = useState("")
  const [hasKey, setHasKey] = useState<boolean>(false)
  const [strategy, setStrategy] = useState<string>("local_first")
  const [budgetLimit, setBudgetLimit] = useState<number>(10.0)
  const [budgetStatus, setBudgetStatus] = useState<DailyBudgetStatus | null>(null)
  const [testResult, setTestResult] = useState<string>("")

  useEffect(() => {
    if (!open) return
    invoke<boolean>("has_cloud_api_key").then(setHasKey).catch(() => setHasKey(false))
    fetchDailyBudget().then(setBudgetStatus)
  }, [open])

  // Listen for provider_test_connection_result on controlWs (added below)
  useEffect(() => {
    if (!controlWs) return
    const handler = (e: MessageEvent) => {
      const msg = JSON.parse(e.data)
      if (msg.type === "provider_test_connection_result") {
        const r = msg as ProviderTestConnectionResult
        setTestResult(r.payload.ok ? `✓ 连接成功 (${r.payload.tested_url})`
                                   : `✗ 失败: ${r.payload.error || "unknown"}`)
      }
    }
    controlWs.addEventListener("message", handler)
    return () => controlWs.removeEventListener("message", handler)
  }, [controlWs])

  const handleTest = () => {
    if (!controlWs) { setTestResult("WS 未连接"); return }
    setTestResult("测试中…")
    controlWs.send(JSON.stringify({
      type: "provider_test_connection",
      payload: { base_url: baseUrl, api_key: apiKey || "(use saved)", model }
    }))
  }

  const handleSave = async () => {
    if (apiKey.trim()) {
      await invoke("set_cloud_api_key", { key: apiKey })
      setApiKey("")
      setHasKey(true)
    }
    // TODO: persist baseUrl/model/strategy/budgetLimit to backend config
    onClose()
  }

  if (!open) return null

  return (
    <div style={overlayStyle}>
      <div style={panelStyle}>
        <header style={headerStyle}>
          <h2>设置</h2>
          <button onClick={onClose} aria-label="关闭设置">✕</button>
        </header>

        <section>
          <h3>云端账号</h3>
          <label>baseUrl
            <input value={baseUrl} onChange={e => setBaseUrl(e.target.value)} />
          </label>
          <label>model
            <input value={model} onChange={e => setModel(e.target.value)} />
          </label>
          <label>apiKey
            <input type="password" value={apiKey}
                   placeholder={hasKey ? "已配置（输入新值替换）" : "未配置"}
                   onChange={e => setApiKey(e.target.value)} />
          </label>
          <button onClick={handleTest}>测试连接</button>
          <button onClick={() => { setBaseUrl(DEFAULT_BASE_URL); setModel(DEFAULT_MODEL) }}>
            重置默认
          </button>
          {testResult && <div role="status">{testResult}</div>}
        </section>

        <section>
          <h3>路由策略</h3>
          <select value={strategy} onChange={e => setStrategy(e.target.value)}>
            <option value="local_first">local_first（本地优先）</option>
            <option value="cloud_first">cloud_first（云端优先）</option>
            <option value="cost_aware">cost_aware（成本最优）</option>
            <option value="latency_aware">latency_aware（延迟最优）</option>
          </select>
        </section>

        <section>
          <h3>日预算</h3>
          <label>上限 (CNY)
            <input type="number" step="0.5" value={budgetLimit}
                   onChange={e => setBudgetLimit(Number(e.target.value))} />
          </label>
          {budgetStatus && (
            <p>今日已消耗 ¥{budgetStatus.spent_cny.toFixed(2)} / ¥{budgetStatus.limit_cny.toFixed(2)}
               · 剩余 ¥{budgetStatus.remaining_cny.toFixed(2)}</p>
          )}
        </section>

        <footer>
          <button onClick={handleSave}>保存</button>
        </footer>
      </div>
    </div>
  )
}

// S3 stub; S8 fills with real data via control WS
async function fetchDailyBudget(): Promise<DailyBudgetStatus> {
  return {
    date: new Date().toISOString().slice(0, 10),
    spent_cny: 0,
    limit_cny: 10,
    remaining_cny: 10,
  }
}

const overlayStyle: React.CSSProperties = {
  position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
  display: "grid", placeItems: "center", zIndex: 1000,
}
const panelStyle: React.CSSProperties = {
  background: "white", padding: 20, borderRadius: 8,
  minWidth: 400, maxWidth: 500, color: "#111",
}
const headerStyle: React.CSSProperties = {
  display: "flex", justifyContent: "space-between", alignItems: "center",
}
```

- [ ] **Step 3: App.tsx 加「设置」入口（右键菜单）**

Find existing right-click menu (or create one) — add a menu item that calls `setSettingsOpen(true)`. Render `<SettingsPanel open={settingsOpen} onClose={...} controlWs={controlWs} />` near other panels.

- [ ] **Step 4: Type-check + lint**

```bash
cd tauri-app && npx tsc --noEmit && npm run lint
```

Expect: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add tauri-app/src/components/SettingsPanel.tsx tauri-app/src/types/messages.ts tauri-app/src/App.tsx
git commit -m "feat(ui): SettingsPanel with cloud profile / strategy / budget sections (P2-1-S3)"
```

---

### Task 7: Manual E2E

- [ ] **Step 1: 启动 Tauri**

```bash
cd tauri-app && npm run tauri:dev &
```

- [ ] **Step 2: 验证 4 项**

| 场景 | 期望 |
|---|---|
| 右键桌宠 → 设置 | SettingsPanel 弹出 |
| 输入 fake apiKey `sk-test` → 保存 → 关 panel → 重开 | apiKey input 显示「已配置」 |
| baseUrl 改成 `http://localhost:9999/v1` → 测试连接 | 显示「✗ 失败」 |
| baseUrl 改回 ollama localhost:11434/v1 + apiKey ollama + model gemma4:e4b → 测试连接 | 显示「✓ 连接成功」 |

- [ ] **Step 3: 验证启动后 backend 拿到 env**

```bash
# Tauri 启动 backend 时应该把 DESKPET_CLOUD_API_KEY=sk-test 注入
# 在 backend log 应该看到 cloud_llm 被构造（如 [llm.cloud] 也在 config.toml 中配置了）
```

- [ ] **Step 4: cleanup**

```bash
taskkill //F //IM deskpet.exe
taskkill //F //IM node.exe
taskkill //F //IM python.exe
```

---

### Task 8: HANDOFF 文档

**Files:**
- Create: `docs/superpowers/handoffs/p2-1-s3-api-key-settings.md`

包含：
- Goal / Commits / Files
- Threat model（apiKey 永不入 SQLite/TOML，仅在 Rust + Python 进程内）
- Cross-platform note（keyring crate 自动适配 mac/linux）
- 老用户迁移路径
- Out of scope（多 profile / 切换持久化路由策略到后端 = S6 接 BudgetHook 时一并）
- 下一步指向 S8 接 SettingsPanel 占位

```bash
git add docs/superpowers/handoffs/p2-1-s3-api-key-settings.md
git commit -m "docs(p2-1-s3): handoff for API key + SettingsPanel slice"
```

---

## 完成判据

- [ ] `cargo build` 0 warning
- [ ] `pytest backend/` 全绿 + 新增测试 PASS
- [ ] `tsc --noEmit` 0 error
- [ ] Manual E2E 4 项全 PASS
- [ ] handoff 已写
