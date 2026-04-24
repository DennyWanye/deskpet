"""P4-S5: classify tool handler exceptions into retriable vs permanent.

Used by ``ToolRegistry.dispatch`` — when a handler raises, we wrap the
exception in ``{"error": "<ExcName>: <msg>", "retriable": <bool>}``
instead of propagating. The agent loop can then decide whether to retry
the same call (retriable=True ⇒ transient) or ask the LLM to adjust its
arguments / give up (retriable=False ⇒ bad input, not worth retrying).

Rules (conservative, tuned for agent retry safety, not Python taxonomy):

* **Network / IO transients → retriable=True**: ``ConnectionError``,
  ``TimeoutError``, ``OSError`` (file busy, disk full, socket reset …).
* **Programmer error → retriable=False**: ``ValueError``, ``TypeError``,
  ``KeyError``, ``AttributeError``, ``IndexError``, ``AssertionError`` —
  retrying with identical args will hit the same bug.
* **Everything else → retriable=True**: unknown failures default to
  retriable so the loop at least tries once more; the exception class
  name travels in the error string, so operators can still spot patterns.

This module is deliberately pure/stateless: no imports beyond stdlib,
no config. Keep it that way so every tool handler can rely on
``classify(exc)`` without triggering circular imports.
"""
from __future__ import annotations


_NON_RETRIABLE: tuple[type[BaseException], ...] = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    IndexError,
    AssertionError,
)

# NB: Python's ``TimeoutError`` is a subclass of ``OSError`` on 3.11+, so
# listing both is redundant for ``isinstance`` — we still keep TimeoutError
# explicitly to document intent when readers skim the retriable list.
_RETRIABLE: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def classify(exc: BaseException) -> bool:
    """Return True if ``exc`` looks transient and worth retrying.

    Precedence: non-retriable checks run first, so a subclass that
    inherits both (unlikely, but e.g. a custom ``BadInputIOError``)
    defaults to the safer "don't loop on programmer bug" answer.
    """
    if isinstance(exc, _NON_RETRIABLE):
        return False
    if isinstance(exc, _RETRIABLE):
        return True
    # Unknown / user-defined exceptions: default to retriable so the
    # agent at least gets another shot. The exception type name ends up
    # in the error string, so observability isn't hurt by the default.
    return True
