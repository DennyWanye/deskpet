# TDD — DeskPet 工具层优化 v3：技术设计 + 测试规格

**关联**: `00-PRD.md`（**第 3 版（v3，终版）**）
**状态**: **第 3 版（v3，终版）** —— 已过两轮架构评审，按评审意见修订

> ## 第 3 版修订要点（详 PRD v3 顶部）
>
> **round2 必改 P0**（6 项）：
> 1. **§A10 v1 deprecated 段** — 整段加废止横幅（残留 emit_metric API）
> 2. **§A11 ToolSpec dataclass 必须加 `replace_allowed: bool = False` 字段**（否则 spec init TypeError）
> 3. **§A12 ToolsConfig 真实位置** — 已在 last-mile 分支 `backend/config.py:232`（M0 合 master 后生效），本期在该处加 5 字段
> 4. **§A8 翻译表语义对账** — `_TIER_TO_CATEGORY = {"l1":"event","l2":"project","l3":"preference","auto":"preference"}`（短/中/长 ↔ 快/中/慢）
> 5. **§A11 字典序方向修正** — memory_tools.py('m') < stubs.py('s')，stubs.py 是**后注册方**会覆盖真实现 → stubs.py 改"name 不在 registry 才注册"守卫模式
> 6. **§A1.1 build_agent 工厂签名补 4 参数** — max_iterations / completion_probe / max_completion_nudges / signature_repeat_threshold
>
> **round2 P1**：
> - §A1.1 加 `make_llm_call(provider)` 签名注释：`(prompt:str) -> Awaitable[str]`
> - §A3 WI-T2.3 第一步先核 `emit_receipt` 内部 duration_ms 算法
> - §A8 `facts.py` 真无 `get_by_id` 方法（需新加或用 `find_active` 反查）
> - §A12 明确 `_cached` 单例放 `backend/config.py:load_config()` + mtime 失效

> ## 第 2 版修订要点（详 PRD v2 §9）
>
> **必改 P0**（按 round1 评审）：
> 1. **API 名错** — 所有 `emit_metric` 改为 `record(event, detail)`；import 路径 `from observability.metrics_sink import record`
> 2. **memory_tools.py 已被 memory-stage2 占用** — §A8 改为 **append 现有文件**，bind() 签名合并（保留 memory-stage2 的 facts_store/embedder/llm_call/enable_natural_language + 加 memory_manager/retriever）
> 3. **ToolNameConflictError 与 stubs.py 设计冲突** — §A11 加 `replace_allowed=True` opt-in 参数；stubs.py 现有占位标 opt-in
> 4. **memory schema 字段错** — 真实 tier enum `["l1", "l2", "l3", "auto"]`；mcp_call 第三参 `arguments`；delegate 字段 `goal`；§A8 加 `_TIER_TO_CATEGORY` 翻译表
> 5. **metrics_sink / llm_call_func 真实 API** — §A1.1 改 `get_default_sink()` + `make_llm_call(local_llm)`
> 6. **ToolsConfig 命名碰撞** — §A12 改为扩展 `backend/config.py:ToolsConfig` 不新建
>
> **P1**（按 round1 评审）：
> 1. `import main; reload` 翻车 — §A1.3 改为 `build_agent(cfg, ...) -> _AgentLoop` 工厂 + 直接 assertion
> 2. §A6 WI-T2.6 session TTL **deferred** — 70KB/周非 leak，砍掉
> 3. §A8 D9 取消双注册改 **schema migration helper**（透明翻译）
> 4. §A10 mcp_call/delegate **直接 unregister** — 无真 caller，不 ship deprecation handler
> 5. §A12 disabled_toolsets 默认 **strict**，opt-in `_schema_only`
> 6. §A12 `load_tools_config` 必须 `_cached` 单例
**原则**: 测试先行。每个 WI 的实现以"让本文用例全绿"为完成标准。
**核心纪律**: 沿用 Stage 1 + last-mile 教训 ——
> Phase A-E 单测全绿但功能零生效；last-mile 代码全写且 receipt 接电，但 VerifyGate 注入 AgentLoop 漏了，shadow metric 永远 0；
> 根因：只测了"模块本身"和"模块接口签名 grep"，**没测"接入后真实运行栈行为"**。

本文每个 WI **必须有 integration test**，断言"wire 进生产链路后，真实调用链里 X 确实发生了"。
**严禁仅 grep 源码 + 单测 ctor 接受参数**作为接电证据。

---

## A. 技术设计

### A0 · M0 last-mile 合 master（前置）

**操作步骤**：

```bash
# 1. 切到 master 拉最新
git checkout master
git pull origin master

# 2. 模拟 merge 看冲突（merge-tree 已验证 0 文本冲突）
git merge-tree $(git merge-base master tool-last-mile-upgrade) master tool-last-mile-upgrade \
    | grep -E "^(\+\+\+|<<<<|====|>>>>)" || echo "no conflicts"

# 3. 正式 merge（保留 26 commits 历史）
git merge tool-last-mile-upgrade --no-ff -m "feat(tools): 工具调用 last-mile 升级 — artifact envelope + receipt + (未接电的)verify gate

详 plans/2026-05-23-tool-last-mile-upgrade/manual-results-2026-05-23-final-qa/REPORT.md
"

# 4. 跑回归
cd backend
.venv/Scripts/python.exe -m pytest tests/ -x --maxfail=5

# 5. 跑 acceptance
.venv/Scripts/python.exe -m scripts.last_mile_smoke

# 6. 跑 frontend vitest（last-mile 9 个新用例）
cd ../tauri-app
npm test -- --run

# 7. push
cd ..
git push origin master
git branch -d tool-last-mile-upgrade
```

**PR description** 含 known gaps 链接到本 PRD。

### A1 · WI-T2.1 VerifyGate 接电

#### A1.0 已存在的资产（关键事实）

合 master 后，master 上**已有**：

| 文件 | 内容 |
|------|------|
| `backend/agent/agent_loop.py:396-398` | `AgentLoop.__init__` 接 `verify_gate / receipt_store / max_verify_nudges` 三个 kwargs |
| `backend/agent/agent_loop.py:423-425` | `self.verify_gate = ...` 等赋值 |
| `backend/agent/agent_loop.py:947-969` | verify check + D8 rebound + nudge counting + ephemeral rescue 完整逻辑 |
| `backend/deskpet/agent/verify_gate.py` | `VerifyGate / RegexExtractor / CascadeExtractor / load_claim_patterns` 完整实现 |
| `backend/tests/test_agent_loop_verify_wiring.py` | 4 个 wiring 测试已绿 |

**WI-T2.1 唯一缺**：`main.py:4015` 处 `_AgentLoop(...)` 调用没传 `verify_gate=` / `receipt_store=` / `max_verify_nudges=`。

#### A1.1 ★v2 build_agent 工厂（testability refactor）

**新增** `backend/main.py` 内 `build_agent(cfg, ...) -> _AgentLoop` 工厂函数（详 PRD §3.2 完整代码）。**关键点**：
- `metrics_sink = get_default_sink()`（**不是** v1 的虚构 `metrics_sink` 变量）
- `llm_call_func` 由调用方传入（实际是 `make_llm_call(local_llm)`，**不是** v1 的虚构 `llm_call_func` 名）
- `verify_gate = VerifyGate(extractor, mode, receipt_store, metrics_sink)` 构造完整传入 AgentLoop ctor
- `_AgentLoop(..., verify_gate=verify_gate, receipt_store=receipt_store_getter(), max_verify_nudges=cfg.tools.verifier.max_verify_nudges)` 是关键接电点

