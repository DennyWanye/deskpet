"""DeskPet Phase 4 (Poseidon) package root.

This package is the home of all Phase 4 agent harness + long-term memory
code. It intentionally lives alongside the P3 flat layout (``backend/agent/``,
``backend/memory/``, ``backend/tools/``) so the shipped P3 rc1 behaviour is
untouched during P4 development.

Cutover happens at P4-S12: ``main.py`` will be rerouted from the legacy
modules to this package. Until then, both trees coexist.

Module map (each submodule's ``__init__.py`` lists the owning slice):

- ``deskpet.agent``           — P4-S6 / S7 agent loop + ContextAssembler
- ``deskpet.memory``          — P4-S1..S4 three-layer memory (L1/L2/L3)
- ``deskpet.tools``           — P4-S5 built-in + web tool framework
- ``deskpet.skills``          — P4-S10 SKILL.md loader + hot reload
- ``deskpet.mcp``             — P4-S9 MCP client (filesystem, weather, ...)
- ``deskpet.llm``             — P4-S6 multi-provider LLM adapters

See ``openspec/changes/p4-poseidon-agent-harness/`` for the full spec.
"""
