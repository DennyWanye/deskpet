"""ContextBundle + AssemblyPolicy + Slice dataclasses (P4-S7).

The assembler runs once per user turn and produces a :class:`ContextBundle`
that the agent loop consumes. The bundle separates *frozen* (prompt-cacheable)
content from *dynamic* (per-turn) content so Anthropic's prompt cache can
fire on every follow-up turn — see spec Requirement
"Prompt Cache Compatibility" + D6 decision.

Message construction order (see ``ContextBundle.build_messages``)::

    [
      {role: system, content: frozen_system},       # cacheable
      {role: system, content: skill_prelude},        # mostly stable
      {role: system, content: memory_block},         # dynamic
      ...history...,
      {role: user, content: user_message},
    ]

``decisions`` holds the telemetry trace written per-turn (task 12.11).
``tool_schemas`` is the filtered subset the agent loop must pass to
``LLMRegistry.chat_with_fallback``.

Spec: openspec/changes/p4-poseidon-agent-harness/specs/context-assembler/spec.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Task types
# ---------------------------------------------------------------------------
# Canonical 8 task types (spec Requirement "Task Types Enumeration").
# Classifier results outside this set fall back to ``chat``.
TASK_TYPES: tuple[str, ...] = (
    "chat",
    "recall",
    "task",
    "code",
    "web_search",
    "plan",
    "emotion",
    "command",
)


# ---------------------------------------------------------------------------
# Slice — what each Component.provide() returns
# ---------------------------------------------------------------------------
@dataclass
class Slice:
    """One component's contribution to the assembled context.

    A component (memory / tool / persona / ...) returns exactly one
    ``Slice`` per call. ``text_content`` goes into a system message;
    ``tool_schemas`` merges into the bundle's overall schema list;
    ``priority`` controls ordering when multiple components land in the
    same bucket.

    Parameters
    ----------
    component_name:
        Stable identifier (``"memory"``, ``"tool"``, ``"persona"`` ...).
        Used as the trace key in ``decisions.components``.
    text_content:
        Rendered text block. Empty string means "contribute nothing to
        the system prompt" (tool-only components use this).
    tool_schemas:
        OpenAI-format schemas this component adds. The assembler unions
        them into ``ContextBundle.tool_schemas`` after de-dup.
    tokens:
        Approximate token count for this slice. Used by :class:`BudgetAllocator`.
        Components MAY set 0 if cheap-to-measure (eg. time) — budget
        allocator treats missing as 0.
    priority:
        Lower value = trimmed first when over budget. Memory = 100
        (never trimmed), persona = 90, skill = 70, tool = 60, workspace
        = 40, time = 10. Values are advisory — policy can override.
    bucket:
        ``"frozen"`` → goes into ``frozen_system`` (cacheable).
        ``"dynamic"`` → goes into ``memory_block`` (rebuilt every turn).
        ``"skill"`` → goes into ``skill_prelude``.
        ``None`` → defaults to ``"frozen"``.
    meta:
        Free-form component diagnostics (L1 bytes, L2 count, etc.) that
        end up in ``decisions.components[name].meta`` for the UI trace.
    """

    component_name: str
    text_content: str = ""
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    priority: int = 50
    bucket: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Assembly policy — declarative per-task_type rules
# ---------------------------------------------------------------------------
@dataclass
class MemoryPolicy:
    """L1/L2/L3 recall parameters for one task_type."""

    l1: str = "snapshot"  # "snapshot" or "off"
    l2_top_k: int = 5
    l3_top_k: int = 5


@dataclass
class AssemblyPolicy:
    """Policy for assembling context when classifier returns ``task_type``.

    Parameters
    ----------
    task_type:
        One of ``TASK_TYPES``.
    must:
        Components that MUST run. ``"memory"`` cannot be removed (D9 —
        enforced by the YAML loader, not this dataclass).
    prefer:
        Components to include when available. Missing components are
        silently skipped.
    tools:
        Whitelist of tool names exposed to the LLM. ``["*"]`` means
        "all tools from registry". Empty list means "no tools this turn".
    memory:
        Per-task recall parameters (L1 mode, L2/L3 top_k).
    budget_ratio:
        Overrides the global ``config.context.assembler.budget_ratio`` for
        this task_type. Default: inherit from config.
    """

    task_type: str
    must: list[str] = field(default_factory=lambda: ["memory"])
    prefer: list[str] = field(
        default_factory=lambda: ["persona", "time", "workspace"]
    )
    tools: list[str] = field(default_factory=lambda: ["*"])
    memory: MemoryPolicy = field(default_factory=MemoryPolicy)
    budget_ratio: Optional[float] = None


# ---------------------------------------------------------------------------
# Decisions — per-turn telemetry trace
# ---------------------------------------------------------------------------
@dataclass
class ComponentTrace:
    """Per-component telemetry entry for ``AssemblyDecisions.components``."""

    tokens: int = 0
    latency_ms: float = 0.0
    included: bool = True
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssemblyDecisions:
    """Telemetry trace written once per assemble() call.

    Surfaces to the Context Trace UI (P4-S11) via IPC. Keep keys stable
    — frontend reads by name.
    """

    task_type: str = "chat"
    classifier_path: str = "rule"  # "rule" | "embed" | "llm" | "default"
    classifier_latency_ms: float = 0.0
    classifier_confidence: float = 0.0
    assembly_latency_ms: float = 0.0
    components: dict[str, ComponentTrace] = field(default_factory=dict)
    budget_cut: list[str] = field(default_factory=list)
    total_tokens: int = 0
    planned_tools: list[str] = field(default_factory=list)
    used_tools: list[str] = field(default_factory=list)
    final_response_len: int = 0
    # P4-S14: caller-stamped fields used by the Context Trace UI.
    # ``timestamp`` is wall-clock seconds (float, ``time.time()``);
    # ``session_id`` lets the panel filter by session if needed.
    # Both default None so the existing IPC contract stays backward
    # compatible — frontend treats missing fields as "?"/"-".
    timestamp: Optional[float] = None
    session_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for IPC/log — dataclasses aren't JSON-native.

        Frontend ``ContextTracePanel`` reads:
        - ``classifier_path`` (verbatim)
        - ``latency_ms`` — alias for ``assembly_latency_ms``
        - ``total_tokens`` (verbatim)
        - ``token_breakdown`` — flattened ``{component_name: tokens}``
        - ``timestamp`` / ``session_id`` (P4-S14 wire-in)
        - ``reason`` — short rationale (task_type + classifier_path)
        """
        components_view = {
            name: {
                "tokens": t.tokens,
                "latency_ms": round(t.latency_ms, 2),
                "included": t.included,
                "meta": t.meta,
            }
            for name, t in self.components.items()
        }
        token_breakdown = {
            name: t.tokens for name, t in self.components.items() if t.tokens
        }
        return {
            "task_type": self.task_type,
            "classifier_path": self.classifier_path,
            "classifier_latency_ms": round(self.classifier_latency_ms, 2),
            "classifier_confidence": round(self.classifier_confidence, 3),
            "assembly_latency_ms": round(self.assembly_latency_ms, 2),
            # Frontend-friendly aliases (kept ALONGSIDE the canonical
            # fields so any existing consumer still sees the original
            # naming).
            "latency_ms": round(self.assembly_latency_ms, 2),
            "token_breakdown": token_breakdown,
            "reason": f"{self.task_type}/{self.classifier_path}",
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "components": components_view,
            "budget_cut": list(self.budget_cut),
            "total_tokens": self.total_tokens,
            "planned_tools": list(self.planned_tools),
            "used_tools": list(self.used_tools),
            "final_response_len": self.final_response_len,
        }