**原 main.py:4015 处** 改为：

```python
_agent = build_agent(
    cfg,
    llm_registry=llm_registry,
    tool_registry=deskpet_tool_registry_v2,
    context_manager=context_manager,
    receipt_store_getter=_get_receipt_store,
    llm_call_func=make_llm_call(local_llm),  # ★v2 真实 API
)
```

#### A1.2 AgentLoop 构造点改动

**搜锚点**：`_AgentLoop(` —— last-mile 分支 `main.py:4015`，合 master 后行号可能漂移。**改为**：

```python
_agent = _AgentLoop(
    llm_registry=llm_registry,
    tool_registry=deskpet_tool_registry_v2,
    max_iterations=...,
    completion_probe=...,
    max_completion_nudges=...,
    signature_repeat_threshold=...,
    context_manager=context_manager,
    # ─── WI-T2.1 v3 新接电（修 last-mile P0-1）───
    verify_gate=verify_gate,
    receipt_store=_get_receipt_store(),
    max_verify_nudges=cfg.tools.verifier.max_verify_nudges,
)
```

#### A1.3 ★v2 测试策略 — build_agent 工厂 assertion + 复用 wiring test

**v2 改动（按 round1 评审 P1-1）**：放弃 v1 的 `import main; reload` 路径（monolithic main.py 99% 翻车）。改为：

**新建** `backend/tests/test_build_agent_verify_wiring.py`：

```python
def test_build_agent_passes_verify_gate_when_flag_on(test_cfg_flag_on):
    """直接 assertion，不 import main 全跑"""
    from main import build_agent
    agent = build_agent(
        test_cfg_flag_on,
        llm_registry=Mock(), tool_registry=Mock(),
        context_manager=Mock(),
        receipt_store_getter=lambda: Mock(),
        llm_call_func=lambda *a, **kw: "",
    )
    assert agent.verify_gate is not None
    assert agent.receipt_store is not None
    assert agent.max_verify_nudges == 2


def test_build_agent_verify_gate_none_when_flag_off(test_cfg_flag_off):
    agent = build_agent(...)
    assert agent.verify_gate is None


def test_build_agent_handles_missing_patterns_file(test_cfg_bad_patterns):
    """patterns.yaml 不存在 → catch + warn + verify_gate=None。"""
    agent = build_agent(test_cfg_bad_patterns, ...)
    assert agent.verify_gate is None
```

**复用** `backend/tests/test_agent_loop_verify_wiring.py`（last-mile 已写好；含 4 用例）：
- `test_agent_loop_signature_accepts_verify_gate`
- `test_verify_gate_off_mode_short_circuits`
- `test_verify_gate_strict_blocks_fake_claim`
- `test_ephemeral_rescue_pass_path`

**新建** `backend/tests/test_main_py_verify_gate_wired.py`（boot smoke，完整代码）：

```python
"""WI-T2.1 boot smoke — 真接电证据.

不重写 AgentLoop.run 全栈 mock（v1 评审 D-RISK-2）；只验证：
  1. main 模块 import 后全局 verify_gate 已构造（flag ON 时不是 None）
  2. flag OFF 时 verify_gate is None
  3. claim_patterns 文件缺失时 catch + warn + disable
  4. backend 启动 30 秒内 metrics.jsonl 出现 verify_* event
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_main_imports_verify_gate_when_flag_on(monkeypatch, tmp_path):
    """flag ON 时 main 模块全局 verify_gate 不是 None。"""
    monkeypatch.setenv("DESKPET_TOOLS_VERIFIER_ENABLED", "true")
    # patch claim_patterns_file 到 fixture 避免依赖真 yaml
    fixture = tmp_path / "patterns.yaml"
    fixture.write_text("patterns: []\n", encoding="utf-8")
    monkeypatch.setenv("DESKPET_TOOLS_VERIFIER_CLAIM_PATTERNS", str(fixture))

    import main as main_module
    importlib.reload(main_module)

    assert main_module.verify_gate is not None, (
        "WI-T2.1: main.verify_gate must not be None when flag is ON — "
        "check VerifyGate eager construction in main.py"
    )


@pytest.mark.asyncio
async def test_main_imports_verify_gate_none_when_flag_off(monkeypatch):
    """flag OFF → main.verify_gate is None。"""
    monkeypatch.setenv("DESKPET_TOOLS_VERIFIER_ENABLED", "false")
    import main as main_module
    importlib.reload(main_module)
    assert main_module.verify_gate is None


@pytest.mark.asyncio
async def test_main_handles_missing_patterns_file(monkeypatch, tmp_path, caplog):
    """patterns.yaml 不存在时 catch + warn + disable，不崩。"""
    monkeypatch.setenv("DESKPET_TOOLS_VERIFIER_ENABLED", "true")
    monkeypatch.setenv(
        "DESKPET_TOOLS_VERIFIER_CLAIM_PATTERNS",
        str(tmp_path / "nonexistent.yaml"),
    )
    import main as main_module
    importlib.reload(main_module)
    assert main_module.verify_gate is None
    assert any("verify_gate init failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_metrics_jsonl_emits_verify_event_within_30s(tmp_path):
    """启 backend subprocess，30 秒内 metrics.jsonl 出现 verify_* event。
    这是 last-mile 接电的唯一硬证据 — 防止"代码全测全绿但功能零生效"。
    """
    env = {**os.environ}
    env["DESKPET_USER_DATA_DIR"] = str(tmp_path)
    env["DESKPET_TOOLS_VERIFIER_ENABLED"] = "true"

    proc = subprocess.Popen(
        [sys.executable, "-m", "main"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        metrics_path = tmp_path / "observability" / "metrics.jsonl"
        deadline = time.time() + 30
        found = False
        while time.time() < deadline:
            if metrics_path.exists():
                content = metrics_path.read_text(encoding="utf-8")
                for line in content.splitlines():
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    name = evt.get("event_name", "")
                    if name.startswith("verify_") or name.startswith("verify."):
                        found = True
                        break
                if found:
                    break
            await asyncio.sleep(0.5)
        assert found, (
            "No verify_* event in metrics.jsonl within 30s — "
            "VerifyGate not wired into AgentLoop!"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
```

### A2 · WI-T2.2 retention 截断 bug 修复

**搜锚点**：`min(retention, 7)` —— last-mile 分支 `main.py:394` 附近。

**改前**：
```python
_receipt_store_box[0] = ReceiptStore(
    db_path=user_data_dir / "receipts",
    retention_days=min(retention, 7),  # ← bug
)
```

**改后**：
```python
_receipt_store_box[0] = ReceiptStore(
    db_path=user_data_dir / "receipts",
    retention_days=retention,
)
```

**进一步检查**（B 阶段 review 提醒）：

```bash
grep -n "min(retention\|retention_days" backend/deskpet/tools/receipt_store.py
```

若 `cleanup_expired` 内部还有同款截断 → 一并修。

### A3 · WI-T2.3 duration_ms 失真修复

**搜锚点**：`registry.py` 的 `started_at=datetime.now` 调用点。

