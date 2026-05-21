"""Phase A — Memory recall evaluation harness.

Three responsibilities:

1. :class:`QASetBuilder` — generate (query, expected_msg_id) ground-truth
   pairs from existing ``messages_archive`` rows (or live ``messages``)
   using a small LLM, persisted in the ``memory_qa_set`` table.
2. :class:`MetricsRunner` — replay each QA against a Retriever, compute
   hit@1 / hit@5 / hit@10 / MRR, write the result to ``memory_eval_run``.
3. :class:`FeedbackStore` — thin wrapper for the ``memory_user_feedback``
   table; consumed by ws handlers for thumbs-up/down.

The whole package is **strangler-fig**: it never alters legacy tables,
it only reads ``messages`` / ``messages_archive``. Disable by skipping
the CLI / hook entirely — nothing else changes.

CLI:
    python -m deskpet.memory.eval build --n 50  # build qa set
    python -m deskpet.memory.eval run            # run eval against retriever
"""
from deskpet.memory.eval.qaset import QASetBuilder, QAItem
from deskpet.memory.eval.metrics import MetricsRunner, EvalReport
from deskpet.memory.eval.feedback import FeedbackStore

__all__ = [
    "QASetBuilder",
    "QAItem",
    "MetricsRunner",
    "EvalReport",
    "FeedbackStore",
]
