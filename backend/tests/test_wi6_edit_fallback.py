from __future__ import annotations

import json
from pathlib import Path

from deskpet.tools.os_tools.edit_file import edit_file


def _decode(result: str) -> dict:
    return json.loads(result)


def test_exact_match_unchanged(tmp_path: Path) -> None:
    p = tmp_path / "doc.txt"
    p.write_text("foo bar baz", encoding="utf-8")

    out = _decode(
        edit_file(
            {"path": str(p), "old_string": "bar", "new_string": "BAR"},
            "",
        )
    )

    assert out["replacements"] == 1
    assert out["path"] == str(p.resolve())
    assert "matched_by" not in out
    assert p.read_text(encoding="utf-8") == "foo BAR baz"


def test_whitespace_fallback(tmp_path: Path) -> None:
    p = tmp_path / "doc.py"
    p.write_text("def f():\n    value = 1\n", encoding="utf-8")

    out = _decode(
        edit_file(
            {
                "path": str(p),
                "old_string": "      value = 1",
                "new_string": "      value = 2",
            },
            "",
        )
    )

    assert out["replacements"] == 1
    assert out["matched_by"] == "whitespace"
    assert out["confidence"] == 1.0
    assert p.read_text(encoding="utf-8") == "def f():\n    value = 2\n"


def test_anchor_fallback(tmp_path: Path) -> None:
    p = tmp_path / "doc.py"
    p.write_text(
        "start\nif enabled:\n    value = compute(1)\n    print(value)\nend\n",
        encoding="utf-8",
    )

    out = _decode(
        edit_file(
            {
                "path": str(p),
                "old_string": "if enabled:\n    value = compute(2)\n    print(value)",
                "new_string": "if enabled:\n    value = compute(3)\n    print(value)",
            },
            "",
        )
    )

    assert out["replacements"] == 1
    assert out["matched_by"] == "anchor"
    assert out["confidence"] >= 0.85
    assert (
        p.read_text(encoding="utf-8")
        == "start\nif enabled:\n    value = compute(3)\n    print(value)\nend\n"
    )


def test_no_match_returns_did_you_mean(tmp_path: Path) -> None:
    p = tmp_path / "doc.py"
    p.write_text("alpha = 1\nbeta = 2\ngamma = 3\n", encoding="utf-8")

    out = _decode(
        edit_file(
            {"path": str(p), "old_string": "bet = 20", "new_string": "beta = 20"},
            "",
        )
    )

    assert out["ok"] is False
    assert out["matched_by"] == "none"
    assert out["did_you_mean"]
    assert isinstance(out["did_you_mean"][0]["line"], int)
    assert out["did_you_mean"][0]["text"] == "beta = 2"
    assert p.read_text(encoding="utf-8") == "alpha = 1\nbeta = 2\ngamma = 3\n"


def test_fuzzy_off_is_exact_only(tmp_path: Path) -> None:
    p = tmp_path / "doc.py"
    p.write_text("    value = 1\n", encoding="utf-8")

    out = _decode(
        edit_file(
            {
                "path": str(p),
                "old_string": "      value = 1",
                "new_string": "      value = 2",
                "fuzzy": False,
            },
            "",
        )
    )

    assert out["ok"] is False
    assert "old_string" in out["error"]
    assert "matched_by" not in out
    assert p.read_text(encoding="utf-8") == "    value = 1\n"