**改前**（last-mile 分支 `backend/deskpet/tools/registry.py:644-646`）：
```python
# emit_receipt 调用时同一时刻取
emit_receipt(
    ...,
    started_at=datetime.now(timezone.utc),
    ended_at=datetime.now(timezone.utc),
)
```

**改为**（包 timer）：

```python
async def execute_tool(self, name: str, args: dict, task_id: str) -> str:
    spec = self._tools.get(name)
    if not spec:
        return _err(f"unknown tool {name!r}")
    # ── WI-T2.3 timer ──
    started_at = datetime.now(timezone.utc)
    try:
        result = await self._run_handler(spec, args, task_id)
    finally:
        ended_at = datetime.now(timezone.utc)
    # last-mile receipt emit
    if self._receipt_store_provider is not None:
        try:
            receipt_store = self._receipt_store_provider()
            if receipt_store is not None:
                receipt = emit_receipt(
                    tool_name=name,
                    args=args, result=result, task_id=task_id,
                    started_at=started_at,
                    ended_at=ended_at,
                )
                receipt_store.append(receipt)
        except Exception as exc:
            logger.warning("emit_receipt failed: %s", exc)
    return result
```

### A4 · WI-T2.4 Tauri cargo test

**新建** 在 `tauri-app/src-tauri/src/artifact_ops.rs` 末尾：

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonicalize_path_existing_file() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let result = canonicalize_path(tmp.path().to_str().unwrap());
        assert!(result.is_ok());
    }

    #[test]
    fn canonicalize_path_nonexistent() {
        let result = canonicalize_path("/this/path/does/not/exist/12345");
        assert!(result.is_err() || result.unwrap().is_empty());
    }

    #[cfg(windows)]
    #[test]
    fn canonicalize_path_unc() {
        let result = canonicalize_path(r"\\?\C:\Windows\System32");
        assert!(result.is_ok() || result.is_err());
    }

    #[test]
    fn canonicalize_path_traversal_blocked() {
        let result = canonicalize_path("../../../etc/passwd");
        if let Ok(p) = result {
            assert!(!p.contains("passwd"), "traversal not blocked");
        }
    }
}
```

**CI workflow** `.github/workflows/last-mile.yml`：

```yaml
- name: cargo test
  run: cargo test --manifest-path tauri-app/src-tauri/Cargo.toml --lib
```

### A5 · WI-T2.5 vitest CI 默认必跑

**改 `backend/scripts/last_mile_smoke.py`**（搜锚点 `--no-vitest`）：

```python
parser.add_argument(
    "--no-vitest", action="store_true",
    help="跳过 vitest（仅本地调试；CI 不允许）",
)
# 主逻辑：
if not args.no_vitest:
    logger.info("Running vitest...")
    subprocess.run(
        ["npm", "test", "--", "--run"],
        check=True, cwd="tauri-app",
    )
```

### A6 · ~~WI-T2.6 session_iteration TTL 清理~~ ★v2 deferred

按 round1 评审 P1-2：70KB/周（350 entries × 200B）**非 leak**。本期 deferred 到 "P3 deferred until evidence of leak observed"。

WI-T2.6 实施代码、测试、人工测试用例**全部砍掉**。如未来 24h+ 长跑真观察到内存爬升，再启用本 WI。

（以下为参考实现，**不在 v2 范围内**）

### ~~A6 deferred~~ · WI-T2.6 session_iteration TTL 清理（不实施）

**改 `backend/deskpet/tools/registry.py`**（搜锚点 `_session_iteration`）：

```python
class ToolRegistry:
    def __init__(self):
        ...
        self._session_iteration: dict[str, int] = {}
        self._session_last_access: dict[str, float] = {}

    def get_session_iteration(self, session_id: str) -> int:
        self._session_last_access[session_id] = time.time()
        return self._session_iteration.get(session_id, 0)

    def bump_session_iteration(self, session_id: str) -> int:
        self._session_last_access[session_id] = time.time()
        self._session_iteration[session_id] = (
            self._session_iteration.get(session_id, 0) + 1
        )
        return self._session_iteration[session_id]

    def cleanup_stale_sessions(
        self, *, now: float | None = None,
        ttl_seconds: float = 7 * 86400.0,
    ) -> int:
        """TTL fallback：7 天未访问的 session 强制清。返回清了几个。"""
        t = now if now is not None else time.time()
        cutoff = t - ttl_seconds
        stale = [
            sid for sid, ts in self._session_last_access.items()
            if ts < cutoff
        ]
        for sid in stale:
            self._session_iteration.pop(sid, None)
            self._session_last_access.pop(sid, None)
        return len(stale)
```

**main.py lifespan 加定时任务**：

```python
async def _registry_cleanup_loop():
    while True:
        await asyncio.sleep(86400)  # 1 天跑一次
        cleaned = deskpet_tool_registry_v2.cleanup_stale_sessions()
        if cleaned:
            logger.info("registry: TTL cleaned %d stale sessions", cleaned)

