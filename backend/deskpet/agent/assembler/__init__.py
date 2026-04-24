"""ContextAssembler v1 (P4-S7, tasks 12.1-12.17).

Runs once before every DeskPet agent loop iteration to:

1. Classify user input (rule -> embed -> LLM cascade, ``classifier.py``).
2. Fan out component providers in parallel (``components/``).
3. Allocate a token budget (``context_window * budget_ratio``, default 0.6).
4. Emit a ``ContextBundle`` with ``frozen_system`` + ``memory_block`` +
   ``skill_prelude`` + ``tool_schemas`` in a cache-friendly order.
5. Log ``decisions`` trace for the Context Trace UI (P4-S11).

Policies are declarative YAML under ``policies/`` and can be overridden by
``%APPDATA%/deskpet/policies/overrides.yaml``.

Spec: ``openspec/changes/p4-poseidon-agent-harness/specs/context-assembler/``.
"""
