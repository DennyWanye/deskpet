"""P2-1 finale Phase 3 smoke — S7 fallback + S8 budget denial, both
exercised at the HybridRouter level directly (no WS), since enabling
a real cloud endpoint in config.toml for every E2E run isn't practical.

Scenarios:
  A. local-fails + cloud-works          → chat_stream yields cloud tokens
  B. local-works + cloud-disabled       → chat_stream yields local tokens
  C. local-fails + cloud-disabled       → LLMUnavailableError
  D. local-fails + budget-exhausted     → LLMUnavailableError(budget_reason=...)
"""
from __future__ import annotations

import asyncio
import sys
from typing import AsyncIterator

from billing.ledger import BillingLedger
from router.hybrid_router import HybridRouter, LLMUnavailableError, RoutingStrategy


class _FakeProvider:
    """Minimal LLMProvider stub for direct HybridRouter tests."""

    def __init__(self, label: str, *, healthy: bool = True, tokens: list[str] | None = None,
                 raises_on_stream: bool = False) -> None:
        self.model = f"fake-{label}"
        self._label = label
        self._healthy = healthy
        self._tokens = tokens or [f"{label}-token"]
        self._raises = raises_on_stream

    async def health_check(self) -> bool:
        return self._healthy

    async def chat_stream(
        self, messages, *, temperature: float = 0.7, max_tokens: int = 2048
    ) -> AsyncIterator[str]:
        if self._raises:
            raise RuntimeError(f"{self._label}: simulated connection refused")
        for t in self._tokens:
            yield t


async def scenario_a_local_fail_cloud_works() -> str:
    local = _FakeProvider("local", raises_on_stream=True)
    cloud = _FakeProvider("cloud", tokens=["hi", " from", " cloud"])
    router = HybridRouter(local=local, cloud=cloud, strategy=RoutingStrategy.LOCAL_FIRST)
    text = ""
    async for tok in router.chat_stream([{"role": "user", "content": "x"}]):
        text += tok
    return text


async def scenario_b_local_only() -> str:
    local = _FakeProvider("local", tokens=["hi", " local"])
    router = HybridRouter(local=local, cloud=None, strategy=RoutingStrategy.LOCAL_FIRST)
    text = ""
    async for tok in router.chat_stream([{"role": "user", "content": "x"}]):
        text += tok
    return text


async def scenario_c_both_dead() -> str:
    local = _FakeProvider("local", raises_on_stream=True)
    router = HybridRouter(local=local, cloud=None, strategy=RoutingStrategy.LOCAL_FIRST)
    try:
        async for _ in router.chat_stream([{"role": "user", "content": "x"}]):
            pass
    except LLMUnavailableError as e:
        return f"raised: {e}"
    return "did not raise"


async def scenario_d_budget_denied(tmpdb) -> str:
    ledger = BillingLedger(
        db_path=tmpdb,
        pricing={"fake-cloud": 100.0},
        unknown_model_price_cny_per_m_tokens=100.0,
        daily_budget_cny=0.0,  # always denied
    )
    await ledger.init()
    local = _FakeProvider("local", raises_on_stream=True)
    cloud = _FakeProvider("cloud", tokens=["should never see this"])
    router = HybridRouter(
        local=local, cloud=cloud,
        strategy=RoutingStrategy.LOCAL_FIRST,
        budget_hook=ledger.create_hook(),
    )
    try:
        async for _ in router.chat_stream([{"role": "user", "content": "x"}]):
            pass
    except LLMUnavailableError as e:
        return f"raised(budget_reason={e.budget_reason!r})"
    return "did not raise"


async def main() -> int:
    import tempfile, pathlib

    results = []

    text = await scenario_a_local_fail_cloud_works()
    results.append(("A. local-fails → cloud fallback", text == "hi from cloud", repr(text)))

    text = await scenario_b_local_only()
    results.append(("B. local-only (no cloud configured)", text == "hi local", repr(text)))

    msg = await scenario_c_both_dead()
    ok = msg.startswith("raised:") and "cloud provider not configured" in msg
    results.append(("C. both dead → LLMUnavailableError", ok, msg[:120]))

    with tempfile.TemporaryDirectory() as d:
        dbpath = pathlib.Path(d) / "billing.db"
        msg = await scenario_d_budget_denied(dbpath)
    ok = "budget_reason=" in msg and "daily_budget_exceeded" in msg
    results.append(("D. local-fail + zero budget → budget_reason propagated", ok, msg[:140]))

    width = max(len(n) for n, _, _ in results)
    print("\n=== P2-1 E2E Phase 3 (HybridRouter fallback + budget scenarios) ===")
    for name, passed, detail in results:
        mark = "OK  " if passed else "FAIL"
        print(f"  [{mark}] {name.ljust(width)}  | {detail}")
    failed = [n for n, p, _ in results if not p]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
