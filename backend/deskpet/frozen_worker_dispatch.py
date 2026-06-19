# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import sys


EMBEDDER_WORKER_MODULE = "deskpet.memory.embedder_worker"


def extract_module_args(argv: list[str], module_name: str) -> list[str] | None:
    """Return args after ``-m module_name`` in a frozen Python-style argv."""
    for index, value in enumerate(argv):
        if value != "-m":
            continue
        if index + 1 >= len(argv):
            return None
        if argv[index + 1] == module_name:
            return argv[index + 2 :]
    return None


def dispatch_frozen_worker_if_requested(argv: list[str] | None = None) -> None:
    """Let the frozen backend exe act like ``python -m`` for worker modules."""
    actual_argv = list(sys.argv if argv is None else argv)
    worker_args = extract_module_args(actual_argv, EMBEDDER_WORKER_MODULE)
    if worker_args is None:
        return

    from deskpet.memory.embedder_worker import main as embedder_worker_main

    raise SystemExit(embedder_worker_main(worker_args))
