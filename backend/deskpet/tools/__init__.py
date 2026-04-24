"""P4 tool framework + built-in tools (P4-S5, tasks 7.x / 8.x / 9.x).

- ``registry.py``       — ToolRegistry singleton with auto-discovery
  (``import deskpet.tools`` walks ``tools/*.py`` and calls ``register``).
  Supports OpenAI function-calling schema export, toolset whitelist
  filtering, ``requires_env`` hiding, ``check_fn`` readiness gating,
  ``ToolSearchTool`` lazy activation.
- ``memory_tools.py``   — P4-S5 lower: memory_write / memory_read /
  memory_search (thin wrappers over MemoryManager).
- ``todo_tools.py``     — P4-S5 lower: todo_write / todo_complete backed
  by a ``todos`` SQLite table.
- ``file_tools.py``     — P4-S5 lower: file_read / file_write / file_glob
  / file_grep sandboxed to %APPDATA%\\deskpet\\workspace\\.
- ``web_fetch.py`` / ``web_crawl.py`` / ``web_extract_article.py``
  / ``web_read_sitemap.py``   — P4-S5 supplement: zero-cost web toolkit
  (httpx + trafilatura + selectolax, no paid search APIs).

Brave / Tavily / Bing / exa.ai are intentionally absent (D9 decision,
reinforced by CI grep guard in task 9.9).
"""
