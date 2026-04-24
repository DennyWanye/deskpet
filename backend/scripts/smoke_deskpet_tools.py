"""P4-S5 smoke script: boot the ToolRegistry, verify all MVP tools are
registered, and exercise a file round-trip + mocked web_fetch.

Meant to be run manually after merging this slice (``python
backend/scripts/smoke_deskpet_tools.py``) and by the Lead agent as part
of the verification before committing.

Exit codes:
  0 — all checks passed (prints ``[OK] <N> tools registered, smoke passed``)
  1 — registry incomplete / file round-trip broken / web_fetch broken

We mock httpx inside this script so the smoke test does NOT require
internet access — ``web_fetch`` gets a synthetic 200/HTML response.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


# Ensure ``backend/`` is on sys.path so ``deskpet.*`` imports resolve
# regardless of where the script is invoked from.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _die(msg: str) -> None:
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main() -> int:
    # Workspace lives in a tmp dir so the smoke doesn't touch real
    # %APPDATA%\deskpet\workspace\.
    tmp = Path(tempfile.mkdtemp(prefix="deskpet_smoke_"))
    os.environ["DESKPET_WORKSPACE_DIR"] = str(tmp)
    os.environ["DESKPET_TODO_PATH"] = str(tmp / "todo.json")

    # Import AFTER env setup so the tools pick up tmp paths.
    import httpx  # noqa: E402

    from deskpet.tools import registry  # noqa: E402
    from deskpet.tools import web_tools  # noqa: E402

    # 1) Registry count — MVP spec: >=16 tools (file 4 + web 4 + todo 2
    #    + stubs 7 + tool_search 1 = 18 total; count >= 16 satisfies spec).
    names = registry.list_tools()
    print(f"[1/4] registered tools ({len(names)}):")
    for name in names:
        spec = registry.get(name)
        print(f"        - {name:<24} toolset={spec.toolset if spec else '?'}")
    if len(names) < 16:
        _die(f"expected ≥16 registered tools, got {len(names)}")

    required = {
        "file": {"file_read", "file_write", "file_glob", "file_grep"},
        "web": {"web_fetch", "web_crawl", "web_extract_article", "web_read_sitemap"},
        "todo": {"todo_write", "todo_complete"},
        "memory": {"memory_write", "memory_read", "memory_search"},
        "control": {"delegate", "skill_invoke", "mcp_call", "tool_search"},
    }
    present = set(names)
    for toolset, expected in required.items():
        missing = expected - present
        if missing:
            _die(f"toolset '{toolset}' missing: {sorted(missing)}")

    # 2) File round-trip — write → read → grep.
    print("[2/4] file_write + file_read round-trip …")
    wres = json.loads(
        registry.dispatch(
            "file_write",
            {"path": "smoke.md", "content": "# hello\nworld\nsmoke ok\n"},
        )
    )
    if "error" in wres:
        _die(f"file_write failed: {wres}")
    rres = json.loads(registry.dispatch("file_read", {"path": "smoke.md"}))
    if rres.get("content", "").splitlines()[0] != "# hello":
        _die(f"file_read round-trip mismatch: {rres!r}")
    gres = json.loads(
        registry.dispatch(
            "file_grep", {"pattern": "^smoke", "path": "smoke.md"}
        )
    )
    if gres.get("count") != 1:
        _die(f"file_grep missed the 'smoke' line: {gres!r}")
    print("        ok")

    # 3) web_fetch — mocked transport (no internet needed).
    print("[3/4] web_fetch (mocked) …")
    real_client = httpx.Client

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            text="<html><title>smoke</title><body>hi from smoke</body></html>",
            headers={"content-type": "text/html"},
        )

    def _fake(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(_handler), **kwargs)

    web_tools.httpx.Client = _fake
    # Short-circuit rate limiting so the smoke doesn't wait 500ms/host.
    from deskpet.tools._config import WebToolsConfig

    web_tools.load_web_config = lambda: WebToolsConfig(
        user_agent="DeskPet/smoke",
        respect_robots_txt=False,
        request_interval_ms=0,
    )
    fres = json.loads(
        registry.dispatch("web_fetch", {"url": "https://smoke.example/x"})
    )
    if fres.get("status") != 200 or "smoke" not in fres.get("content", ""):
        _die(f"web_fetch mock failed: {fres!r}")
    print("        ok")

    # 4) tool_search sanity — ensure lazy-lookup finds file tools.
    print("[4/4] tool_search finds file_* …")
    sres = json.loads(registry.dispatch("tool_search", {"query": "file"}))
    if sres.get("count", 0) < 4:
        _die(f"tool_search underpopulated: {sres!r}")
    print("        ok")

    print(f"[OK] {len(names)} tools registered, smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
