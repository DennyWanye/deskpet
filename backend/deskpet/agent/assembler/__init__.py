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

from deskpet.agent.assembler.assembler import ContextAssembler
from deskpet.agent.assembler.bundle import (
    TASK_TYPES,
    AssemblyDecisions,
    AssemblyPolicy,
    ComponentTrace,
    ContextBundle,
    MemoryPolicy,
    Slice,
)
from deskpet.agent.assembler.budget import BudgetAllocator, BudgetResult
from deskpet.agent.assembler.classifier import ClassifierResult, TaskClassifier
from deskpet.agent.assembler.components.base import Component, ComponentContext
from deskpet.agent.assembler.components.memory import MemoryComponent
from deskpet.agent.assembler.components.persona import PersonaComponent
from deskpet.agent.assembler.components.skill import SkillComponent
from deskpet.agent.assembler.components.time_component import TimeComponent
from deskpet.agent.assembler.components.tool import ToolComponent
from deskpet.agent.assembler.components.workspace import WorkspaceComponent
from deskpet.agent.assembler.policy import load_policies
from deskpet.agent.assembler.registry import ComponentRegistry
from deskpet.agent.assembler.tts_prenarration import TTSPreNarrator

__all__ = [
    "TASK_TYPES",
    "AssemblyDecisions",
    "AssemblyPolicy",
    "BudgetAllocator",
    "BudgetResult",
    "ClassifierResult",
    "Component",
    "ComponentContext",
    "ComponentRegistry",
    "ComponentTrace",
    "ContextAssembler",
    "ContextBundle",
    "MemoryComponent",
    "MemoryPolicy",
    "PersonaComponent",
    "SkillComponent",
    "Slice",
    "TaskClassifier",
    "TTSPreNarrator",
    "TimeComponent",
    "ToolComponent",
    "WorkspaceComponent",
    "load_policies",
]


def build_default_assembler(
    *,
    embedder=None,
    llm_registry=None,
    enabled: bool = True,
    llm_model: str = "claude-haiku-4-5",
    context_window: int = 200_000,
    budget_ratio: float = 0.6,
) -> ContextAssembler:
    """One-shot factory for the common case.

    Wires: 6 built-in components + packaged default.yaml policies +
    classifier with provided embedder/LLM + default budget allocator.
    Caller still supplies memory_manager / tool_registry per-turn
    via :meth:`ContextAssembler.assemble`.
    """
    registry = ComponentRegistry()
    registry.register(MemoryComponent())
    registry.register(ToolComponent())
    registry.register(SkillComponent())
    registry.register(PersonaComponent())
    registry.register(TimeComponent())
    registry.register(WorkspaceComponent())

    policies = load_policies()

    classifier = TaskClassifier(
        embedder=embedder,
        llm_registry=llm_registry,
        llm_model=llm_model,
    )

    budget = BudgetAllocator(
        context_window=context_window, budget_ratio=budget_ratio
    )

    return ContextAssembler(
        component_registry=registry,
        policies=policies,
        classifier=classifier,
        budget_allocator=budget,
        enabled=enabled,
    )
