"""P4-S2 task 4.1 / 4.2 / 4.4 — Embedder 单元测试。

覆盖：
  * mock 模式下 encode shape=(N, 1024) + 可复现
  * warmup 在无模型时不抛，降级 mock
  * 伪装模型目录存在但加载失败（via monkeypatch）→ 仍降级 mock
  * is_ready / is_mock / close 幂等
  * 空 list encode 返回空矩阵

真模型测试带 ``@pytest.mark.model_required``，默认跳过（pyproject 已配
``addopts = "-m 'not perf and not model_required'"``）。开发手动跑时
``pytest -m model_required`` 即可。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio

from deskpet.memory.embedder import EMBEDDING_DIM, Embedder, _mock_vector


# ---- Mock 向量稳定性（无 event loop 纯函数检查） -------------------


def test_mock_vector_is_stable_for_same_text():
    v1 = _mock_vector("hello")
    v2 = _mock_vector("hello")
    assert v1.shape == (EMBEDDING_DIM,)
    assert np.array_equal(v1, v2)


def test_mock_vector_differs_for_different_text():
    v1 = _mock_vector("hello")
    v2 = _mock_vector("world")
    assert not np.array_equal(v1, v2)


def test_mock_vector_is_unit_normalized():
    v = _mock_vector("anything")
    assert float(np.linalg.norm(v)) == pytest.approx(1.0, rel=1e-5)


# ---- Embedder lifecycle ---------------------------------------------


@pytest_asyncio.fixture
async def mock_embedder(tmp_path: Path):
    """强制 mock 模式：指向一个不存在的目录。"""
    e = Embedder(
        model_path=tmp_path / "nonexistent-bge-m3",
        use_mock_when_missing=True,
    )
    await e.warmup()
    yield e
    await e.close()


@pytest.mark.asyncio
async def test_warmup_without_model_falls_back_to_mock(tmp_path: Path):
    e = Embedder(
        model_path=tmp_path / "no-model-here",
        use_mock_when_missing=True,
    )
    await e.warmup()
    assert e.is_ready() is True
    assert e.is_mock() is True
    await e.close()


@pytest.mark.asyncio
async def test_warmup_is_idempotent(tmp_path: Path):
    e = Embedder(
        model_path=tmp_path / "no-model", use_mock_when_missing=True
    )
    await e.warmup()
    await e.warmup()  # 不应重复加载也不抛
    assert e.is_ready()
    await e.close()


@pytest.mark.asyncio
async def test_warmup_without_model_raises_when_use_mock_false(tmp_path: Path):
    e = Embedder(
        model_path=tmp_path / "no-model",
        use_mock_when_missing=False,
    )
    with pytest.raises(RuntimeError, match="BGE-M3 model not found"):
        await e.warmup()


# ---- encode mock path ----------------------------------------------


@pytest.mark.asyncio
async def test_encode_returns_expected_shape(mock_embedder: Embedder):
    texts = ["今天天气真好", "hello world"]
    vecs = await mock_embedder.encode(texts)
    assert vecs.shape == (2, EMBEDDING_DIM)
    assert vecs.dtype == np.float32


@pytest.mark.asyncio
async def test_encode_empty_list(mock_embedder: Embedder):
    vecs = await mock_embedder.encode([])
    assert vecs.shape == (0, EMBEDDING_DIM)
    assert vecs.dtype == np.float32


@pytest.mark.asyncio
async def test_encode_is_reproducible(mock_embedder: Embedder):
    """同文本两次 encode → 完全相同（稳定 seed）。"""
    texts = ["我喜欢红色袜子", "DeskPet memory test"]
    v1 = await mock_embedder.encode(texts)
    v2 = await mock_embedder.encode(texts)
    assert np.array_equal(v1, v2)


@pytest.mark.asyncio
async def test_encode_auto_warmup_if_not_called(tmp_path: Path):
    """encode 在没先 warmup 时也能工作——内部自动触发 warmup。"""
    e = Embedder(
        model_path=tmp_path / "no-model", use_mock_when_missing=True
    )
    assert not e.is_ready()
    vecs = await e.encode(["quick test"])
    assert vecs.shape == (1, EMBEDDING_DIM)
    assert e.is_ready()
    await e.close()


@pytest.mark.asyncio
async def test_encode_concurrent_calls_are_serialized(mock_embedder: Embedder):
    """asyncio.gather 并发 encode 不应崩（内部 lock 确保串行）。"""
    jobs = [mock_embedder.encode([f"concurrent {i}"]) for i in range(5)]
    results = await asyncio.gather(*jobs)
    assert all(r.shape == (1, EMBEDDING_DIM) for r in results)


# ---- 降级：目录存在但加载抛错 ---------------------------------------


@pytest.mark.asyncio
async def test_fake_model_dir_load_failure_falls_back_to_mock(
    tmp_path: Path, monkeypatch
):
    """伪装 model_path 存在但 _load_real_model 会抛 → 仍降级到 mock。

    用 monkeypatch 让 _load_real_model 直接 raise，模拟 FlagEmbedding
    加载失败（权重损坏 / CUDA 不兼容）。use_mock_when_missing=True 时
    此类失败也 MUST 降级而非崩。
    """
    model_dir = tmp_path / "fake-bge"
    model_dir.mkdir()
    # 放个空 config.json 看起来像模型目录（不影响 —— 核心是 _load_real_model 抛）
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    e = Embedder(model_path=model_dir, use_mock_when_missing=True)

    async def _boom() -> None:
        raise RuntimeError("simulated torch DLL load failure")

    monkeypatch.setattr(e, "_load_real_model", _boom)

    await e.warmup()
    assert e.is_ready() is True
    assert e.is_mock() is True  # 降级生效
    # encode 仍可用
    vecs = await e.encode(["post-failure test"])
    assert vecs.shape == (1, EMBEDDING_DIM)
    await e.close()


# ---- 真模型：默认跳过 -----------------------------------------------


@pytest.mark.model_required
@pytest.mark.asyncio
async def test_real_bge_m3_encode_and_self_similarity():
    """真模型 smoke：dim=1024 + cosine(self, self)=1.0。

    默认跳过（需 model_required marker + 模型已下载）。
    """
    # 让 Embedder 走默认 path 解析（%LocalAppData%\deskpet\models\bge-m3-int8）
    e = Embedder(use_mock_when_missing=False)
    try:
        await e.warmup()
    except RuntimeError:
        pytest.skip("BGE-M3 weights not present on disk")
    assert e.is_mock() is False
    try:
        vecs = await e.encode(["我爱北京天安门"])
        assert vecs.shape == (1, EMBEDDING_DIM)
        # 自己与自己 cosine ≈ 1（BGE-M3 输出已 L2 normalized）
        sim = float(vecs[0] @ vecs[0])
        assert sim == pytest.approx(1.0, abs=1e-3)
    finally:
        await e.close()
