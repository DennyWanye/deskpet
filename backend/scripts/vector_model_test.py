"""测本地向量模型 bge-m3 是否可用（真编码，非 mock）。与 relay 无关。"""
from __future__ import annotations

import asyncio
import sys
import time


async def main() -> int:
    from deskpet.memory.embedder import Embedder, EMBEDDING_DIM

    emb = Embedder(device="auto")
    print(f"EMBEDDING_DIM={EMBEDDING_DIM}")
    t0 = time.time()
    await emb.warmup()
    print(f"warmup {(time.time()-t0):.1f}s  ready={emb.is_ready()}  is_mock={emb.is_mock()}")
    if emb.is_mock():
        print("⚠️ 跑在 MOCK 模式 —— 真权重没加载到（可能模型目录缺失）。这不算真向量能力。")
    texts = ["程序员的核心竞争力是什么", "今天天气怎么样", "深度学习与神经网络"]
    t0 = time.time()
    vecs = await emb.encode(texts)
    dt = (time.time() - t0) * 1000
    import numpy as np
    arr = np.asarray(vecs)
    print(f"encode {len(texts)} texts: {dt:.0f}ms  shape={arr.shape}  dtype={arr.dtype}")
    # 相似度自检：句1 vs 句3（都偏技术）应高于 句1 vs 句2
    def cos(a, b):
        a = np.asarray(a); b = np.asarray(b)
        return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
    s13 = cos(vecs[0], vecs[2])
    s12 = cos(vecs[0], vecs[1])
    print(f"cos(技术,技术)={s13:.3f}  cos(技术,天气)={s12:.3f}  语义合理={'YES' if s13 > s12 else 'NO(mock?)'}")
    await emb.close()
    return 0 if not emb.is_mock() else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