# ---------------------------------------------------------------------------
# ContextBundle — assembler output consumed by agent loop
# ---------------------------------------------------------------------------
@dataclass
class ContextBundle:
    """Assembled context handed to the agent loop.

    Fields are split by cache-friendliness. ``build_messages`` preserves
    the prompt-cache-safe order (frozen → skill → dynamic) regardless of
    component registration order.
    """

    task_type: str
    frozen_system: str = ""
    memory_block: str = ""
    skill_prelude: str = ""
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    decisions: AssemblyDecisions = field(default_factory=AssemblyDecisions)
    cost_hint: dict[str, int] = field(default_factory=dict)

    def build_messages(
        self,
        base_system: str = "",
        *,
        history: Optional[list[dict[str, Any]]] = None,
        user_message: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Construct an OpenAI-format messages list.

        Order is CRITICAL for prompt caching (spec "Prompt Cache
        Compatibility" scenario): frozen parts first (cacheable), then
        the dynamic memory_block, then history, then the user turn.

        ``base_system`` is prefixed to ``frozen_system`` so callers can
        inject their own preamble (eg. persona override for dev mode)
        without mutating the bundle.

        ``history`` entries are appended verbatim — the caller is
        responsible for role/content shape.
        """
        messages: list[dict[str, Any]] = []

        frozen = "\n\n".join(
            chunk for chunk in (base_system, self.frozen_system) if chunk
        )
        if frozen:
            messages.append({"role": "system", "content": frozen})

        if self.skill_prelude:
            messages.append(
                {"role": "system", "content": self.skill_prelude}
            )

        if self.memory_block:
            messages.append(
                {"role": "system", "content": self.memory_block}
            )

        if history:
            messages.extend(history)

        if user_message is not None:
            messages.append({"role": "user", "content": user_message})

        return messages
