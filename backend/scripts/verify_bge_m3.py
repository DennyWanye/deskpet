# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""确认 BGE-M3 真连上 dev 并能产生真实语义向量(非 mock)。
从 F 盘模型加载, encode 测试文本, 用语义相似度证明是真 BGE-M3:
真模型 → '猫咪'vs'小猫' 相似度高, '猫咪'vs'汽车' 低; mock(md5随机) 不会有语义。"""
import os, sys
os.environ["DESKPET_MODEL_ROOT"] = r"F:\DeskPetData\models"
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import asyncio
import numpy as np
from deskpet.memory.embedder import Embedder

def cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

async def main():
    print("[1] 实例化 Embedder (model_root=F:\\DeskPetData\\models, inprocess)...")
    e = Embedder(mode="inprocess", device="auto")
    await e.warmup()
    print(f"[2] is_ready={e.is_ready()}  is_mock={e.is_mock()}  model_path={e._model_path}")
    if e.is_mock():
        print("[FAIL] embedder 是 mock — BGE-M3 没真加载!")
        return 2
    texts = ["猫咪很可爱", "小猫咪真萌", "汽车发动机维修"]
    print(f"[3] encode {texts}")
    vecs = await e.encode(texts)
    vecs = np.asarray(vecs)
    print(f"    向量 shape={vecs.shape} dtype={vecs.dtype}")
    s_near = cos(vecs[0], vecs[1])   # 猫咪 vs 小猫 — 应高
    s_far = cos(vecs[0], vecs[2])    # 猫咪 vs 汽车 — 应低
    print(f"[4] 语义相似度:")
    print(f"    '猫咪很可爱' vs '小猫咪真萌'  = {s_near:.4f}  (真模型应 >0.5)")
    print(f"    '猫咪很可爱' vs '汽车发动机维修' = {s_far:.4f}  (应明显更低)")
    ok = (
        not e.is_mock()
        and vecs.shape == (3, 1024)
        and s_near > 0.5
        and s_near > s_far + 0.1
    )
    print("\n" + "=" * 56)
    if ok:
        print("[PASS] BGE-M3 真连上 dev + 从 F 盘加载 + 产生真实语义向量")
        print(f"       (非 mock, 1024 维, 语义相关性正确: 近 {s_near:.2f} > 远 {s_far:.2f})")
    else:
        print(f"[FAIL] is_mock={e.is_mock()} shape={vecs.shape} near={s_near:.3f} far={s_far:.3f}")
    print("=" * 56)
    return 0 if ok else 2

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
