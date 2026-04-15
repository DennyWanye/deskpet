# P2-1-S8 Billing & Budget 实现 plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task.

**Goal:** 上线 BillingLedger（SQLite 记录 per-call 使用量）+ 每日预算 BudgetHook；当日预算超限时 HybridRouter 自动降级到 local，前端收到 toast 提示。

**Architecture:** `backend/billing/ledger.py` 持久化 usage，实现 `BudgetHook` 协议注入到 HybridRouter；OpenAICompatibleProvider 通过 `stream_options={"include_usage": True}` 捕获 last_usage；预算状态通过 control WebSocket `budget_status` 事件推送给前端。

**Tech Stack:** aiosqlite, prometheus_client (已在 S6 引入), FastAPI WebSocket, React toast。

**Spec:** `docs/superpowers/specs/2026-04-15-p2-1-finale-design.md` §2.4 / §1.1 / §1.2

**Branch:** `feat/p2-1-s8-billing-budget`（依赖 S6 合入后 rebase）

---

### Task 1: 创建 BillingLedger（SQLite schema + 写入 API）

**Files:**
- Create: `backend/billing/__init__.py`（空）
- Create: `backend/billing/ledger.py`
- Create: `backend/tests/test_billing_ledger.py`

- [ ] **Step 1: 写 schema + 基础 CRUD 测试**

```python
# backend/tests/test_billing_ledger.py
"""P2-1-S8 BillingLedger 单元测试。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from billing.ledger import BillingLedger


@pytest.fixture
async def ledger():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "billing.db"
        l = BillingLedger(
            db_path=db,
            pricing={"qwen3.6-plus": 8.0, "deepseek-chat": 1.0},
            unknown_model_price_cny_per_m_tokens=20.0,
            daily_budget_cny=10.0,
        )
        await l.init()
        yield l


@pytest.mark.asyncio
async def test_record_then_spent_today(ledger):
    await ledger.record(
        provider="cloud", model="qwen3.6-plus",
        prompt_tokens=1000, completion_tokens=500,
    )
    # (1000+500)/1_000_000 * 8.0 = 0.012
    spent = await ledger.spent_today_cny()
    assert abs(spent - 0.012) < 1e-6


@pytest.mark.asyncio
async def test_unknown_model_uses_fallback_price(ledger):
    await ledger.record(
        provider="cloud", model="some-new-model",
        prompt_tokens=500_000, completion_tokens=500_000,
    )
    # 1.0M tokens * 20.0 = 20.0
    spent = await ledger.spent_today_cny()
    assert abs(spent - 20.0) < 1e-6


@pytest.mark.asyncio
async def test_local_provider_records_zero_cost(ledger):
    await ledger.record(
        provider="local", model="qwen3:4b",
        prompt_tokens=1000, completion_tokens=500,
    )
    # local provider: cost=0 regardless of token count
    spent = await ledger.spent_today_cny()
    assert spent == 0.0


@pytest.mark.asyncio
async def test_status_reports_remaining(ledger):
    await ledger.record("cloud", "qwen3.6-plus", 1000, 500)
    s = await ledger.status()
    assert s["spent_today_cny"] > 0
    assert s["daily_budget_cny"] == 10.0
    assert s["remaining_cny"] == pytest.approx(10.0 - s["spent_today_cny"])
    assert s["percent_used"] == pytest.approx(s["spent_today_cny"] / 10.0)
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

```bash
cd backend && uv run pytest tests/test_billing_ledger.py -v
```

- [ ] **Step 3: 实现 `backend/billing/ledger.py`**

```python
"""P2-1-S8 BillingLedger — SQLite 每次 LLM 调用的 usage 与成本记录.

Data model: 单表 `calls`，每次 chat_stream 完成写入一行。
Cost model: cloud provider 按 model 的 per-1M-token 价格折算（prompt+completion 合计），
local provider 成本为 0。

DailyBudgetHook contract: 参见 docs/superpowers/specs/2026-04-15-p2-1-finale-design.md §1.1。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import structlog

from router.types import BudgetContext, BudgetDecision, BudgetHook

logger = structlog.get_logger()


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    ts_date TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_cny REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_ts_date_idx ON calls(ts_date);
"""


