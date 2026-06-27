import json
import time

import httpx
import win32cred

c = win32cred.CredRead("default.deskpet-cloud-llm", win32cred.CRED_TYPE_GENERIC)
k = c["CredentialBlob"].decode("utf-16-le")
cli = httpx.Client(timeout=httpx.Timeout(connect=8, read=90, write=8, pool=8), trust_env=False)
for sz in ["1536x1024", "1792x1024", "1344x768"]:
    t0 = time.time()
    try:
        r = cli.post(
            "https://relay.example.com/v1/images/generations",
            headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
            json={"model": "doubao-seedream-4.0", "prompt": "a wide cinematic ocean horizon", "size": sz, "n": 1, "quality": "high", "response_format": "b64_json"},
        )
        dt = (time.time() - t0) * 1000
        if r.status_code < 400:
            d = r.json().get("data", [])
            info = f"items={len(d)} b64={bool(d and d[0].get('b64_json'))} url={bool(d and d[0].get('url'))}"
        else:
            info = json.dumps(r.json(), ensure_ascii=False)[:160]
        print(f"{sz}: {r.status_code} {dt:.0f}ms {info}", flush=True)
    except Exception as e:
        print(f"{sz}: EXC {repr(e)[:140]}", flush=True)
cli.close()
print("[ALL DONE]", flush=True)
