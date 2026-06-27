from __future__ import annotations

from deskpet.agent.assembler.components.persona import _resolve_persona


def test_code_persona_has_closing_checklist() -> None:
    persona = _resolve_persona(
        {
            "llm": {"model": "test-model", "base_url": "http://example.test"},
            "code_mode": {"enabled": True, "project_root": "/path/to/deskpet"},
        }
    )

    assert "收尾自查" in persona
    assert "原始需求" in persona
    assert "✓" in persona
    assert "验证证据" in persona
    assert "剩余项" in persona


def test_companion_persona_unchanged() -> None:
    persona = _resolve_persona(
        {
            "llm": {"model": "test-model", "base_url": "http://example.test"},
            "code_mode": {"enabled": False, "project_root": "/path/to/deskpet"},
        }
    )

    assert "收尾自查" not in persona
