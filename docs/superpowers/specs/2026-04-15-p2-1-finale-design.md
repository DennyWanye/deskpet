# P2-1 收尾包 — 设计 spec（S3 + S6 + S7 + S8）

**Date:** 2026-04-15
**Sprint:** V6 Phase 2 · Sprint P2-1 · Slices S3 / S6 / S7 / S8
**Status:** SIGNED-OFF（2026-04-15，全部 Q1–Q18 默认推荐）
**Roadmap:** [`docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md`](../plans/2026-04-14-phase2-v6-roadmap.md) §3.2
**Predecessor:** [`docs/superpowers/handoffs/p2-1-s2-hybrid-router.md`](../handoffs/p2-1-s2-hybrid-router.md)

---

## 0. 文档范围

本 spec 一次性覆盖 P2-1 剩余 4 个 slice：

| Slice | 目标（一句话） |
|---|---|
| **S3** | API key 进 Windows Credential Manager；新建 SettingsPanel 暴露 cloud profile / 路由策略 / 日预算编辑 |
| **S6** | `/metrics` 端点 + `llm_ttft_seconds{provider,model}` Histogram + perf 脚本 |
| **S7** | pytest E2E 验证 fallback 全链路（`MockTransport` 注入 503 → 30s 内回复或 `[echo]`） |
| **S8** | `BillingLedger`（SQLite）+ token 计数 + 日 ¥10 预算护栏 + 超额降级 + 前端 toast |

四 slice **可并行**，merge 顺序 **S6 → S3 → S7 → S8**。

为什么并行可行：4 个 slice 在「文件归属」+「2 个跨 slice 接口契约」清晰后，3 个能在独立 worktree 完成；S8 因 router 二次改 + 前端占位填充，作为最后 merge 项主动 rebase。

---

## 1. 跨 slice 接口契约（提前定死 = 并行的前提）

### 1.1 `BudgetHook` 签名（S6 / S8 都依赖）

替换 `HybridRouter.__init__` 现有 `budget_check: Callable[[], bool] | None`：

```python
# backend/router/types.py（S6 创建，S8 实现填充）
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

@dataclass(frozen=True)
class BudgetContext:
    provider: Literal["local", "cloud"]
    model: str
    estimated_input_tokens: int | None  # None = 不估算（S8 决定不强约束）

@dataclass(frozen=True)
class BudgetDecision:
    allow: bool
    reason: str | None  # 拒绝时的人类可读原因；UI 直接 toast

BudgetHook = Callable[[BudgetContext], Awaitable[BudgetDecision]]
```

**契约规则：**
- 调用时机：`HybridRouter.chat_stream` 在选定 provider **之后、调 provider.chat_stream 之前** 同步 await
- 默认实现（无 ledger 时）：`async def _allow_all(ctx): return BudgetDecision(allow=True, reason=None)`
- 拒绝行为：S8 决定 —— `provider="cloud"` 拒绝 → router 自动 fallback 到 local；`provider="local"` 永不拒绝（local 免费）
- **关键约束：** S6 创建 `types.py` + 把 `_budget_check` 字段类型升级到 `BudgetHook | None`（含默认 `_allow_all`），**不实现 ledger**。S8 在 S6 merge 后 rebase，填 `BillingLedger.create_hook()` 实现。

### 1.2 `usage` 数据回传（S8 单方依赖，但需要 S6 merge 后 rebase）

`OpenAICompatibleProvider.chat_stream` 当前只 yield content tokens。S8 改造：

```python
# backend/providers/openai_compatible.py（S8 修改）
class OpenAICompatibleProvider:
    def __init__(self, ...):
        ...
        self.last_usage: dict | None = None  # {prompt_tokens, completion_tokens, total_tokens}

    async def chat_stream(self, messages, *, ...):
        self.last_usage = None  # reset
        payload = {
            ...,
            "stream_options": {"include_usage": True},  # OpenAI 标准
        }
        async with self._client(...) as client:
            ...
            async for line in response.aiter_lines():
                ...
                data = json.loads(data_str)
                # 兼容 Ollama 旧格式（不一定支持 stream_options，缺字段 fallback）
                if data.get("usage"):
                    self.last_usage = data["usage"]
                    continue  # usage chunk 没有 choices
                choices = data.get("choices") or []
                ...
```

