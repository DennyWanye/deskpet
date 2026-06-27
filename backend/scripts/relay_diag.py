"""一次性 relay 诊断：取 keychain key → 列模型 → 测图像模型候选 → 测 LLM 负载/延迟。

只读探测，不生图（生图烧钱）。图像候选只发一张最小请求看 relay 接不接、报什么错。
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = "https://relay.example.com/v1"


def get_key() -> str | None:
    # 1) env
    v = os.environ.get("DESKPET_CLOUD_API_KEY")
    if v:
        return v
    # 2) Windows 凭据库：Rust keyring 用 target=`{user}.{service}`，
    #    Tauri 注入的 DESKPET_CLOUD_API_KEY = default.deskpet-cloud-llm
    try:
        import win32cred

        for tgt in ["default.deskpet-cloud-llm"]:
            try:
                c = win32cred.CredRead(tgt, win32cred.CRED_TYPE_GENERIC)
                blob = c["CredentialBlob"]
                try:
                    s = blob.decode("utf-16-le")
                except Exception:
                    s = blob.decode("utf-8", "replace")
                if s:
                    print(f"[key] got from CredRead {tgt} len={len(s)}")
                    return s
            except Exception as e:  # noqa: BLE001
                print(f"[key] CredRead {tgt} fail: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"[key] win32cred import fail: {e}")
    return None


def client() -> httpx.Client:
    # 直连，不走系统代理（Clash 掐空闲连接）
    return httpx.Client(timeout=httpx.Timeout(connect=8, read=30, write=8, pool=8), trust_env=False)


def list_models(cli: httpx.Client, key: str) -> list[str]:
    print("\n=== /models ===")
    t0 = time.time()
    try:
        r = cli.get(f"{BASE}/models", headers={"Authorization": f"Bearer {key}"})
        dt = (time.time() - t0) * 1000
        print(f"status={r.status_code} latency={dt:.0f}ms")
        if r.status_code >= 400:
            print("body:", r.text[:500])
            return []
        data = r.json().get("data", [])
        ids = sorted(str(m.get("id")) for m in data)
        print(f"count={len(ids)}")
        # 分类
        img = [i for i in ids if any(k in i.lower() for k in ("image", "dall", "flux", "sd", "stable", "midjourney", "mj", "ideogram", "imagen", "seedream", "qwen-image", "gpt-image", "cogview", "kolors"))]
        emb = [i for i in ids if "embed" in i.lower() or "bge" in i.lower() or "m3e" in i.lower()]
        print("\n[图像类候选]:")
        for i in img:
            print("  ", i)
        print("\n[embedding 类]:")
        for i in emb:
            print("  ", i)
        return ids
    except Exception as e:  # noqa: BLE001
        print("ERR:", repr(e))
        return []


def probe_image(cli: httpx.Client, key: str, model: str) -> None:
    """最小图像请求，看 relay 接不接受这个模型名（不真出图就报错也有信息量）。"""
    t0 = time.time()
    try:
        r = cli.post(
            f"{BASE}/images/generations",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "prompt": "a small red circle on white", "n": 1, "size": "1024x1024"},
        )
        dt = (time.time() - t0) * 1000
        ok = r.status_code < 400
        msg = ""
        try:
            j = r.json()
            if ok:
                d = j.get("data", [])
                msg = f"data_items={len(d)} has_url={bool(d and d[0].get('url'))} has_b64={bool(d and d[0].get('b64_json'))}"
            else:
                msg = json.dumps(j.get("error", j), ensure_ascii=False)[:240]
        except Exception:
            msg = r.text[:240]
        print(f"  {model:24s} status={r.status_code} {dt:.0f}ms  {('OK' if ok else 'ERR')}  {msg}")
    except Exception as e:  # noqa: BLE001
        print(f"  {model:24s} EXC {repr(e)[:160]}")


def probe_chat(cli: httpx.Client, key: str, model: str = "gpt-5.5") -> None:
    print(f"\n=== chat 负载/延迟 ({model}) ===")
    for i in range(3):
        t0 = time.time()
        try:
            r = cli.post(
                f"{BASE}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "回复一个字：好"}], "max_tokens": 8, "stream": False},
            )
            dt = (time.time() - t0) * 1000
            txt = ""
            try:
                txt = r.json()["choices"][0]["message"]["content"][:20]
            except Exception:
                txt = r.text[:120]
            print(f"  #{i+1} status={r.status_code} {dt:.0f}ms reply={txt!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  #{i+1} EXC {repr(e)[:160]}")


def main() -> int:
    key = get_key()
    if not key:
        print("FATAL: 拿不到 relay key（env 和 keyring 都没有）")
        return 2
    print(f"[key] resolved, len={len(key)} prefix={key[:6]}…")
    with client() as cli:
        ids = list_models(cli, key)
        print("\n=== 图像模型探测（最小请求，可能真扣费/可能秒拒）===")
        # gpt-image-2 旧的 + 从 models 列表里挑出来的图像候选 + 几个常见名
        candidates = []
        for i in ids:
            il = i.lower()
            if any(k in il for k in ("image", "flux", "dall", "seedream", "qwen-image", "cogview", "kolors", "ideogram", "imagen", "mj_", "midjourney")):
                candidates.append(i)
        # 去重保序
        seen = set()
        candidates = [c for c in candidates if not (c in seen or seen.add(c))]
        if not candidates:
            print("  (models 列表里没有明显图像模型；试几个常见名)")
            candidates = ["gpt-image-1", "dall-e-3", "flux.1-schnell"]
        for c in candidates[:12]:
            probe_image(cli, key, c)
        probe_chat(cli, key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
