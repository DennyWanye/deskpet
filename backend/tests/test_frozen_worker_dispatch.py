# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from pathlib import Path

import pytest

from deskpet.frozen_worker_dispatch import (
    dispatch_frozen_worker_if_requested,
    extract_module_args,
)


def test_extracts_worker_args_from_frozen_python_m_invocation():
    argv = [
        r"F:\DeskPet\backend\deskpet-backend.exe",
        "-X",
        "utf8",
        "-m",
        "deskpet.memory.embedder_worker",
        "--model-path",
        r"/path/to/DeskPetData\models\bge-m3-int8",
        "--device",
        "cpu",
    ]

    assert extract_module_args(argv, "deskpet.memory.embedder_worker") == [
        "--model-path",
        r"/path/to/DeskPetData\models\bge-m3-int8",
        "--device",
        "cpu",
    ]


def test_ignores_normal_backend_invocation():
    argv = [r"F:\DeskPet\backend\deskpet-backend.exe"]

    assert extract_module_args(argv, "deskpet.memory.embedder_worker") is None


def test_main_dispatches_before_heavy_imports():
    main_py = Path(__file__).resolve().parents[1] / "main.py"
    source = main_py.read_text(encoding="utf-8")

    dispatch_pos = source.index("dispatch_frozen_worker_if_requested()")
    heavy_import_pos = source.index("import asyncio")

    assert dispatch_pos < heavy_import_pos


def test_dispatch_runs_embedder_worker_main_and_exits(monkeypatch):
    from deskpet.memory import embedder_worker

    seen: dict[str, list[str]] = {}

    def fake_main(args: list[str]) -> int:
        seen["args"] = args
        return 23

    monkeypatch.setattr(embedder_worker, "main", fake_main)

    with pytest.raises(SystemExit) as exc:
        dispatch_frozen_worker_if_requested(
            [
                r"F:\DeskPet\backend\deskpet-backend.exe",
                "-X",
                "utf8",
                "-m",
                "deskpet.memory.embedder_worker",
                "--model-path",
                r"/path/to/DeskPetData\models\bge-m3-int8",
                "--device",
                "cpu",
            ]
        )

    assert exc.value.code == 23
    assert seen["args"] == [
        "--model-path",
        r"/path/to/DeskPetData\models\bge-m3-int8",
        "--device",
        "cpu",
    ]
