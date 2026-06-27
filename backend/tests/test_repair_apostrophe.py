# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""LLM JSON args repair tests (P6 bugfix 2026-05-14).

Mirrors the multi-strategy repair chain in
``providers/openai_compatible.py``:

  1. ``\\'`` → ``'``       (single-quote pseudo-escape)
  2. ``\\X`` → ``X``       (any invalid JSON escape, regex-driven)
  3. ``strict=False``      (raw control chars inside strings)
"""
import json
import re

import pytest


_INVALID_ESC = re.compile(r'\\([^"\\/bfnrtu])')


def _try_repair(raw: str) -> tuple[dict | None, str | None]:
    """Return (parsed_dict, repair_label) or (None, None)."""
    if "\\'" in raw:
        try:
            r = json.loads(raw.replace("\\'", "'"))
            if isinstance(r, dict):
                return r, "stripped_backslash_apostrophe"
        except json.JSONDecodeError:
            pass
    if _INVALID_ESC.search(raw):
        try:
            r = json.loads(_INVALID_ESC.sub(r"\1", raw))
            if isinstance(r, dict):
                return r, "stripped_invalid_escapes"
        except json.JSONDecodeError:
            pass
    try:
        r = json.JSONDecoder(strict=False).decode(raw)
        if isinstance(r, dict):
            return r, "strict_false"
    except json.JSONDecodeError:
        pass
    return None, None


def _repair_apostrophe(raw: str) -> dict | None:
    """Legacy single-strategy wrapper for backwards-compat tests."""
    parsed, _ = _try_repair(raw)
    return parsed


def test_bad_apostrophe_in_react_import_repaired() -> None:
    # Raw bytes the LLM streamed (single byte sequences):
    #   {"path": "a.py", "content": "import { x } from \'react\'"}
    # That's 4 chars: backslash + apostrophe inside the JSON string.
    raw = "".join([
        '{"path": "a.py", "content": "import { x } from ',
        chr(92), chr(39),  # \'
        'react',
        chr(92), chr(39),  # \'
        '"}',
    ])
    # Confirm raw is unparseable (the bug)
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    # Repair succeeds
    repaired = _repair_apostrophe(raw)
    assert repaired is not None
    assert repaired["content"] == "import { x } from 'react'"


def test_legal_json_repair_returns_dict() -> None:
    """Already-valid JSON: repair chain's strict=False fallback parses
    it successfully (which is fine — caller only enters repair after
    initial json.loads failed, so this codepath is unreached for valid
    JSON in production)."""
    raw = '{"path": "a.py", "content": "no quotes here"}'
    assert json.loads(raw)["path"] == "a.py"
    parsed, label = _try_repair(raw)
    assert parsed is not None
    assert label == "strict_false"


def test_unrepairable_returns_none() -> None:
    """If JSON has \\' but still has other problems, repair returns None."""
    raw = "".join(['{"path": "a", "content": "x\\', "'", '"}', "extra garbage"])
    assert _repair_apostrophe(raw) is None


def test_invalid_escape_repair_catches_apostrophe() -> None:
    """The broad-regex repair catches \\' as a fallback even when
    strategy 1 wasn't enough (e.g. mixed bad escapes)."""
    raw = "".join([
        '{"path": "a.tsx", "content": "import { x } from ',
        chr(92), chr(39), 'react', chr(92), chr(39), '"}',
    ])
    parsed, label = _try_repair(raw)
    assert parsed is not None
    assert label in ("stripped_backslash_apostrophe", "stripped_invalid_escapes")
    assert parsed["content"] == "import { x } from 'react'"


def test_invalid_escape_repair_drops_random_bad_escape() -> None:
    """E.g. \\; is invalid (semicolon doesn't need escape) — repair 2 fixes."""
    raw = '{"k": "stop\\;here"}'
    parsed, label = _try_repair(raw)
    assert parsed is not None
    assert label == "stripped_invalid_escapes"
    assert parsed["k"] == "stop;here"


def test_legal_escapes_preserved() -> None:
    """\\n \\t \\" \\\\ \\/ \\b \\f \\r \\uXXXX all stay intact."""
    raw = r'{"k": "line1\nline2\tend中\"quoted\""}'
    parsed = json.loads(raw)  # should pass without repair
    assert parsed["k"] == 'line1\nline2\tend中"quoted"'
    # And repair shouldn't mangle it either
    repaired = _INVALID_ESC.sub(r"\1", raw)
    parsed2 = json.loads(repaired)
    assert parsed2 == parsed


def test_strict_false_accepts_raw_newline_in_string() -> None:
    """LLM occasionally streams raw \\n bytes (control char 0x0a)
    inside a string instead of the \\\\n escape sequence. strict=False
    accepts these."""
    raw = '{"k": "line1\nline2"}'  # actual newline byte inside string
    # Default strict mode rejects
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    parsed, label = _try_repair(raw)
    assert parsed is not None
    assert label == "strict_false"
    assert parsed["k"] == "line1\nline2"
