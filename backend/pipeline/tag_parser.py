"""Streaming parser for [emotion:xxx] / [action:xxx] tags in LLM output.

Design goal: feed LLM tokens one at a time, yield either:
  - str chunks (non-tag text, safe to concatenate for TTS)
  - TagEvent instances (whitelisted tags extracted from the stream)

Guarantees:
  - Every non-tag character reaches the output exactly once (no data loss).
  - Tag boundary may fall anywhere across chunks — parser buffers until
    it sees `]` or exceeds MAX_BUFFER (overflow = flush as plain text).
  - Only `emotion` and `action` keys are stripped; unknown tags pass through
    verbatim (so `[color:red]` stays in the user-visible text).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Literal

TagKind = Literal["emotion", "action"]
_WHITELIST: frozenset[str] = frozenset({"emotion", "action"})
MAX_BUFFER = 32  # chars held in potential-tag state before we give up


@dataclass(frozen=True)
class TagEvent:
    """A whitelisted tag extracted from the stream."""
    kind: TagKind
    value: str


class StreamingTagParser:
    """State machine: NORMAL ↔ IN_TAG (buffering since last `[`).

    Every `feed(token)` yields zero or more items, each either a str
    (safe to append to output text) or a TagEvent.
    """

    def __init__(self) -> None:
        self._buf: str = ""  # holds '[...' while deciding if it's a tag
        self._in_tag: bool = False

    def feed(self, token: str) -> Iterator[str | TagEvent]:
        for ch in token:
            if not self._in_tag:
                if ch == "[":
                    self._buf = "["
                    self._in_tag = True
                else:
                    yield ch
            else:
                self._buf += ch
                if ch == "]":
                    # Try to parse buffered [...]
                    yield from self._resolve_tag()
                    self._in_tag = False
                    self._buf = ""
                elif len(self._buf) > MAX_BUFFER:
                    # Overflow — give up, flush as plain text
                    yield self._buf
                    self._in_tag = False
                    self._buf = ""

    def flush(self) -> Iterator[str | TagEvent]:
        """Call at end of stream — any dangling buffer becomes plain text."""
        if self._buf:
            yield self._buf
            self._buf = ""
            self._in_tag = False

    def _resolve_tag(self) -> Iterator[str | TagEvent]:
        """Buffer contains e.g. '[emotion:happy]'. Emit event if whitelisted,
        otherwise yield the whole thing as plain text (pass-through)."""
        inner = self._buf[1:-1]  # strip '[' and ']'
        if ":" in inner:
            key, _, value = inner.partition(":")
            if key in _WHITELIST and value:
                yield TagEvent(kind=key, value=value)  # type: ignore[arg-type]
                return
        # Not a recognized tag — pass through verbatim
        yield self._buf
