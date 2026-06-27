# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""为 model_required 测试稳健解析 BGE-M3 模型目录。

## 为什么需要
此前各 model_required 测试**硬编码** `C:/Users/you/AppData/Local/deskpet/
models/bge-m3-int8`。2026-06-02 实际踩到：用户把 DeskPet 用户数据迁到 F 盘
（`/path/to/DeskPetData/`）后，C: 路径空了 → **所有 model_required 测试整批 ERROR**
（模型找不到 + use_mock_when_missing=False 直接 raise）。

本 helper 按候选位置依次探测、验证模型文件齐全才返回，找不到返回 None
（调用方应据此 `pytest.skip`，而不是误用 mock 假装语义召回 —— 后者正是 G5
警告的"FTS 字面虚高"自欺）。

候选顺序：env override → app 自身 paths 解析 → 已知绝对位置（含 F: 迁移位置）。
"""
from __future__ import annotations

import os
from pathlib import Path

_SUBDIR = "bge-m3-int8"
# 判定"完整 BGE-M3 模型"的关键文件（tokenizer + 配置必须在）。
_REQUIRED_FILES = ("config.json", "tokenizer.json", "sentencepiece.bpe.model")


def _is_complete_model(d: Path) -> bool:
    """目录存在 + 关键 tokenizer/config 都在 + 至少一种权重格式存在。"""
    try:
        if not d.is_dir():
            return False
        if not all((d / f).is_file() for f in _REQUIRED_FILES):
            return False
        if (d / "pytorch_model.bin").is_file():
            return True
        onnx = d / "onnx"
        if onnx.is_dir() and any(onnx.iterdir()):
            return True
        return any(d.glob("*.safetensors"))
    except OSError:
        return False


def _candidates() -> list[Path]:
    cands: list[Path] = []
    # 1. 显式 env override（CI / 开发者可一行覆盖）。
    env = os.environ.get("DESKPET_BGE_M3_DIR") or os.environ.get(
        "DESKPET_MODEL_ROOT"
    )
    if env:
        p = Path(env)
        cands.append(p if p.name == _SUBDIR else p / _SUBDIR)
    # 2. 复用 app 自己的 models 目录解析（含 DESKPET_MODEL_ROOT / portable）。
    try:
        from paths import user_models_dir  # type: ignore[import-not-found]

        cands.append(user_models_dir() / _SUBDIR)
    except Exception:  # noqa: BLE001 — paths 不可导入时跳过这条候选
        pass
    # 3. 已知绝对候选：经典 C: 默认 + 用户迁移到 F: 的两处位置。
    cands += [
        Path(r"C:/Users/you/AppData/Local/deskpet/models") / _SUBDIR,
        Path(r"/path/to/DeskPetData/models") / _SUBDIR,
        Path(r"F:/DeskPet/backend/_internal/models") / _SUBDIR,
    ]
    return cands


def resolve_bge_m3() -> Path | None:
    """返回第一个**完整**的 BGE-M3 模型目录；任何已知位置都没有 → None。"""
    seen: set[str] = set()
    for c in _candidates():
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        if _is_complete_model(c):
            return c
    return None
