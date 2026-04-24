"""ContextAssembler policy files (P4-S7, tasks 12.7 / 12.8).

Houses the declarative YAML assembly policies:

- ``default.yaml``    — 8 task_types (chat / recall / task / code /
  web_search / plan / emotion / command), each with ``must`` / ``prefer``
  component lists, tool whitelist, and L1/L2/L3 recall parameters.
- ``exemplars.jsonl`` — labelled user-message examples used by the embed
  tier of the classifier (~100 hand-labelled lines target).

User overrides live at ``%APPDATA%/deskpet/policies/overrides.yaml`` (NOT
in this package). Merge rule: user > default; ``must`` may add components
but MUST NOT remove core memory (D9 decision).

Python modules in this directory (future) hold loader helpers; the
YAML/JSONL files themselves are data.
"""