**契约规则：**
- 每次 `chat_stream` 调用结束后，`provider.last_usage` 要么是 OpenAI 标准 dict，要么是 `None`（Ollama 不支持时）
- HybridRouter 在 stream 完成后读 `provider.last_usage`，喂给 `BillingLedger.record(...)`
- 如果 `last_usage is None`（local Ollama 通常不返回），ledger 仍需记录一行 `total_tokens=0, cost_cny=0`，用于审计完整性

### 1.3 `SettingsPanel.tsx` JSX 占位（S3 建，S8 填）

S3 把 SettingsPanel 完整脚手架建好，含「日预算」section 的占位组件：

```tsx
// tauri-app/src/components/SettingsPanel.tsx（S3 创建）

// 这两个 Tauri command 名 + 返回类型由 spec 定死
// S3 时：调用返回 mock 数据（today=0.0, limit=10.0）
// S8 时：实现 backend 真实数据
type DailyBudgetStatus = {
  date: string           // "2026-04-15"
  spent_cny: number      // 当日已花
  limit_cny: number      // 用户配置的上限
  remaining_cny: number  // limit - spent
}

// S3 写空实现 + 留 TODO(P2-1-S8)
async function fetchDailyBudget(): Promise<DailyBudgetStatus> {
  // S3: return { date: today, spent_cny: 0, limit_cny: 10, remaining_cny: 10 }
  // S8: 实际通过 control WS 拿
  return { date: new Date().toISOString().slice(0,10),
           spent_cny: 0, limit_cny: 10, remaining_cny: 10 }
}
```

**契约规则：**
- S3 必须实现完整 JSX（含「今日已消耗 ¥X.XX / ¥10.00」展示），用 mock 数据；S8 只改 `fetchDailyBudget` 实现
- 「日预算上限」编辑框由 S3 写到 `config.toml` 的 `[llm]daily_budget_cny`（已存在字段）
- 「toast 超额提示」由 S8 监听 `chat_response` 中新增的 `payload.budget_exceeded: bool` 字段触发；S3 不实现 toast 组件本身

---

## 2. 各 slice 详细设计

### 2.1 S3 — API key + SettingsPanel

**目标：** 让用户能在 UI 内填 cloud baseUrl/apiKey/model + 切策略 + 改预算，apiKey 通过 Windows Credential Manager 持久化（绝不入 SQLite / config.toml plaintext）。

**架构：**

```
SettingsPanel.tsx
  ├── 「云端账号」section
  │     ├── baseUrl input (默认 https://dashscope.aliyuncs.com/compatible-mode/v1)
  │     ├── model input (默认 qwen3.6-plus)
  │     ├── apiKey input (type=password，placeholder「已配置」/「未配置」)
  │     ├── [测试连接] button → control WS provider_test_connection
  │     ├── [重置默认] button (只重置 baseUrl/model，不动 key)
  │     └── [保存] button
  ├── 「路由策略」section
  │     └── select: local_first / cloud_first / cost_aware / latency_aware
  └── 「日预算」section
        ├── 上限 input (默认 10.0 CNY)
        ├── 今日已消耗显示（mock 或 S8 实数）
        └── [保存] button
```

**Rust 侧（`tauri-app/src-tauri/src/secrets.rs` 新建）：**

```rust
use keyring::Entry;

const SERVICE: &str = "deskpet-cloud-llm";

#[tauri::command]
pub fn set_cloud_api_key(key: String) -> Result<(), String> {
    Entry::new(SERVICE, "default")
        .map_err(|e| e.to_string())?
        .set_password(&key)
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn get_cloud_api_key() -> Result<Option<String>, String> {
    match Entry::new(SERVICE, "default").map_err(|e| e.to_string())?.get_password() {
        Ok(k) => Ok(Some(k)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
pub fn delete_cloud_api_key() -> Result<(), String> {
    let entry = Entry::new(SERVICE, "default").map_err(|e| e.to_string())?;
    match entry.delete_password() {
        Ok(_) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),  // idempotent
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
pub fn has_cloud_api_key() -> Result<bool, String> {
    Ok(get_cloud_api_key()?.is_some())
}
```

