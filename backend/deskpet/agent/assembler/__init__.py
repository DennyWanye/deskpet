# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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
from deskpet.agent.assembler.components.workspace_memory import (
    WorkspaceMemoryComponent,
)
from deskpet.agent.assembler.components.preference_profile import (
    PreferenceProfileComponent,
)
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
    "WorkspaceMemoryComponent",
    "PreferenceProfileComponent",
    "load_policies",
]


def build_default_assembler(
    *,
    embedder=None,
    llm_registry=None,
    enabled: bool = True,
    llm_model: str = "claude-haiku-4-5",
    llm_timeout_s: float = 6.0,
    context_window: int = 200_000,
    budget_ratio: float = 0.6,
    workspace_memory_store=None,
    facts_store=None,
    persona_inject: bool = False,
    skill_matcher=None,
    skill_loader=None,
    auto_disclosure_config=None,
    knowledge_enabled: bool = False,
) -> ContextAssembler:
    """One-shot factory for the common case.

    Wires: 8 built-in components + packaged default.yaml policies +
    classifier with provided embedder/LLM + default budget allocator.
    Caller still supplies memory_manager / tool_registry per-turn
    via :meth:`ContextAssembler.assemble`.

    记忆系统升级 WI-M1.6：``workspace_memory_store`` 由 main.py 在
    ``memory.v2.workspace_memory`` flag 开时注入；None → 组件空转。

    FP-4 WI-3.2：``facts_store`` 由 main.py 在 ``memory.v2.persona_inject``
    flag 开时注入；None / flag_enabled=False → 组件返回空 Slice（BC）。
    """
    registry = ComponentRegistry()
    registry.register(MemoryComponent())
    registry.register(ToolComponent())
    # FP-5 缺口 5c (2026-06-06): 注入 matcher/loader 让 auto-disclosure 生效。
    # 默认 None → SkillComponent 降级 desc-only（字节级 BC，与 WI-4.1 前一致）。
    registry.register(
        SkillComponent(skill_matcher=skill_matcher, skill_loader=skill_loader)
    )
    registry.register(PersonaComponent())
    registry.register(TimeComponent())
    registry.register(WorkspaceComponent())
    registry.register(WorkspaceMemoryComponent(store=workspace_memory_store))
    registry.register(
        PreferenceProfileComponent(store=facts_store, flag_enabled=persona_inject)
    )

    policies = load_policies()

    classifier = TaskClassifier(
        embedder=embedder,
        llm_registry=llm_registry,
        llm_model=llm_model,
        # WI-5 R2 命门：classifier llm tier 默认 timeout 2.0s，但 shim 忽略 llm_model 实际
        # 跑 local_llm.model（relay 主模型 gpt-5.5，thinking 4-6s）→ 2s 必超时 = 接了等于没接。
        # 放宽到 ≥6s 让 llm tier 真出结果。
        llm_timeout_s=llm_timeout_s,
    )

    budget = BudgetAllocator(
        context_window=context_window, budget_ratio=budget_ratio
    )

    # FP-5 缺口 5j：把 auto_disclosure 配置作为 assemble() 的 default_config，
    # 任何 venue 调用方不传 skills 也能让 SkillComponent 拿到 → 根治 venue-miss。
    # WI-5: knowledge_enabled 也进 default_config，让 assemble() 的 per-turn
    # config（被 SkillComponent + assembler 的 prefer 注入逻辑读）拿得到该 flag，
    # 否则触发式知识注入在任何 venue 都因 config 缺该键而恒不生效（真机暴露）。
    _default_config = None
    if auto_disclosure_config or knowledge_enabled:
        _skills_default = {}
        if auto_disclosure_config:
            _skills_default["auto_disclosure"] = dict(auto_disclosure_config)
        if knowledge_enabled:
            _skills_default["knowledge_enabled"] = True
        _default_config = {"skills": _skills_default}

    return ContextAssembler(
        component_registry=registry,
        policies=policies,
        classifier=classifier,
        budget_allocator=budget,
        enabled=enabled,
        default_config=_default_config,
    )