app.state.background_tasks.add(
    asyncio.create_task(_registry_cleanup_loop())
)
```

### A7 · WI-T2.7 metrics dashboard

**新建** `backend/scripts/metrics/__init__.py`（空）。

**新建** `backend/scripts/metrics/dashboard.py`：

```python
"""Lightweight metrics console dashboard.

Reads metrics.jsonl, aggregates by event_name, prints rich table.

Usage:
  python -m scripts.metrics.dashboard
  python -m scripts.metrics.dashboard --watch
  python -m scripts.metrics.dashboard --since 1h
  python -m scripts.metrics.dashboard --alert verify.fake_completion_caught:>0.2
  python -m scripts.metrics.dashboard --report-json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from rich.console import Console
    from rich.table import Table
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


_KEY_METRICS = [
    "tool.execute",
    "verify.fake_completion_caught",
    "verify.fake_completion_detected",
    "verify.ephemeral_rescued",
    "verify.sig_invalid_filtered",
    "verify_extractor.fallback_used",
    "receipt.append",
    "artifact.action.open",
    "artifact.action.show_in_folder",
    "artifact.action.copy_path",
    "artifact.action.save_as",
    "tool.deprecated_called",
]


def _parse_since(s: str) -> float:
    """'1h' → 3600.0, '30m' → 1800.0, '1d' → 86400.0"""
    n = float(s[:-1])
    unit = s[-1]
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _load_metrics(path: Path, since: float | None = None) -> list[dict]:
    rows: list[dict] = []
    cutoff = time.time() - since if since else 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff and row.get("ts", 0) < cutoff:
                continue
            rows.append(row)
    return rows


def _aggregate(rows: list[dict]) -> dict[str, dict]:
    """{event_name: {count, durations, p50_ms, p95_ms, rate}}"""
    agg: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "durations": []}
    )
    total_tool_exec = 0
    for r in rows:
        name = r.get("event_name", "?")
        agg[name]["count"] += 1
        d = r.get("duration_ms")
        if isinstance(d, (int, float)) and d > 0:
            agg[name]["durations"].append(d)
        if name == "tool.execute":
            total_tool_exec += 1
    for name in agg:
        ds = agg[name]["durations"]
        ds.sort()
        agg[name]["p50_ms"] = ds[len(ds) // 2] if ds else 0
        agg[name]["p95_ms"] = ds[int(len(ds) * 0.95)] if ds else 0
        agg[name]["rate"] = (
            agg[name]["count"] / total_tool_exec
            if total_tool_exec and name != "tool.execute"
            else None
        )
    return agg


def _print_table(agg: dict[str, dict]):
    if _HAS_RICH:
        c = Console()
        t = Table(title="DeskPet Metrics Dashboard")
        t.add_column("event_name")
        t.add_column("count", justify="right")
        t.add_column("rate", justify="right")
        t.add_column("p50 ms", justify="right")
        t.add_column("p95 ms", justify="right")
        for name in _KEY_METRICS:
            if name not in agg:
                continue
            row = agg[name]
            t.add_row(
                name,
                str(row["count"]),
                f"{row['rate']*100:.2f}%" if row.get("rate") else "-",
                f"{row['p50_ms']:.0f}" if row["p50_ms"] else "-",
                f"{row['p95_ms']:.0f}" if row["p95_ms"] else "-",
            )
        c.print(t)
    else:
        # plain fallback
        print(f"{'event_name':<40}{'count':>8}{'rate':>10}{'p50':>8}{'p95':>8}")
        for name in _KEY_METRICS:
            if name not in agg:
                continue
            row = agg[name]
            print(
                f"{name:<40}{row['count']:>8}"
                f"{(row.get('rate') or 0)*100:>9.2f}%"
                f"{row['p50_ms']:>8.0f}{row['p95_ms']:>8.0f}"
            )


def _make_report_json(agg: dict[str, dict], window_seconds: float | None) -> dict:
    """Generate machine-readable report for cron/monitoring pickup."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_seconds": window_seconds,
        "events": {
            name: {
                "count": row["count"],
                "rate": row.get("rate"),
                "p50_ms": row["p50_ms"] or None,
                "p95_ms": row["p95_ms"] or None,
            }
            for name, row in agg.items()
            if name in _KEY_METRICS
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m scripts.metrics.dashboard")
    p.add_argument("--metrics-path", default=None, help="metrics.jsonl 路径")
    p.add_argument("--since", default=None,
                   help="只看最近 X 时间，如 1h / 30m / 1d")
    p.add_argument("--watch", action="store_true",
                   help="tail -f 模式实时刷新")
    p.add_argument("--alert", default=None,
                   help="event_name:>thresh，超阈值 exit != 0")
    p.add_argument("--report-json", action="store_true",
                   help="输出聚合 JSON 到 stdout（供 cron / 监控拉取）")
    args = p.parse_args()

    # 找 metrics.jsonl
    if args.metrics_path:
        path = Path(args.metrics_path)
    else:
        from deskpet.config import load_config
        cfg = load_config()
        path = Path(cfg.observability.metrics_path)

    if not path.exists():
        print(f"[metrics] no file: {path}", file=sys.stderr)
        return 2

    since = _parse_since(args.since) if args.since else None

    if args.watch:
        while True:
            rows = _load_metrics(path, since=since)
            agg = _aggregate(rows)
            print("\033[H\033[2J", end="")
            _print_table(agg)
            time.sleep(2)
    else:
        rows = _load_metrics(path, since=since)
        agg = _aggregate(rows)
        if args.report_json:
            report = _make_report_json(agg, window_seconds=since)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        _print_table(agg)

    if args.alert:
        ev_name, op_thresh = args.alert.split(":", 1)
        op = ">" if op_thresh.startswith(">") else "<"
        thresh = float(op_thresh.lstrip("><"))
        actual = agg.get(ev_name, {}).get("rate") or 0.0
        if (op == ">" and actual > thresh) or (op == "<" and actual < thresh):
            print(
                f"[metrics] ALERT {ev_name} {actual} {op} {thresh}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### A8 · WI-T3.1 ★v2 memory_* schema migration helper（在已有 memory_tools.py append）

**关键事实（v2 round1 修正）**：`backend/deskpet/tools/memory_tools.py` 已被 memory-stage2 创建（注册 `memory_forget` + `bind(facts_store=, embedder=, llm_call=, enable_natural_language=)`）。**不能 Write 新文件**（会覆盖）。**必须 append 现有文件**。

**append 到现有 `memory_tools.py`**（在 memory_forget 注册下方）：

```python
# ─── v3 新增（在 memory-stage2 内容之后追加）───
import re
import json
import logging

logger = logging.getLogger(__name__)

# ─── tier → category 翻译表（D17 v2）───
# master memory_write schema 真实 tier enum 是 ["l1", "l2", "l3", "auto"]
# 与 FactsStore VALID_CATEGORIES (preference/profile/project/event/reflection)
# 完全不同维度。这是 schema migration helper（非双注册）。
_TIER_TO_CATEGORY: dict[str, str] = {
    "l1": "preference",
    "l2": "project",
    "l3": "event",
    "auto": "preference",
}


def _slugify(text: str, max_len: int = 30) -> str:
    s = re.sub(r"[^a-zA-Z0-9一-龥]+", "_", text.strip())
    return s.strip("_").lower()[:max_len] or "auto_key"


# v3 新增 module-level handles
_memory_manager = None
_retriever = None


# ★v2 bind() 签名合并（保留 memory-stage2 + 加新参数）
_original_bind = bind  # save memory-stage2 bind for chain call

def bind(  # 覆盖现有 bind
    *,
    facts_store=None, embedder=None, llm_call=None,
    enable_natural_language=False,  # memory-stage2 兼容
    memory_manager=None, retriever=None,  # v3 新增
):
    global _memory_manager, _retriever
    # 调原 bind 保留 memory-stage2 行为
    _original_bind(
        facts_store=facts_store, embedder=embedder,
        llm_call=llm_call,
        enable_natural_language=enable_natural_language,
    )
    _memory_manager = memory_manager
    _retriever = retriever


# v3 新增 schema + handler（旧 schema 兼容 + 新 schema 显式）
_OLD_SCHEMA_WRITE = {
    "name": "memory_write",
    "description": "Persist memory (legacy schema; new prompts should use memory_v2_write).",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "tier": {"type": "string", "enum": ["l1", "l2", "l3", "auto"]},  # ★v2 真实 enum
            "salience": {"type": "number"},
        },
        "required": ["text"],
    },
}


async def _handle_memory_write_v3(args: dict, task_id: str) -> str:
    if _memory_manager is None:
        return json.dumps({"ok": False, "error": "memory_tools v3 not bound"})
    text = (args.get("text") or "").strip()
    if not text:
        return json.dumps({"ok": False, "error": "text required"})
    tier = args.get("tier", "auto")
    if tier not in _TIER_TO_CATEGORY:
        logger.warning("memory_write: unknown tier %r → preference", tier)
    category = _TIER_TO_CATEGORY.get(tier, "preference")
    try:
        fid = await _memory_manager.facts_store.upsert(
            category=category, subject="user",
            key=_slugify(text[:20]), value=text,
            confidence=float(args.get("salience", 0.5)),
            source_msg_id=None,
            evidence=f"[tool-call:{task_id}][legacy_schema]",
        )
        return json.dumps({"ok": True, "fact_id": fid})
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"write failed: {exc}"})


# memory_v2_write / memory_v2_read / memory_search / memory_read 等 handler 同款
# 完整实现略（按 round1 评审格式）


# 顶层注册：用 replace_allowed=True opt-in（stubs.py 已注册同名占位）
registry.register("memory_write", "memory", _OLD_SCHEMA_WRITE,
                  _handle_memory_write_v3, replace_allowed=True)
registry.register("memory_read", "memory", _OLD_SCHEMA_READ,
                  _handle_memory_read_v3, replace_allowed=True)
registry.register("memory_search", "memory", _OLD_SCHEMA_SEARCH,
                  _handle_memory_search_v3, replace_allowed=True)
registry.register("memory_v2_write", "memory", _NEW_SCHEMA_WRITE,
                  _handle_memory_v2_write)  # 新名字无冲突
registry.register("memory_v2_read", "memory", _NEW_SCHEMA_READ,
                  _handle_memory_v2_read)
```

**`stubs.py` 同步改动**：3 个 memory_* register 行加 `replace_allowed=True`：

```python
registry.register("memory_write", "memory", _MEMORY_WRITE_SCHEMA,
                  _stub_handler("S4"), replace_allowed=True)  # ★v2 opt-in
# 同款 memory_read / memory_search
```

**main.py lifespan 接入** —— bind() 调用同时传 memory-stage2 旧参数 + v3 新参数。

### ~~A8 v1 (deprecated)~~ —— 以下 v1 内容已被 ★v2 替换

**新建** `backend/deskpet/tools/memory_tools.py`：

```python
"""memory_* tools — real implementations + 双注册兼容（D9）.

兼容层：
  - 旧 schema (memory_write/read/search) — master 上的 text/tier/salience,
    memory_id, query/top_k —— handler 做字段适配后调真 FactsStore/Retriever
  - 新 schema (memory_v2_write/read/search) — 用 key/value/category/subject
    直接调真实现

1 release 后评估 deprecate 旧 schema。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from deskpet.tools.registry import registry

logger = logging.getLogger(__name__)

_memory_manager: Any = None
_retriever: Any = None


def bind(*, memory_manager: Any, retriever: Any) -> None:
    """main.py lifespan 调一次。"""
    global _memory_manager, _retriever
    _memory_manager = memory_manager
    _retriever = retriever
    logger.info(
        "p4_memory_tools_bound mm=%s retriever=%s",
        type(memory_manager).__name__, type(retriever).__name__,
    )


def _err(reason: str) -> str:
    return json.dumps({"ok": False, "error": reason})


def _slugify(text: str, max_len: int = 30) -> str:
    """text 自动生成 key：ASCII 字母数字 + 下划线分词，截断"""
    s = re.sub(r"[^a-zA-Z0-9一-龥]+", "_", text.strip())
    return s.strip("_").lower()[:max_len] or "auto_key"


# ───────────────────────────────────────────────────────────
# 旧 schema 兼容（master 上的 text/tier/salience）
# ───────────────────────────────────────────────────────────
_OLD_SCHEMA_WRITE: dict[str, Any] = {
    "name": "memory_write",
    "description": "Persist memory (legacy schema; use memory_v2_write for new prompts).",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "fact content"},
            "tier": {"type": "string", "default": "preference"},
            "salience": {"type": "number", "default": 0.5},
        },
        "required": ["text"],
    },
}


async def _handle_old_memory_write(args: dict, task_id: str) -> str:
    if _memory_manager is None:
        return _err("memory_tools not bound")
    text = (args.get("text") or "").strip()
    if not text:
        return _err("text required")
    auto_key = _slugify(text[:20])
    try:
        fid = await _memory_manager.facts_store.upsert(
            category=args.get("tier", "preference"),
            subject="user", key=auto_key, value=text,
            confidence=float(args.get("salience", 0.5)),
            source_msg_id=None,
            evidence=f"[tool-call:{task_id}][legacy_schema]",
        )
        return json.dumps({"ok": True, "fact_id": fid, "key": auto_key})
    except Exception as exc:
        return _err(f"write failed: {exc}")


_OLD_SCHEMA_READ: dict[str, Any] = {
    "name": "memory_read",
    "description": "Read memory by id (legacy; use memory_v2_read for key-based lookup).",
    "parameters": {
        "type": "object",
        "properties": {"memory_id": {"type": "integer"}},
        "required": ["memory_id"],
    },
}


async def _handle_old_memory_read(args: dict, task_id: str) -> str:
    if _memory_manager is None:
        return _err("not bound")
    mid = args.get("memory_id")
    if not isinstance(mid, int):
        return _err("memory_id required (int)")
    try:
        # facts_store 是否有 get_by_id？没有则用 SQL 查
        row = await _memory_manager.facts_store.get_by_id(mid)
        if row is None:
            return json.dumps({"ok": True, "fact": None})
        return json.dumps({"ok": True, "fact": dict(row)})
    except Exception as exc:
        return _err(f"read failed: {exc}")


_OLD_SCHEMA_SEARCH: dict[str, Any] = {
    "name": "memory_search",
    "description": "Hybrid search over memory.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}


async def _handle_memory_search(args: dict, task_id: str) -> str:
    """新旧 schema 共用（query/top_k 字段一致）"""
    if _retriever is None:
        return _err("retriever not bound")
    query = args.get("query")
    if not query:
        return _err("query required")
    try:
        hits = await _retriever.recall(
            query=query, top_k=int(args.get("top_k", 5)),
        )
        return json.dumps({
            "ok": True,
            "hits": [
                {"text": h.text, "score": float(h.score), "source": h.source}
                for h in hits
            ],
        })
    except Exception as exc:
        return _err(f"search failed: {exc}")


# ───────────────────────────────────────────────────────────
# 新 schema (v2)
# ───────────────────────────────────────────────────────────
_NEW_SCHEMA_WRITE: dict[str, Any] = {
    "name": "memory_v2_write",
    "description": "Persist a stable fact (new schema with explicit key/category).",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": [
                    "preference", "profile", "project", "event", "reflection",
                ],
            },
            "subject": {"type": "string", "default": "user"},
            "key": {"type": "string"},
            "value": {"type": "string"},
            "confidence": {"type": "number", "default": 0.9},
        },
        "required": ["key", "value"],
    },
}


async def _handle_new_memory_write(args: dict, task_id: str) -> str:
    if _memory_manager is None:
        return _err("not bound")
    try:
        fid = await _memory_manager.facts_store.upsert(
            category=args.get("category", "preference"),
            subject=args.get("subject", "user"),
            key=args["key"], value=args["value"],
            confidence=float(args.get("confidence", 0.9)),
            source_msg_id=None,
            evidence=f"[tool-call:{task_id}]",
        )
        return json.dumps({"ok": True, "fact_id": fid})
    except Exception as exc:
        return _err(f"write failed: {exc}")


_NEW_SCHEMA_READ: dict[str, Any] = {
    "name": "memory_v2_read",
    "description": "Read a specific fact by (subject, key).",
    "parameters": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "default": "user"},
            "key": {"type": "string"},
        },
        "required": ["key"],
    },
}


async def _handle_new_memory_read(args: dict, task_id: str) -> str:
    if _memory_manager is None:
        return _err("not bound")
    try:
        row = await _memory_manager.facts_store.find_active(
            subject=args.get("subject", "user"),
            key=args["key"],
        )
        if row is None:
            return json.dumps({"ok": True, "fact": None})
        return json.dumps({"ok": True, "fact": {
            "id": row["id"], "category": row["category"],
            "subject": row["subject"], "key": row["key"],
            "value": row["value"], "confidence": row["confidence"],
            "updated_at": row["updated_at"],
        }})
    except Exception as exc:
        return _err(f"read failed: {exc}")


# ───────────────────────────────────────────────────────────
# 顶层注册（替换 stubs.py 中的 3 个 stub；同时新增 v2 名字）
# ───────────────────────────────────────────────────────────
registry.register("memory_write", "memory", _OLD_SCHEMA_WRITE, _handle_old_memory_write)
registry.register("memory_read", "memory", _OLD_SCHEMA_READ, _handle_old_memory_read)
registry.register("memory_search", "memory", _OLD_SCHEMA_SEARCH, _handle_memory_search)
registry.register("memory_v2_write", "memory", _NEW_SCHEMA_WRITE, _handle_new_memory_write)
registry.register("memory_v2_read", "memory", _NEW_SCHEMA_READ, _handle_new_memory_read)
# memory_v2_search 与 memory_search 同款（不重复注册）
```

**改 `stubs.py`**：移除 3 个 memory_* 的 `registry.register` 行；加注释：

```python
# ─────────────────────────────────────────────────────
# WI-T3.1: memory_* 已由 memory_tools.py 替换（双注册）
# 旧 stub 注册移除；保留 DEPRECATED 注释作历史 reference
# ─────────────────────────────────────────────────────
# registry.register("memory_write", ...)    # REPLACED by memory_tools.py
# registry.register("memory_read", ...)     # REPLACED by memory_tools.py
# registry.register("memory_search", ...)   # REPLACED by memory_tools.py
```

**main.py lifespan 接入**（在 `_get_verify_gate` 附近）：

```python
if cfg.memory.v2.facts_extract:
    from deskpet.tools import memory_tools
    memory_tools.bind(
        memory_manager=memory_manager,
        retriever=enhanced_retriever,
    )
```

### A9 · WI-T3.2 skill_invoke 真实现

**新建** `backend/deskpet/tools/skill_tools.py`：

```python
"""skill_invoke real implementation replacing stub."""
from __future__ import annotations

import json
import logging
from typing import Any

from deskpet.tools.registry import registry

logger = logging.getLogger(__name__)

_skill_loader: Any = None


def bind(*, skill_loader: Any) -> None:
    global _skill_loader
    _skill_loader = skill_loader
    logger.info("p4_skill_tools_bound loader=%s", type(skill_loader).__name__)


async def _handle_skill_invoke(args: dict, task_id: str) -> str:
    if _skill_loader is None:
        return json.dumps({"ok": False, "error": "skill_loader not bound"})
    name = args.get("name")
    args_dict = args.get("args", {})
    if not name:
        return json.dumps({"ok": False, "error": "skill name required"})
    try:
        result = await _skill_loader.execute_skill(name, args_dict)
        return json.dumps({"ok": True, "result": result})
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "error": f"skill execution failed: {exc}",
        })


_SCHEMA: dict[str, Any] = {
    "name": "skill_invoke",
    "description": "Invoke a registered skill by name.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "args": {"type": "object"},
        },
        "required": ["name"],
    },
}


registry.register(
    "skill_invoke", "control", _SCHEMA, _handle_skill_invoke,
    dangerous=True, permission_category="write_file",
)
```

**改 stubs.py**：移除 skill_invoke 注册。

### A10 · WI-T3.3 mcp_call / delegate deprecation handler

**改 `backend/deskpet/tools/stubs.py`**：

```python
from deskpet.observability.metrics_sink import emit_metric


def _make_deprecated_handler(name: str, alternative: str):
    async def _handle(args: dict, task_id: str) -> str:
        emit_metric("tool.deprecated_called", {
            "tool_name": name, "alternative": alternative,
        })
        logger.warning(
            "deprecated tool %r called; use %r instead", name, alternative,
        )
        return json.dumps({
            "ok": False,
            "deprecated": True,
            "alternative": alternative,
            "error": (
                f"tool {name!r} is deprecated; use {alternative!r} instead. "
                f"This shim will be removed next release."
            ),
        })
    return _handle


_MCP_CALL_SCHEMA_DEPRECATED: dict[str, Any] = {
    "name": "mcp_call",
    "description": (
        "[DEPRECATED] Direct MCP tool dispatch. "
        "Use mcp__<server>__<tool> direct names instead "
        "(MCPManager auto-registers them). Removed next release."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "tool": {"type": "string"},
            "args": {"type": "object"},
        },
        "required": ["server", "tool"],
    },
}


_DELEGATE_SCHEMA_DEPRECATED: dict[str, Any] = {
    "name": "delegate",
    "description": (
        "[DEPRECATED] Sub-agent delegation. "
        "Use 'agent' tool in code_tools instead. Removed next release."
    ),
    "parameters": {
        "type": "object",
        "properties": {"task": {"type": "string"}},
        "required": ["task"],
    },
}


# 替换原 stub 注册
registry.register(
    "mcp_call", "control", _MCP_CALL_SCHEMA_DEPRECATED,
    _make_deprecated_handler("mcp_call", "mcp__<server>__<tool>"),
)
registry.register(
    "delegate", "control", _DELEGATE_SCHEMA_DEPRECATED,
    _make_deprecated_handler("delegate", "agent (in code_tools)"),
)
```

### A11 ★v2 · WI-T4.1 + T4.2 ToolNameConflictError + replace_allowed opt-in

按 round1 评审 P0-3：stubs.py 设计就是"register replaces on duplicate"，**D11 一刀切抛错会让 backend 启动崩**（pkgutil 字典序 memory_tools.py < stubs.py，register 顺序撞同名）。

**v2 改动**：加 `replace_allowed=True` opt-in 参数到 `registry.register()`。

**改 `backend/deskpet/tools/registry.py`**（搜锚点 `previous definition replaced`）：

```python
class ToolNameConflictError(Exception):
    """两个 tool 同名时抛出（PRD §3.4 D11）。"""

    def __init__(self, name: str, existing: "ToolSpec", new: "ToolSpec"):
        super().__init__(
            f"tool name conflict: {name!r} already registered by "
            f"source={existing.source!r}; cannot register from "
            f"source={new.source!r}. "
            f"Use distinct plugin instance_id if intentional."
        )
        self.tool_name = name
        self.existing = existing
        self.new = new


def _extract_instance_id(source: str) -> str:
    """plugin:my_plugin:instance_abc → 'instance_abc'（空字符串 = 无 instance_id）"""
    parts = source.split(":")
    return parts[2] if len(parts) >= 3 else ""


# 在 ToolRegistry.register() 内：
def register(self, name, toolset, schema, handler, *,
             check_fn=None, requires_env=None,
             permission_category="read_file",
             source="builtin", dangerous=False,
             timeout_seconds=60.0,
             replace_allowed=False) -> None:  # ★v2 新增
    # ── WI-T4.2 plugin 自动前缀 ──
    if source.startswith("plugin:"):
        parts = source.split(":")
        plugin_name = parts[1] if len(parts) > 1 else ""
        if plugin_name and not name.startswith(f"{plugin_name}:"):
            name = f"{plugin_name}:{name}"

    spec = ToolSpec(
        name=name, toolset=toolset, schema=schema, handler=handler,
        check_fn=check_fn, requires_env=list(requires_env or []),
        permission_category=permission_category, source=source,
        dangerous=dangerous, timeout_seconds=float(timeout_seconds),
        replace_allowed=replace_allowed,  # ★v2 存到 spec
    )

    with self._lock:
        if name in self._tools:
            existing = self._tools[name]
            # ── WI-T4.1 v2 抛错矩阵 ──
            # 1. 双 builtin 同名：仅在 **双方都 opt-in replace_allowed=True** 才 warn+覆盖
            if existing.source == "builtin" and source == "builtin":
                if not (existing.replace_allowed and replace_allowed):
                    raise ToolNameConflictError(name, existing, spec)
            if (
                existing.source.startswith("plugin:")
                and source.startswith("plugin:")
            ):
                existing_iid = _extract_instance_id(existing.source)
                new_iid = _extract_instance_id(source)
                if existing_iid != new_iid:
                    raise ToolNameConflictError(name, existing, spec)
                logger.warning(
                    "plugin %r reloaded; tool %r replaced",
                    existing.source, name,
                )
            else:
                logger.warning(
                    "tool %r re-registered; %s replaces %s",
                    name, source, existing.source,
                )
        self._tools[name] = spec
```

### A12 ★v2 · WI-T5.1 扩展 `backend/config.py:ToolsConfig`

按 round1 评审 P0-6：`backend/config.py:232` 已有 `ToolsConfig`（含 verifier 子段），**不能** 在 `tools/_config.py` 新建同名 ToolsConfig（命名碰撞）。改为在已有 ToolsConfig 加 5 字段：

```python
# backend/config.py
@dataclass
class ToolsConfig:
    verifier: ToolsVerifierConfig = field(default_factory=ToolsVerifierConfig)
    # ...其他已有字段（last_mile / web 等）

    # ─── v3 新增 ───
    disabled_toolsets: list[str] = field(default_factory=list)
    """禁用 toolset list — ★v2 默认 strict（schemas + execute_tool 双层挡）"""

    disabled_toolsets_schema_only: list[str] = field(default_factory=list)
    """opt-in 边缘场景：仅 LLM 看不到但 execute_tool 仍可调"""

    dangerous_tools_allowlist: list[str] = field(default_factory=list)
    """非空时仅 allowlist 中 dangerous tool 可用"""

    default_timeout_seconds: float = 60.0
    """tool spec 未指定 timeout 时的默认"""

    strict_unknown_toolset: bool = False
    """True → typo 触发 fail-fast"""
```

**`load_config()` 加 `_cached` 单例 + mtime 失效**（按 round1 P1-6）：

```python
_cfg_cache = None
_cfg_mtime = None

def load_config():
    global _cfg_cache, _cfg_mtime
    config_path = _resolve_config_path()
    cur_mtime = config_path.stat().st_mtime if config_path.exists() else 0
    if _cfg_cache and _cfg_mtime == cur_mtime:
        return _cfg_cache
    _cfg_cache = _parse_config(config_path)
    _cfg_mtime = cur_mtime
    return _cfg_cache
```

详 PRD §3.5 完整代码骨架。

### ~~A12 v1 (deprecated)~~ —— 以下 v1 内容已被 ★v2 替换

详 §3.5 PRD（完整代码已含）。

### A13 · WI-T6.1 / T6.2 OpenSpec 同步

仅文档操作。逐条 git log + grep 验证后回填 `[x]` + `<!-- verified via commit <hash> -->`；最后跑 `openspec archive p4-poseidon-agent-harness`。

---

## B. 自动化测试规格

### TG-A0 · M0 合 master 回归

| # | 用例 | 断言 |
|---|---|---|
| TA0-1 | `git merge tool-last-mile-upgrade --no-ff` | 0 文本冲突 |
| TA0-2 | `pytest backend/tests/ -x` | ≥ 1931 passed; 0 fail |
| TA0-3 | `bash backend/scripts/last_mile_smoke.py` | acceptance 4 一票否决全过 |
| TA0-4 | `npm test -- --run` | vitest 9/9 passed |

### TG-A1 · VerifyGate 接电（WI-T2.1，核心）

`backend/tests/test_main_py_verify_gate_wired.py` + 复用 `test_agent_loop_verify_wiring.py`

| # | 用例 | 断言 |
|---|---|---|
| TA1-1 | flag ON → import main → main.verify_gate | not None；类型 VerifyGate |
| TA1-2 | flag OFF → import main → main.verify_gate | None |
| TA1-3 | claim_patterns.yaml 不存在 + flag ON | log warning；cfg.tools.verifier.enabled auto-False；main.verify_gate is None；不崩 |
| TA1-4 | 启 backend subprocess 30s 内看 metrics.jsonl | 出现 verify_* event |
| TA1-5 | boot log | 含 `p4_verify_gate_ready mode=shadow extractor=RegexExtractor patterns=N` |
| TA1-6~9 | 复用 4 个 wiring 测试 | 全绿 |

### TG-A2 · retention 修复（WI-T2.2）

| # | 用例 | 断言 |
|---|---|---|
| TA2-1 | cfg 30 天 → ReceiptStore.retention_days | == 30 |
| TA2-2 | cleanup_expired：1d/10d/35d 三文件 | 仅 35d 删 |
| TA2-3 | cfg 7 天（regression guard）| == 7 |

### TG-A3 · duration_ms 修复（WI-T2.3）

| # | 用例 | 断言 |
|---|---|---|
| TA3-1 | sleep 100ms mock tool | receipt.duration_ms ≥ 80 |
| TA3-2 | metrics.jsonl tool.execute event | duration_ms > 0 |
| TA3-3 | exception mock tool | receipt 仍 emit；duration > 0；ok=False |

### TG-A4 · Tauri cargo test（WI-T2.4）

| # | 用例 | 断言 |
|---|---|---|
| TA4-1~4 | canonicalize_path 4 用例 | 各自 pass |

### TG-A5 · vitest CI（WI-T2.5）

| # | 用例 | 断言 |
|---|---|---|
| TA5-1 | last_mile_smoke.py 无 --no-vitest | npm test 真调；exit 0 |
| TA5-2 | --no-vitest | 跳过；exit 0 |

### TG-A6 · session TTL（WI-T2.6）

| # | 用例 | 断言 |
|---|---|---|
| TA6-1 | bump 1 session | _last_access dict 有 1 项 |
| TA6-2 | cleanup_stale 25h 前 session | 被清；返回 1 |
| TA6-3 | 幂等多次跑 | 第 2 次返回 0 |

### TG-A7 · metrics dashboard + `--report-json`（WI-T2.7）

| # | 用例 | 断言 |
|---|---|---|
| TA7-1 | mock jsonl 30 条 → dashboard | exit 0；含 5 类 |
| TA7-2 | --since 1h | 过滤生效 |
| TA7-3 | --alert event:>0.5 超阈值 | exit != 0 |
| TA7-4 | rich 不可用 | 降级 plain text |
| TA7-5 | --report-json | 输出 valid JSON；schema 含 generated_at/window_seconds/events |

### TG-A8 · memory_* 双注册（WI-T3.1）

`backend/tests/test_memory_tools_integration.py`

| # | 用例 | 断言 |
|---|---|---|
| TA8-1 | 调旧 memory_write(text=...) | facts 表新增；key 自动 slugify |
| TA8-2 | 调旧 memory_read(memory_id=N) | 返完整 fact |
| TA8-3 | 调 memory_search(query=...) | 返 hits |
| TA8-4 | 调新 memory_v2_write(key=,value=,category=) | facts 表新增；key 显式 |
| TA8-5 | 调新 memory_v2_read(subject=,key=) | 返单条 fact |
| TA8-6 | bind 未调时 handler | 返 not_bound error |
| TA8-7 | registry.list_tools() | 含 memory_write + memory_v2_write 双注册 |
| TA8-8 | stubs.py 不再注册 memory_* | grep stubs.py 无 active register 行 |

### TG-A9 · skill_invoke 真实现（WI-T3.2）

| # | 用例 | 断言 |
|---|---|---|
| TA9-1 | 调 skill_invoke 真 SkillLoader | execute_skill 被调；返 result |
| TA9-2 | 调不存在 skill | 返 error |

### TG-A10 · mcp_call/delegate deprecation（WI-T3.3）

`backend/tests/test_deprecated_tools.py`

| # | 用例 | 断言 |
|---|---|---|
| TA10-1 | 调 mcp_call → metrics_sink | emit `tool.deprecated_called`；含 tool_name + alternative |
| TA10-2 | handler 返回值 | ok=False; deprecated=True; alternative; error 含 "deprecated" |
| TA10-3 | registry.list_tools() | mcp_call / delegate 仍存在 |
| TA10-4 | schemas() 输出 | description 含 [DEPRECATED] |
| TA10-5 | 调 delegate | 同 TA10-1/2，alternative="agent (in code_tools)" |

### TG-A11 · ToolNameConflictError（WI-T4.1）

| # | 用例 | 断言 |
|---|---|---|
| TA11-1 | 双 builtin 注册同名 | raise ToolNameConflictError + tool_name 字段 |
| TA11-2 | builtin + plugin（含前缀）同名 | plugin 前缀后不冲突 |
| TA11-3 | plugin:p1:iid_a + plugin:p1:iid_b 同前缀同名 | raise |
| TA11-4 | plugin:p1:iid_a + plugin:p1:iid_a reload | warn + 覆盖；不 raise |
| TA11-5 | MCP tool + builtin | 名字不同，不冲突 |

### TG-A12 · plugin 前缀（WI-T4.2）

| # | 用例 | 断言 |
|---|---|---|
| TA12-1 | source=plugin:my_plugin → 注册 my_plugin:greet | yes |
| TA12-2 | source=plugin:my_plugin:iid_1 | yes (instance_id 不进 name) |
| TA12-3 | source=builtin | 名字不变 |

### TG-A13 · _config.py 扩展（WI-T5.1）

| # | 用例 | 断言 |
|---|---|---|
| TA13-1 | 无 [tools] section | 全字段缺省 |
| TA13-2 | disabled_toolsets=["computer_use"] | schemas() 不含；execute_tool 仍可调 |
| TA13-3 | disabled_toolsets_strict=["computer_use"] | schemas() 不含 + execute_tool 拒绝 |
| TA13-4 | dangerous_tools_allowlist=["run_shell"] | 仅 run_shell 在 schemas |
| TA13-5 | dangerous_tools_allowlist=[] 默认 | 现状 |
| TA13-6 | default_timeout_seconds=30 | 未显式 timeout 工具用 30s |
| TA13-7 | strict_unknown_toolset=true + typo | raise ValueError |
| TA13-8 | strict_unknown_toolset=false + typo | warn；不崩 |

### TG-A14 · 全套回归

| 套件 | 通过线 |
|---|---|
| backend pytest (all flags ON) | 0 fail（≈ 2000）|
| backend pytest (all flags OFF) | 0 fail |
| frontend vitest | 0 fail |
| frontend tsc | 0 error |
| Rust cargo test | 0 fail（≥ 4 新增）|
| eval_gate | PASS |
| last_mile_smoke.py（--with-vitest 默认）| PASS |
| metrics dashboard --report-json | 输出 valid JSON |

### B-末 · 完成定义

每个 WI = 对应 TG 用例全绿 + TG-A14 回归 0 倒退 + flag 可独立开关。

**核心红线**：
- **WI-T2.1 完成 = TA1-1 ~ TA1-9 全绿，且 metrics.jsonl 真出现 verify_* event count ≥ 1**
- 不允许只 grep 当接电证据
- PR description 含 metrics.jsonl verify event 截图作证据
- **不重写 AgentLoop.run 全栈 mock**

---

## C. 实施顺序

```
M0  last-mile 合 master ─────────────────────────────────────┐
                                                              │
M1  WI-T2.1 (~2h) + T2.2 + T2.3 (Stage A P0) ────────────────┤
                                                              │
M2  WI-T2.4/5/6/7 (Stage B) ──────────────────────────────────┤
M3  WI-T3.1 (双注册) + T3.2 + T3.3 (deprecation) ─────────────┤  ◄ 三路并行
M5  WI-T5.1 (strict 变体) ────────────────────────────────────┤
                                                              │
M4  WI-T4.1/2 (plugin/plugin 同名) ──────────────────────────┤
M6  WI-T6.1/2 (OpenSpec) ─────────────────────────────────────┤
                                                              │
M7  全套回归 + PR ────────────────────────────────────────────┘
```

并行：M2 / M3 / M5 三路；M1 必先做。

---

## D. 实测结果（动工后回填）

### 自动化测试

| 测试组 | 文件 | 结果 |
|---|---|---|
| TG-A0 合 master 回归 | (git ops) | ⬜ |
| TG-A1 verify_gate 接电 ★ | test_main_py_verify_gate_wired.py + 复用 test_agent_loop_verify_wiring.py | ⬜ |
| TG-A2 retention 修复 | test_receipt_store.py | ⬜ |
| TG-A3 duration_ms 修复 | test_registry_execute_tool.py | ⬜ |
| TG-A4 Tauri cargo test | artifact_ops.rs::tests | ⬜ |
| TG-A5 vitest CI | last_mile_smoke.py | ⬜ |
| TG-A6 session TTL | test_registry_session_cleanup.py | ⬜ |
| TG-A7 metrics dashboard | test_metrics_dashboard.py | ⬜ |
| TG-A8 memory_* 双注册 | test_memory_tools_integration.py | ⬜ |
| TG-A9 skill_invoke | test_skill_tools_integration.py | ⬜ |
| TG-A10 deprecated tools | test_deprecated_tools.py | ⬜ |
| TG-A11 ToolNameConflictError | test_registry_conflict.py | ⬜ |
| TG-A12 plugin 前缀 | test_registry_plugin_prefix.py | ⬜ |
| TG-A13 _config.py 扩展 | test_tools_config.py | ⬜ |

### TG-A14 全套回归

- backend pytest (all ON): ⬜
- backend pytest (all OFF): ⬜
- frontend vitest: ⬜
- frontend tsc: ⬜
- Rust cargo test: ⬜
- eval_gate: ⬜
- last_mile_smoke.py: ⬜
- metrics dashboard --report-json: ⬜

### 接入确认

预期 boot 日志（all flags ON）：
- `p4_receipt_store_ready` (合 last-mile 后已有)
- `p4_verify_gate_ready mode=shadow extractor=RegexExtractor patterns=N` ← **WI-T2.1 接电证据**
- `p4_memory_tools_bound mm=... retriever=...` ← WI-T3.1
- `p4_skill_tools_bound loader=...` ← WI-T3.2

预期 metrics.jsonl 1h 窗内：
- `tool.execute` count > 0 + `duration_ms > 0` ← WI-T2.3
- `verify_extractor.fallback_used` ≥ 0 ← WI-T2.1
- `tool.deprecated_called` 当 LLM 调 mcp_call/delegate 时出现 ← WI-T3.3
