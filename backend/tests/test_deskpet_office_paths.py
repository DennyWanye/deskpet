"""TDD T0 — office_paths.py path authorization (防手滑层)."""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.tools import office_paths as op


@pytest.fixture(autouse=True)
def _clean():
    op.clear_authorizations()
    yield
    op.clear_authorizations()


def test_t0_1_authorize_then_is_authorized(tmp_path: Path):
    f = tmp_path / "a.docx"
    f.write_text("x")
    op.authorize_path(f)
    assert op.is_authorized(f)


def test_t0_2_unauthorized_is_false(tmp_path: Path):
    assert not op.is_authorized(tmp_path / "never.docx")


def test_t0_3_dir_authorization_is_recursive(tmp_path: Path):
    op.authorize_path(tmp_path)
    assert op.is_authorized(tmp_path / "sub" / "deep.docx")


def test_t0_4_resolve_for_read_unauthorized_none(tmp_path: Path):
    f = tmp_path / "a.docx"
    f.write_text("x")
    assert op.resolve_for_read(f) is None


def test_t0_5_resolve_for_read_authorized(tmp_path: Path):
    f = tmp_path / "a.docx"
    f.write_text("x")
    op.authorize_path(f)
    resolved = op.resolve_for_read(f)
    assert resolved is not None and resolved.is_absolute()


def test_t0_6_write_into_system_dir_refused(tmp_path: Path):
    op.authorize_path("C:\\Windows")
    with pytest.raises(op.PathError):
        op.resolve_for_write(
            "C:\\Windows\\evil.docx", default_prefix="d", default_suffix=".docx"
        )


def test_t0_7_write_into_temp_allowed():
    import tempfile

    target = Path(tempfile.gettempdir()) / "deskpet-test-out.docx"
    resolved = op.resolve_for_write(target, default_prefix="d", default_suffix=".docx")
    assert resolved == target.resolve()


def test_t0_8_write_none_returns_temp_path():
    resolved = op.resolve_for_write(None, default_prefix="deskpet-x", default_suffix=".xlsx")
    assert resolved.suffix == ".xlsx"
    assert "deskpet-x" in resolved.name


def test_t0_9_dotdot_traversal_escapes_authorization(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    op.authorize_path(docs)
    # docs/../secret resolves outside the authorized dir.
    escaped = docs / ".." / "secret.docx"
    assert not op.is_authorized(escaped)


def test_t0_10_clear_authorizations(tmp_path: Path):
    op.authorize_path(tmp_path)
    assert op.is_authorized(tmp_path / "a")
    op.clear_authorizations()
    assert not op.is_authorized(tmp_path / "a")


def test_t0_extra_resolve_write_authorized_dir(tmp_path: Path):
    op.authorize_path(tmp_path)
    target = tmp_path / "new" / "out.xlsx"
    resolved = op.resolve_for_write(target, default_prefix="d", default_suffix=".xlsx")
    assert resolved.parent.exists()
