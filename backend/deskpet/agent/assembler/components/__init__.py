"""ContextAssembler built-in components (P4-S7, task 12.5).

Six components ship in v1, each exposing ``async def provide(ctx) -> Slice``:

- ``memory.py``     — L1 frozen snapshot + L2/L3 recall results.
- ``tool.py``       — tool schemas filtered by ``policy.tools`` whitelist.
- ``skill.py``      — skill preludes auto-mounted from ``policy.prefer``.
- ``persona.py``    — frozen persona / system prompt chunk.
- ``time.py``       — current time / date / timezone block.
- ``workspace.py``  — %APPDATA%\\deskpet\\workspace\\ summary / cwd.

``ComponentRegistry`` in the parent package fans these out via
``asyncio.gather`` so total latency approaches ``max(components)`` rather
than the serial sum.
"""
