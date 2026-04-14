"""Tests for StreamingTagParser — parses [emotion:xxx] / [action:xxx]
from a token stream without losing non-tag characters."""
from __future__ import annotations

import pytest

from pipeline.tag_parser import StreamingTagParser, TagEvent


def _drain(parser: StreamingTagParser, tokens: list[str]) -> tuple[str, list[TagEvent]]:
    """Feed tokens, collect all text and events, then flush."""
    text = ""
    events: list[TagEvent] = []
    for tok in tokens:
        for item in parser.feed(tok):
            if isinstance(item, str):
                text += item
            else:
                events.append(item)
    for item in parser.flush():
        if isinstance(item, str):
            text += item
        else:
            events.append(item)
    return text, events


def test_plain_text_passes_through():
    parser = StreamingTagParser()
    text, events = _drain(parser, ["Hello", " ", "world"])
    assert text == "Hello world"
    assert events == []


def test_emotion_tag_extracted_and_stripped():
    parser = StreamingTagParser()
    text, events = _drain(parser, ["Hi[emotion:happy] there"])
    assert text == "Hi there"
    assert len(events) == 1
    assert events[0].kind == "emotion"
    assert events[0].value == "happy"


def test_action_tag_extracted_and_stripped():
    parser = StreamingTagParser()
    text, events = _drain(parser, ["[action:wave]Hello"])
    assert text == "Hello"
    assert len(events) == 1
    assert events[0].kind == "action"
    assert events[0].value == "wave"


def test_tag_split_across_chunks():
    """Tag boundary falls mid-chunk — parser must buffer without emitting."""
    parser = StreamingTagParser()
    text, events = _drain(parser, ["Hi[emot", "ion:ha", "ppy] bye"])
    assert text == "Hi bye"
    assert len(events) == 1
    assert events[0].value == "happy"


def test_unknown_tag_passes_through_verbatim():
    """Non-whitelist tag (e.g. [color:red]) is not stripped."""
    parser = StreamingTagParser()
    text, events = _drain(parser, ["see [color:red] here"])
    assert text == "see [color:red] here"
    assert events == []


def test_unclosed_bracket_at_end_flushes():
    """Dangling '[' at stream end must flush to output, not get eaten."""
    parser = StreamingTagParser()
    text, events = _drain(parser, ["Hello [emot"])
    assert text == "Hello [emot"
    assert events == []


def test_bracket_without_colon_passes_through():
    """'[foo]' without colon is not a tag."""
    parser = StreamingTagParser()
    text, events = _drain(parser, ["see [note] here"])
    assert text == "see [note] here"
    assert events == []


def test_multiple_tags_in_one_stream():
    parser = StreamingTagParser()
    text, events = _drain(
        parser,
        ["[emotion:sad]Oh no ", "[action:shake]I'm upset"],
    )
    assert text == "Oh no I'm upset"
    assert len(events) == 2
    assert (events[0].kind, events[0].value) == ("emotion", "sad")
    assert (events[1].kind, events[1].value) == ("action", "shake")


def test_buffer_overflow_flushes_as_text():
    """Pathological input ('[[[[...') must not grow buffer unboundedly."""
    parser = StreamingTagParser()
    # 100 chars of '[' — well past MAX_BUFFER — must not be swallowed
    text, events = _drain(parser, ["[" * 100])
    assert "[" in text  # at least some brackets made it out
    assert events == []


def test_tag_event_is_frozen_dataclass():
    """TagEvent should be immutable value object."""
    evt = TagEvent(kind="emotion", value="happy")
    with pytest.raises((AttributeError, Exception)):
        evt.value = "sad"  # type: ignore[misc]
