"""P4 agent subsystem.

Houses:

- ``loop.py``               (P4-S6, task 11.x) — DeskPetAgent ReAct loop,
  lifted and slimmed from Hermes ``AIAgent.run_conversation``.
- ``context_compressor.py`` (P4-S8, task 13.x) — rolling summary when
  conversation token count exceeds ``context_window * 0.7``.
- ``assembler/``            (P4-S7, task 12.x) — ContextAssembler v1
  (TaskClassifier + ComponentRegistry + BudgetAllocator + ContextBundle).

All concrete modules land in later slices. This ``__init__`` only declares
the package surface so imports work from P4-S0 onwards.
"""