**Python 后端读取 apiKey 的路径：**

`config.toml` 里 `[llm.cloud].api_key` **不再用作真实 key**（保留字段以兼容旧 schema 但忽略）。后端启动时改成：

```python
# backend/main.py（S3 改）
import os
def _get_cloud_api_key() -> str | None:
    # 优先 env var（CI / 开发用），否则从启动 args 拿（Tauri 启动 backend 时注入）
    return os.environ.get("DESKPET_CLOUD_API_KEY")
```

Tauri 在 `start_backend` 时调 `get_cloud_api_key()` → 注入 `DESKPET_CLOUD_API_KEY` env 给子进程。

**老 config.toml plaintext apiKey 自动迁移（Q5=A）：**

```python
# backend/config.py（S3 加）
def _migrate_plaintext_cloud_key(cfg_path: Path, raw: dict) -> None:
    """If [llm.cloud].api_key contains a value, write to Credential Manager via
    a one-shot CLI helper, then strip from TOML on disk."""
    cloud = raw.get("llm", {}).get("cloud", {})
    api_key = cloud.get("api_key")
    if api_key and api_key not in ("", "sk-..."):
        logger.info("config_migrating_plaintext_cloud_key_to_credential_manager")
        # 写文件去掉 api_key 行 + 通过 stderr 提示用户重启 Tauri
        ...
```

**「测试连接」按钮路径（Q4=A）：**

新增 control WS 消息：

```python
# backend/main.py（S3 加）
elif msg_type == "provider_test_connection":
    payload = raw.get("payload", {}) or {}
    base_url = payload.get("base_url", "")
    api_key = payload.get("api_key", "")
    model = payload.get("model", "")
    test_provider = OpenAICompatibleProvider(
        base_url=base_url, api_key=api_key, model=model,
    )
    ok = await test_provider.health_check()
    await ws.send_json({
        "type": "provider_test_connection_result",
        "payload": {"ok": ok, "tested_url": base_url + "/models"},
    })
```

⚠️ 这里 apiKey 从 JS → WS → Python，确实暴露给 JS。解释：JS 输入框里用户刚输入的 key 必然在 JS 内存里待过；测试完成后 JS 调 `set_cloud_api_key` → Rust 持久化 → JS 把 input 清空。整体生命周期内 plaintext key 不进 SQLite/不进 config.toml。

**入口（Q3=A）：** 复用 MemoryPanel 的开关模式 —— `App.tsx` 头部右键菜单加「设置」项。

**E2E 自测点：**
1. 启动 Tauri，打开设置，填 fake apiKey `sk-test`，保存 → 关 Tauri → 重开 → key 还在
2. 测试连接按钮：填错 baseUrl → 红色失败提示；填对 → 绿色成功
3. 启动时 config.toml 含老 plaintext key → 启动后 toml 中 key 被清空 + Credential Manager 有值 + 后端能用

---

### 2.2 S6 — TTFT 埋点 + /metrics

**目标：** 暴露 Prometheus 端点，记录 cloud / local LLM 的 first-token 延迟分布。

**新增依赖：** `prometheus_client` (PyPI)。

**架构：**

```python
# backend/observability/metrics.py（S6 新建）
from prometheus_client import Histogram, CONTENT_TYPE_LATEST, generate_latest

# 自定义 buckets：本地 LLM 通常 100ms–2s；云端 200ms–5s
_TTFT_BUCKETS = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, float("inf"))

llm_ttft_seconds = Histogram(
    "llm_ttft_seconds",
    "Time to first token from chat_stream call to first yielded token",
    labelnames=["provider", "model"],
    buckets=_TTFT_BUCKETS,
)

def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
```

**`/metrics` endpoint：**

