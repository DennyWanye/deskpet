"""走生产代码 image_tools.generate_images 实出一张图，证明 seedream-4.0 端到端通。"""
from __future__ import annotations

import os
import time

import win32cred

# 生产代码从 env 读 key（Tauri 平时注入），脚本里自己从 keychain 取
c = win32cred.CredRead("default.deskpet-cloud-llm", win32cred.CRED_TYPE_GENERIC)
os.environ["DESKPET_CLOUD_API_KEY"] = c["CredentialBlob"].decode("utf-16-le")

from deskpet.tools import image_tools as it  # noqa: E402

print("resolved model =", it._image_model())
print("endpoint base  =", it._resolve_endpoint()[0])

t0 = time.time()
# 用 PPT 实际会发的横版尺寸跑一张
results = it.generate_images(["a serene minimalist mountain landscape at dawn"], size="1536x1024")
dt = time.time() - t0
print(f"generate_images 耗时 {dt:.0f}s")
r = results[0] if results else {}
print("result keys:", list(r.keys()) if isinstance(r, dict) else type(r))
if isinstance(r, dict) and r.get("path"):
    import os as _os
    p = r["path"]
    print(f"✅ PNG 落盘: {p}  size={_os.path.getsize(p)} bytes  exists={_os.path.exists(p)}")
else:
    print("❌ 失败:", r)
