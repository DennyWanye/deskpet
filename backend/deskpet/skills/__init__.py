"""Skill system with SKILL.md hot reload (P4-S10, tasks 15.x).

- ``loader.py``       — scans ``%APPDATA%/deskpet/skills/{built-in,user}/``
  for SKILL.md files, parses the YAML frontmatter (required fields:
  ``name``, ``description``, ``version``, ``author``), and registers a
  ``skill_invoke`` tool handler per skill.
- Slash commands      — front-end sends ``/name`` which dispatches to
  ``skill_invoke(name, args=[])``. SKILL.md body is injected as a ``user``
  role message (NOT system — avoids busting prompt cache).
- Hot reload          — ``watchdog`` watches the user skill directory with
  1s debounce (D3 decision). Reload failures are logged but do not unload
  previously-good skills.
- Ship set (v1)       — 3 built-ins: ``recall-yesterday``,
  ``summarize-day``, ``weather-report`` (task 15.7).
"""