```python
# backend/main.py（S6 加）
from observability.metrics import render as render_metrics

@app.get("/metrics")
async def metrics(request: Request):
    if not DEV_MODE:
        secret = request.headers.get("x-shared-secret", "")
        if not secrets.compare_digest(secret, SHARED_SECRET):
            return Response(status_code=401)
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
```

**TTFT 计时点（HybridRouter 内）：**

```python
# backend/router/hybrid_router.py（S6 改）
from observability.metrics import llm_ttft_seconds

async def _stream_with_ttft(self, provider, provider_label, messages, **kwargs):
    """Wrap chat_stream to record TTFT. Yields tokens unchanged."""
    t0 = _now()
    first = True
    async for tok in provider.chat_stream(messages, **kwargs):
        if first:
            llm_ttft_seconds.labels(
                provider=provider_label,
                model=getattr(provider, "model", "unknown"),
            ).observe(_now() - t0)
            first = False
        yield tok
```

local 路径和 `_stream_cloud` 都改成走 `_stream_with_ttft(provider, "local"/"cloud", ...)`。

**也包含：** `backend/router/types.py` 创建（含 `BudgetContext` / `BudgetDecision` / `BudgetHook` 占位 + 默认 `_allow_all` 实现），但**不实现 ledger**。HybridRouter `_budget_check` 字段升级到 `BudgetHook | None`（向后兼容 None）。

**新增 perf 脚本：**

```python
# backend/scripts/perf/ttft_cloud.py（S6 新建，仿 ttft_voice.py）
"""Run N rounds of chat_stream against cloud profile, print TTFT distribution."""
# 调 ws/control + force_cloud=True；记录每次 first-token 时间；输出 p50/p95
```

**E2E 自测点：**
1. 启动 backend → `curl -H "x-shared-secret: $SECRET" http://127.0.0.1:8100/metrics` 看到 `llm_ttft_seconds_bucket{...}`
2. 跑 smoke_chat.py 一次 → metrics 里出现 `llm_ttft_seconds_count{provider="local"} 1`
3. 跑 ttft_cloud.py 5 round → p95 数字落在合理区间（< 5s）

---

### 2.3 S7 — Fallback E2E

**目标：** pytest 级 E2E 验证「local 挂 + cloud 挂」 → 用户不会无限阻塞，得到清晰错误；「local 挂 + cloud OK」 → 30s 内 fallback 成功。

**实现策略（Q9=C, Q10=A）：**

不引入 toxiproxy（额外依赖）。用 `httpx.MockTransport` 注入 503，启动 backend 之前 monkeypatch `OpenAICompatibleProvider._test_transport`。

**新文件：**

```python
# backend/tests/test_fallback_e2e.py（S7 新建）
"""
P2-1-S7 fallback E2E — uses httpx.MockTransport to inject cloud 503,
no toxiproxy / docker dependency.

Exercises the full path:
  TestClient(/ws/control) → agent → HybridRouter → cloud (503) → local (OK)
"""
import pytest
from fastapi.testclient import TestClient
from httpx import MockTransport, Response

# fixture: monkeypatch HybridRouter to use 503-injected cloud + healthy local mock
# scenario 1: local healthy + cloud injected 503 → user gets local response
# scenario 2: local injected fail + cloud injected 503 → LLMUnavailableError → echo fallback
# scenario 3: local injected fail + cloud OK → user gets cloud response
# scenario 4: 3x cloud 503 → circuit OPEN → 4th request skips cloud entirely
```

**为什么不在 `tests/e2e/` 放：** 现有 `tests/e2e/test_chat_flow.py` 是「跑起来真 backend 后挂的脚本」，与 pytest fixture 模式不兼容。S7 要的是 fixture-based + CI 友好，所以放 `backend/tests/`。

**关键 fixture 设计：**

```python
@pytest.fixture
def app_with_injected_routes(monkeypatch):
    """Build a fresh FastAPI app whose HybridRouter has mocked local+cloud providers."""
    # 不能直接 import main.py（它在 module-load 时构造 router）—— 改用 factory pattern
    # S7 实现时可能需要先 refactor main.py 露出 build_app() factory，
    # 但要 minimal — 不动 production codepath
```