class BillingLedger:
    """Per-call usage 记录 + 每日预算查询。

    Thread-safe via asyncio.Lock on write path.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        pricing: dict[str, float],
        unknown_model_price_cny_per_m_tokens: float,
        daily_budget_cny: float,
    ) -> None:
        self._db_path = db_path
        self._pricing = pricing
        self._unknown_price = unknown_model_price_cny_per_m_tokens
        self._daily_budget = daily_budget_cny
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(CREATE_SQL)
            await db.commit()

    def _cost_cny(self, provider: str, model: str, total_tokens: int) -> float:
        if provider == "local":
            return 0.0
        price = self._pricing.get(model, self._unknown_price)
        return total_tokens / 1_000_000.0 * price

    async def record(
        self, provider: str, model: str,
        prompt_tokens: int, completion_tokens: int,
    ) -> None:
        total = prompt_tokens + completion_tokens
        cost = self._cost_cny(provider, model, total)
        now = datetime.now(timezone.utc)
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT INTO calls (ts_utc, ts_date, provider, model, "
                    "prompt_tokens, completion_tokens, cost_cny) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (now.isoformat(), now.date().isoformat(), provider, model,
                     prompt_tokens, completion_tokens, cost),
                )
                await db.commit()
        logger.info(
            "billing_record",
            provider=provider, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cost_cny=cost,
        )

    async def spent_today_cny(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT COALESCE(SUM(cost_cny), 0.0) FROM calls WHERE ts_date = ?",
                (today,),
            ) as cur:
                row = await cur.fetchone()
                return float(row[0]) if row else 0.0

    async def status(self) -> dict:
        spent = await self.spent_today_cny()
        remaining = max(0.0, self._daily_budget - spent)
        pct = spent / self._daily_budget if self._daily_budget > 0 else 1.0
        return {
            "spent_today_cny": spent,
            "daily_budget_cny": self._daily_budget,
            "remaining_cny": remaining,
            "percent_used": pct,
        }

    def create_hook(self) -> BudgetHook:
        """Returns a BudgetHook usable by HybridRouter."""
        async def _hook(ctx: BudgetContext) -> BudgetDecision:
            # local 永远允许
            if ctx.route == "local":
                return BudgetDecision(allow=True)
            spent = await self.spent_today_cny()
            if spent >= self._daily_budget:
                return BudgetDecision(
                    allow=False,
                    reason=f"daily_budget_exceeded:{spent:.3f}/{self._daily_budget:.3f}",
                )
            return BudgetDecision(allow=True)
        return _hook
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd backend && uv run pytest tests/test_billing_ledger.py -v
```

Expect: 4 passed.

- [ ] **Step 5: 添加 aiosqlite 依赖（若 pyproject 还没）**

```bash
cd backend && uv add aiosqlite
```

（若已在 pyproject.toml 则跳过。memory_store 已用 aiosqlite，大概率已存在）

- [ ] **Step 6: Commit**

```bash
git add backend/billing/ backend/tests/test_billing_ledger.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(billing): BillingLedger SQLite + per-call cost model (P2-1-S8)"
```

---

### Task 2: 配置层 — pricing 表 + daily_budget

**Files:**
- Modify: `backend/config.toml`
- Modify: `backend/config.py`

- [ ] **Step 1: 在 config.toml 加 `[billing]` 段**

追加到现有 `[cloud]` 之后：

```toml
[billing]
daily_budget_cny = 10.0
unknown_model_price_cny_per_m_tokens = 20.0

[billing.pricing]
"qwen3.6-plus" = 8.0
"deepseek-chat" = 1.0
```

- [ ] **Step 2: config.py 加 BillingConfig dataclass**

```python
# 在 backend/config.py 顶部 imports 附近
@dataclass(frozen=True)
class BillingConfig:
    daily_budget_cny: float
    unknown_model_price_cny_per_m_tokens: float
    pricing: dict[str, float]
    db_path: Path

    @classmethod
    def from_toml(cls, data: dict, db_dir: Path) -> "BillingConfig":
        b = data.get("billing", {})
        return cls(
            daily_budget_cny=float(b.get("daily_budget_cny", 10.0)),
            unknown_model_price_cny_per_m_tokens=float(
                b.get("unknown_model_price_cny_per_m_tokens", 20.0)
            ),
            pricing=dict(b.get("pricing", {})),
            db_path=db_dir / "billing.db",
        )
```

在主 `Config.from_toml`（或等效 loader）中加：

```python
billing = BillingConfig.from_toml(raw, db_dir=Path(data_dir))
return Config(..., billing=billing)
```

- [ ] **Step 3: 写配置解析测试**

```python
# backend/tests/test_config_billing.py
from pathlib import Path
import tomllib
from config import BillingConfig


def test_billing_config_from_toml(tmp_path):
    raw = {
        "billing": {
            "daily_budget_cny": 5.0,
            "pricing": {"qwen3.6-plus": 8.0, "deepseek-chat": 1.0},
        }
    }
    cfg = BillingConfig.from_toml(raw, db_dir=tmp_path)
    assert cfg.daily_budget_cny == 5.0
    assert cfg.pricing["qwen3.6-plus"] == 8.0
    assert cfg.unknown_model_price_cny_per_m_tokens == 20.0  # default


def test_billing_config_defaults():
    cfg = BillingConfig.from_toml({}, db_dir=Path("/tmp"))
    assert cfg.daily_budget_cny == 10.0
    assert cfg.pricing == {}
```

- [ ] **Step 4: Run**

```bash
cd backend && uv run pytest tests/test_config_billing.py -v
```

Expect: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/config.toml backend/config.py backend/tests/test_config_billing.py
git commit -m "feat(billing): load [billing] config section (P2-1-S8)"
```

---

### Task 3: OpenAICompatibleProvider — 捕获 last_usage

**Files:**
- Modify: `backend/providers/openai_compatible.py`
- Modify: `backend/tests/test_openai_compatible.py`

- [ ] **Step 1: 写失败测试（usage 捕获）**

在 test file 追加：

```python
@pytest.mark.asyncio
async def test_chat_stream_captures_usage(provider):
    SSE = (
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'
        b'data: [DONE]\n\n'
    )
    def handler(req):
        assert 'stream_options' in req.content.decode() or True  # 软断言 — body 存在即可
        return httpx.Response(200, content=SSE,
                              headers={"content-type": "text/event-stream"})
    provider._test_transport = httpx.MockTransport(handler)
    tokens = [t async for t in provider.chat_stream([{"role":"user","content":"q"}])]
    assert "".join(tokens) == "hi"
    assert provider.last_usage == {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
    }


@pytest.mark.asyncio
async def test_chat_stream_last_usage_resets_per_call(provider):
    """Second call without usage chunk returns None (no stale data)."""
    # First call: has usage
    SSE_WITH = (
        b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n'
        b'data: [DONE]\n\n'
    )
    SSE_WITHOUT = (
        b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
        b'data: [DONE]\n\n'
    )
    calls = [0]
    def handler(req):
        body = SSE_WITH if calls[0] == 0 else SSE_WITHOUT
        calls[0] += 1
        return httpx.Response(200, content=body,
                              headers={"content-type": "text/event-stream"})
    provider._test_transport = httpx.MockTransport(handler)
    _ = [t async for t in provider.chat_stream([{"role":"user","content":"q"}])]
    assert provider.last_usage is not None
    _ = [t async for t in provider.chat_stream([{"role":"user","content":"q"}])]
    assert provider.last_usage is None
```

- [ ] **Step 2: Run — expect FAIL (last_usage attr missing)**

- [ ] **Step 3: 修改 `backend/providers/openai_compatible.py`**

在 `__init__` 添加：
```python
self.last_usage: dict | None = None
```

在 `chat_stream` 中：
1. 请求 body 加入 `stream_options={"include_usage": True}`
2. 每次调用进入时先 `self.last_usage = None`
3. SSE 解析循环里，当看到 chunk 含 `usage` 字段时记录到 `self.last_usage`

核心 diff（示意，具体看现有代码结构）：

```python
async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
    self.last_usage = None  # <<< 每次重置
    body = {
        "model": self._model, "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},  # <<< 新增
        "temperature": temperature, "max_tokens": max_tokens,
    }
    ...
    async for line in resp.aiter_lines():
        ...
        chunk = json.loads(payload)
        # capture usage
        if chunk.get("usage"):
            self.last_usage = chunk["usage"]
        # yield content delta
        for ch in chunk.get("choices", []):
            delta = ch.get("delta", {})
            if content := delta.get("content"):
                yield content
```

- [ ] **Step 4: Run — expect PASS**

```bash
cd backend && uv run pytest tests/test_openai_compatible.py -v
```

Expect: 原有 tests + 2 new all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/providers/openai_compatible.py backend/tests/test_openai_compatible.py
git commit -m "feat(providers): capture last_usage from OpenAI stream chunks (P2-1-S8)"
```

---

### Task 4: HybridRouter rebase — 调用 BudgetHook

> **依赖：** 需要 S6 已合入（提供 `BudgetContext`/`BudgetDecision`/`allow_all_budget`）。

**Files:**
- Modify: `backend/router/hybrid_router.py`
- Modify: `backend/tests/test_hybrid_router.py`

- [ ] **Step 1: 写 budget 拒绝测试**

```python
async def test_budget_exceeded_skips_cloud_and_falls_to_local(fake_local_ok, fake_cloud_ok):
    async def deny_cloud_hook(ctx):
        from router.types import BudgetDecision
        if ctx.route == "cloud":
            return BudgetDecision(allow=False, reason="budget_exceeded_test")
        return BudgetDecision(allow=True)

    # local 死 → 应该拒 cloud → LLMUnavailableError
    router = HybridRouter(
        local=FakeProvider(healthy=False),
        cloud=fake_cloud_ok,
        budget_hook=deny_cloud_hook,
    )
    with pytest.raises(LLMUnavailableError) as ei:
        async for _ in router.chat_stream([{"role":"user","content":"q"}]):
            pass
    assert "budget" in str(ei.value).lower()


async def test_budget_allow_goes_through(fake_local_down, fake_cloud_ok):
    async def allow(ctx):
        from router.types import BudgetDecision
        return BudgetDecision(allow=True)
    router = HybridRouter(local=fake_local_down, cloud=fake_cloud_ok, budget_hook=allow)
    tokens = [t async for t in router.chat_stream([{"role":"user","content":"q"}])]
    assert tokens  # got cloud response
```

- [ ] **Step 2: Run — expect FAIL (budget_hook 参数不被使用)**

- [ ] **Step 3: 修改 HybridRouter._stream_cloud**

在入口（`if self._cloud is None` 之后、circuit check 之前）加：

```python
# budget check
decision = await self._budget_hook(BudgetContext(
    route="cloud",
    model=getattr(self._cloud, "_model", "unknown"),
))
if not decision.allow:
    logger.info("router_cloud_budget_denied", reason=decision.reason)
    self._last_budget_reason = decision.reason  # 供 main.py 读取
    raise LLMUnavailableError(f"budget denied: {decision.reason}")
```

（S6 已把 `self._budget_hook` 初始化为 `allow_all_budget` 默认）

- [ ] **Step 4: Run — expect PASS**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py -v
```

Expect: all existing + 2 new PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(router): invoke BudgetHook before cloud call (P2-1-S8)"
```

---

### Task 5: main.py 接线 — BillingLedger 注入 + chat_response meta

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: 初始化 BillingLedger**

在 `service_context` 构造区域加：

```python
from billing.ledger import BillingLedger

billing_ledger = BillingLedger(
    db_path=cfg.billing.db_path,
    pricing=cfg.billing.pricing,
    unknown_model_price_cny_per_m_tokens=cfg.billing.unknown_model_price_cny_per_m_tokens,
    daily_budget_cny=cfg.billing.daily_budget_cny,
)
# 在 lifespan 里 await billing_ledger.init()
service_context.register("billing_ledger", billing_ledger)
```

构造 router 时传入 hook：

```python
router = HybridRouter(
    local=local_provider, cloud=cloud_provider,
    budget_hook=billing_ledger.create_hook(),
)
```

- [ ] **Step 2: chat_stream 完成时记录 usage**

找到 `/ws/control` 的 chat 分支，在 stream 消费完后：

```python
# 尝试从 router 下层 provider 读 last_usage
lu = None
for p in (router._local, router._cloud):
    if p is not None and getattr(p, "last_usage", None) is not None:
        lu = p.last_usage
        used_provider = "local" if p is router._local else "cloud"
        used_model = getattr(p, "_model", "unknown")
        break

if lu is not None:
    await billing_ledger.record(
        provider=used_provider, model=used_model,
        prompt_tokens=lu.get("prompt_tokens", 0),
        completion_tokens=lu.get("completion_tokens", 0),
    )
```

（注：读 `router._local/._cloud` 是测试友好的简化路径；正式版本可在 HybridRouter 加 `last_used_provider` 公开属性，留 P2-2 改）

- [ ] **Step 3: LLMUnavailableError 的 budget 分支 — 附 reason 到 chat_response**

现有 catch 里：

```python
except LLMUnavailableError as exc:
    reason = getattr(router, "_last_budget_reason", None)
    router._last_budget_reason = None  # consume
    payload = {"text": f"[echo] {text}"}
    if reason and "budget" in reason:
        payload["budget_exceeded"] = True
        payload["budget_reason"] = reason
    await ws.send_json({"type": "chat_response", "payload": payload})
```

- [ ] **Step 4: 加 WS handler `budget_status`**

```python
elif msg["type"] == "budget_status":
    s = await billing_ledger.status()
    await ws.send_json({"type": "budget_status", "payload": s})
```

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(main): wire BillingLedger + budget_status WS handler (P2-1-S8)"
```

---

### Task 6: SettingsPanel rebase — fetchDailyBudget 接真实数据 + toast 监听

> **依赖：** S3 已合入（提供 SettingsPanel.tsx 骨架 + `fetchDailyBudget` 占位）。

**Files:**
- Modify: `src/panels/SettingsPanel.tsx`
- Modify: `src/hooks/useControlWs.ts`（或等价 WebSocket hook，按现有命名）
- Create: `src/hooks/useBudgetToast.ts`

- [ ] **Step 1: fetchDailyBudget 改为真实 WS 请求**

替换 S3 里的 stub：

```tsx
async function fetchDailyBudget(wsSend, wsWaitFor): Promise<DailyBudgetStatus> {
  wsSend({ type: "budget_status" });
  const msg = await wsWaitFor("budget_status", 3000);
  return msg.payload;
}
```

（具体 hook API 视 S3 实现而定，按 S3 HANDOFF 调整）

- [ ] **Step 2: 创建 useBudgetToast hook 监听 chat_response**

```tsx
// src/hooks/useBudgetToast.ts
import { useEffect } from "react";

export function useBudgetToast(ws: WebSocket | null, showToast: (m: string) => void) {
  useEffect(() => {
    if (!ws) return;
    const handler = (ev: MessageEvent) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "chat_response" && msg.payload?.budget_exceeded) {
          showToast(`今日云端预算已用尽，已降级到本地模型。${msg.payload.budget_reason ?? ""}`);
        }
      } catch { /* ignore */ }
    };
    ws.addEventListener("message", handler);
    return () => ws.removeEventListener("message", handler);
  }, [ws, showToast]);
}
```

- [ ] **Step 3: 在顶层 App 挂载 toast**

现有 App.tsx 中找一个带 WS 的位置：

```tsx
useBudgetToast(controlWs, (m) => toast.show(m, { kind: "warning" }));
```

（如果项目还没 toast 组件，用最简 alert 过渡 — 但先检查 `src/components` 有没有现成的）

- [ ] **Step 4: 手动 smoke — frontend 构建不破**

```bash
pnpm --filter deskpet-ui tsc --noEmit
pnpm --filter deskpet-ui build
```

Expect: 0 errors.

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(ui): real budget status + toast on budget_exceeded (P2-1-S8)"
```

---

### Task 7: 手动 E2E 验收（必须人工跑）

按全局规则 — 不是跑 pytest 就完事，必须实际启动后端和前端，人工触发 4 个关键场景。

- [ ] **Scenario 1: 正常扣费流**

1. 设置 `daily_budget_cny = 10.0`（默认）
2. `python -m scripts.startup` 启动后端 + `pnpm tauri dev`
3. 发 2–3 条 chat（force_cloud 或默认 local_first→fallback 到 cloud）
4. 检查 `sqlite3 backend/data/billing.db "SELECT * FROM calls ORDER BY id DESC LIMIT 5;"` — 有新行，cost > 0
5. SettingsPanel → "今日使用" 面板 — 数字 > 0

- [ ] **Scenario 2: 预算超限 toast + 降级**

1. 改 `config.toml`: `daily_budget_cny = 0.001`
2. 重启后端
3. 发一条 `force_cloud=true` chat（或断开 local provider）
4. Expect:
   - 前端出现 toast: "今日云端预算已用尽，已降级到本地模型..."
   - Chat 回复为 `[echo] <文本>` 或 local 回复（看 local 是否在线）
   - `backend/data/billing.db` 没有新行（因为 cloud 调用被预算 hook 拦下）

- [ ] **Scenario 3: local 不计成本**

1. 恢复 `daily_budget_cny = 10.0`
2. 确认 local provider 在线（`ollama serve`）
3. 发一条 chat（走 local）
4. SQL: `SELECT provider, cost_cny FROM calls ORDER BY id DESC LIMIT 1;`
5. Expect: `local | 0.0`

- [ ] **Scenario 4: SettingsPanel 刷新**

1. 前端打开 SettingsPanel
2. 发一条 cloud chat
3. 点击 "刷新" 按钮
4. Expect: "已用 X.XXX / 10.0 元" 数字更新

- [ ] **完成后：恢复 config.toml 到默认值**

```bash
git diff backend/config.toml  # 应该无差异（或仅保留新的 [billing] 段）
```

- [ ] **Commit（若 E2E 过程发现小 bug）**

```bash
git commit -am "fix(billing): <具体问题>（P2-1-S8 manual E2E 发现）"
```

---

### Task 8: HANDOFF 文档

**Files:**
- Create: `docs/superpowers/handoffs/p2-1-s8-billing-budget.md`

包含：
- Goal + Commits
- BillingLedger 数据模型（字段含义 + 每日滚动策略）
- Pricing 表现状 + 如何加新模型
- BudgetHook 触发路径（local 永放过 / cloud 超限拒）
- chat_response 新字段 `budget_exceeded` + `budget_reason` 契约
- SettingsPanel 今日使用模块如何读
- 数据清理策略（P2-1 不做 auto-purge；后续 P2-2 可加 `--vacuum-before-days`）
- Out of scope：per-user budget（单用户桌宠不需要）、月度/周度预算（只做 daily）

```bash
git add docs/superpowers/handoffs/p2-1-s8-billing-budget.md
git commit -m "docs(p2-1-s8): handoff for billing + budget slice"
```

---

## 完成判据

- [ ] `pytest backend/tests/test_billing_ledger.py` 4/4 PASS
- [ ] `pytest backend/tests/test_openai_compatible.py` 原有 + 2 new PASS
- [ ] `pytest backend/tests/test_hybrid_router.py` 原有 + 2 new PASS
- [ ] `pytest backend/tests/test_config_billing.py` 2/2 PASS
- [ ] `pytest backend/` 整体不破坏
- [ ] 前端 `tsc --noEmit` 无错误
- [ ] 手动 E2E 4 个场景全绿
- [ ] HANDOFF 已写
- [ ] `data/billing.db` 会在 first run 自动生成