**E2E 自测点：**
1. `pytest backend/tests/test_fallback_e2e.py -v` 全绿
2. 跑一次完整 `pytest backend/` —— 不应破坏现有 163 个测试
3. CI 集成（如有）跑通

---

### 2.4 S8 — BillingLedger + Budget

**目标：** 记 token 使用 + 算成本 + 超日预算时拒 cloud → fallback local + 前端 toast。

**SQLite schema（Q15=A）：**

```python
# backend/billing/ledger.py（S8 新建）
import aiosqlite
from datetime import date

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS billing_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix REAL NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    cost_cny REAL NOT NULL DEFAULT 0,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_ledger_ts ON billing_ledger(ts_unix);
"""

class BillingLedger:
    def __init__(self, db_path: str, daily_budget_cny: float, pricing: dict):
        self._db = db_path
        self._budget = daily_budget_cny
        self._pricing = pricing  # {model_name: cny_per_1m_tokens}

    async def init(self) -> None: ...

    async def record(self, *, provider, model, input_tokens, output_tokens,
                     total_tokens, session_id) -> float:
        """Insert a row, return cost_cny."""
        cost = self._calc_cost(provider, model, total_tokens)
        ...
        return cost

    async def spent_today_cny(self, tz: str = "Asia/Shanghai") -> float:
        """Sum cost_cny for rows where date(ts_unix in tz) == today in tz."""
        ...

    async def status(self) -> dict:
        spent = await self.spent_today_cny()
        return {
            "date": date.today().isoformat(),
            "spent_cny": round(spent, 4),
            "limit_cny": self._budget,
            "remaining_cny": max(0.0, self._budget - spent),
        }

    def create_hook(self) -> BudgetHook:
        async def hook(ctx: BudgetContext) -> BudgetDecision:
            if ctx.provider == "local":
                return BudgetDecision(allow=True, reason=None)
            spent = await self.spent_today_cny()
            if spent >= self._budget:
                return BudgetDecision(
                    allow=False,
                    reason=f"今日云端预算 ¥{self._budget:.2f} 已用完（已消耗 ¥{spent:.2f}），自动降级到本地",
                )
            return BudgetDecision(allow=True, reason=None)
        return hook
```

**价格表（Q16=A）：**

```toml
# config.toml（S8 加）
[billing.pricing]
# CNY per 1M tokens (output 价格；input 单独算太麻烦，简化按 total 算)
"qwen3.6-plus" = 8.0
"qwen3.6-turbo" = 0.6
"deepseek-chat" = 1.0
"unknown_model_pricing_cny_per_1m" = 20.0  # 保守 fallback
```

**HybridRouter 改动（在 S6 提供的 hook slot 上插实现）：**

```python
# backend/router/hybrid_router.py（S8 改）
async def chat_stream(self, messages, *, ...):
    ...
    # Before each provider call:
    decision = await self._budget_check(BudgetContext(
        provider="cloud", model=self._cloud.model,
        estimated_input_tokens=None,
    ))
    if not decision.allow:
        # fallback to local OR raise if no local
        ...
        # also signal upward via a sentinel for ws/control to pick up:
        self._last_budget_reason = decision.reason
```

**ws/control 把超额 reason 传到前端：**

```python
# backend/main.py（S8 加）
# 在 chat handler 里，stream 完成后：
budget_msg = getattr(llm_engine, "_last_budget_reason", None)
await ws.send_json({
    "type": "chat_response",
    "payload": {
        "text": response_text,
        "budget_exceeded": budget_msg is not None,
        "budget_reason": budget_msg,
    },
})
```

**前端 toast（S8 改 SettingsPanel + ChatHistoryPanel）：**

监听 `chat_response.payload.budget_exceeded` → 一次性 toast「今日云端预算已用完，自动用本地回复」。

**E2E 自测点：**
1. 把日预算改成 0.001 CNY，发一条 cloud chat → toast 出现 + 实际是 local 在跑
2. 查 `data/billing.db` 表里有记录
3. 第二天（fake date）→ 重置成功，能继续 cloud

---

## 3. 并行执行 & merge 策略

### 3.1 worktree 布局

```
G:\projects\deskpet-worktrees\
  ├── s3-api-key/      → branch feat/p2-1-s3-api-key
  ├── s6-ttft/         → branch feat/p2-1-s6-ttft-metrics
  ├── s7-fallback-e2e/ → branch feat/p2-1-s7-fallback-e2e
  └── s8-billing/      → branch feat/p2-1-s8-billing-budget
```

每个 worktree 从 master HEAD（commit `369134a`）切出。

### 3.2 文件归属（避免 merge 冲突）

| 文件 | S3 | S6 | S7 | S8 |
|---|---|---|---|---|
| `backend/router/hybrid_router.py` | – | ✏️ TTFT 包装 + types 导入 | – | ✏️ rebase + 插 budget hook |
| `backend/router/types.py` | – | ➕ 创建 | – | – |
| `backend/router/__init__.py` | – | ✏️ 导出 BudgetHook | – | – |
| `backend/observability/metrics.py` | – | ➕ 创建 | – | – |
| `backend/main.py` | ✏️ provider_test_connection + apiKey env | ✏️ /metrics 路由 | – | ✏️ rebase + budget reason 到 chat_response |
| `backend/providers/openai_compatible.py` | – | – | – | ✏️ stream_options + last_usage |
| `backend/billing/ledger.py` | – | – | – | ➕ 创建 |
| `backend/scripts/perf/ttft_cloud.py` | – | ➕ 创建 | – | – |
| `backend/tests/test_fallback_e2e.py` | – | – | ➕ 创建 | – |
| `backend/config.py` | ✏️ 老 key 迁移 | – | – | ✏️ 加载 [billing.pricing] |
| `config.toml` | – | – | – | ✏️ [billing.pricing] section |
| `tauri-app/src-tauri/src/secrets.rs` | ➕ 创建 | – | – | – |
| `tauri-app/src-tauri/src/lib.rs` | ✏️ register secret commands | – | – | – |
| `tauri-app/src-tauri/Cargo.toml` | ✏️ + keyring crate | – | – | – |
| `tauri-app/src/components/SettingsPanel.tsx` | ➕ 创建（含 budget 占位） | – | – | ✏️ 填 budget 真实数据 + toast |
| `tauri-app/src/App.tsx` | ✏️ 右键菜单加「设置」 | – | – | – |
| `tauri-app/src/types/messages.ts` | ✏️ provider_test_connection 类型 | – | – | ✏️ budget_exceeded 字段 |

**真冲突（必须按顺序 merge）：**
1. `backend/main.py` —— S3 / S6 / S8 都改，但改的位置不同（不同 handler / 不同路由），merge 顺序按 S6→S3→S8 一般可自动解决，必要时 S8 主动 rebase
2. `backend/router/hybrid_router.py` —— S6 / S8 都改，S8 必须在 S6 后 rebase
3. `tauri-app/src/components/SettingsPanel.tsx` —— S3 创建，S8 改字段，S8 在 S3 后 rebase

### 3.3 merge 顺序 & 时机（Q17=A）

```
master (369134a)
  ├── S6 ─── merge 1st  →  master + TTFT + types.py
  ├── S3 ─── merge 2nd  →  master + Settings + secrets
  ├── S7 ─── merge 3rd  →  master + fallback E2E（验证 S6 没破 router）
  └── S8 ─── rebase + merge 4th  →  master + billing
                                     ↑ 主动吃掉 S6 的 router 改 + S3 的 SettingsPanel 占位
```

**为什么 S6 第一个：** 改动最小（router 只加包装函数 + 新文件），先入 master 后让 S8 rebase 时只需吃 router 一处变化。

**为什么 S7 第三个不是最后：** S7 纯加测试，不改 production；先入 master 验证 S6 没破现有 fallback 行为，给 S8 一个干净起点。

**为什么 S8 最后：** 它依赖 S6 的 `BudgetHook` 类型 + S3 的 SettingsPanel 占位；rebase 一次到位。

### 3.4 多 agent 派发（Q18=A）

每个 worktree 派**一个 implementer subagent**（subagent-driven-development 模式，sonnet 模型）。每个 subagent 跑完后由主对话派**两个 reviewer subagent** 串行（spec 合规 → 代码质量）。

**4 个 implementer 同时派**（互不阻塞）；review 在 implementer 完成后串。

---

## 4. Out of scope（明确不做）

| 概念 | 推迟到 |
|---|---|
| 跨平台 keyring（Mac Keychain / Linux Secret Service） | Phase 3（架构已为之准备 —— `keyring` crate 自动适配） |
| OpenAI tiktoken 本地估算 token | 永不做（vendor-agnostic 不可能准） |
| 月度预算 / 单次预算 | 永不做（spec D1-3 已定只设日上限） |
| 多 persona 各自独立预算 | Phase 3（与 PersonaRegistry 一起） |
| `cost_aware` / `latency_aware` 路由策略 | Phase 3 或永久不做（local_first 已覆盖意图） |
| HALF_OPEN race fix | P2-2 prep（多会话才会触发） |
| `/metrics` 暴露 audio pipeline 指标 | P2-2 |
| `BillingLedger` UI 历史浏览面板 | Phase 3 |

---

## 5. 验收清单（merge to master 前）

- [ ] S3：4 个 subagent + 2 个 reviewer 全 PASS；Tauri 启动后能填 key 并保存重启依然在
- [ ] S6：`/metrics` 端点返回 prometheus 格式；TTFT histogram 有真实数据
- [ ] S7：`pytest backend/tests/test_fallback_e2e.py` 全绿；`pytest backend/` 整体 ≥ 163 个 + 新增不破坏现有
- [ ] S8：BillingLedger 表创建；超预算路径触发降级 + 前端 toast；价格表生效
- [ ] **总体：** 跑一次完整 E2E（启 backend + Tauri + 触发 fallback + 看 toast + grep `/metrics` + 看 `data/billing.db`）确认 4 slice 协同 OK
- [ ] 所有 4 个 handoff 文档落到 `docs/superpowers/handoffs/p2-1-s{3,6,7,8}-*.md`
- [ ] STATE.md 更新到 `2026-04-15 (P2-1 finale shipped; P2-1 sprint complete, ready for P2-2)`

---

## 6. 风险

| 风险 | 缓解 |
|---|---|
| `keyring` crate 在 Win11 Insider 上 Credential Manager API 行为变化 | 手动验证一次 + 文档化测试用例 |
| `prometheus_client` 与 FastAPI Response 类型冲突 | 用 `Response(content=bytes, media_type=...)` 显式构造 |
| `MockTransport` 在 streaming 场景下 SSE 响应不能被 httpx 正确解析 | S2 已在 `test_hybrid_router.py::test_router_with_real_providers_routes_to_local_when_healthy` 验证过 mock SSE 可行 |
| Ollama 不返回 usage chunk → BillingLedger 拿 `None` | spec 1.2 已定：local 必记 0；这是预期行为，不是 bug |
| 4 个 worktree 间环境（uv venv / node_modules）重复 | 各 worktree 独立 `uv sync` + `npm install`，磁盘代价可接受（~1.5GB × 4） |
| Subagent 同时改 master 检出的 worktree 时不知道彼此存在 | 不需要知道 —— spec §1 已把接口定死，按文件归属表写各自的代码即可 |

---

## 7. 参考

- 联合 plan 入口（执行用）：见本 spec 同日产出的 4 份 plan
  - `2026-04-15-p2-1-s3-api-key-settings.md`
  - `2026-04-15-p2-1-s6-ttft-metrics.md`
  - `2026-04-15-p2-1-s7-fallback-e2e.md`
  - `2026-04-15-p2-1-s8-billing-budget.md`
- S2 handoff（关键先验）：`docs/superpowers/handoffs/p2-1-s2-hybrid-router.md`
- V6 roadmap：`docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md`
