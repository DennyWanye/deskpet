"""Reproduce backend's worker spawn protocol to see what fails."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    print("=== using interpreter:", sys.executable, flush=True)

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-X", "utf8",
        "-m", "deskpet.memory.embedder_worker",
        "--model-path", "C:/Users/24378/AppData/Local/deskpet/models/bge-m3-int8",
        "--device", "cuda",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        # stderr 继承父 — 我们这里直接看到
        limit=16 * 1024 * 1024,
    )
    print("=== spawned PID:", proc.pid, flush=True)

    # First line: spawn heartbeat
    try:
        spawn = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
        print("=== spawn line:", spawn, flush=True)
    except asyncio.TimeoutError:
        print("!!! spawn heartbeat timeout in 10s")
        proc.kill()
        return 1

    # Second line: ready / fatal
    try:
        ready = await asyncio.wait_for(proc.stdout.readline(), timeout=60.0)
        print("=== ready line:", ready, flush=True)
    except asyncio.TimeoutError:
        print("!!! ready/fatal timeout in 60s")
        # Don't kill — let it print stderr
        await asyncio.sleep(2)
        rc = proc.returncode
        print("returncode:", rc, flush=True)
        return 1

    if not ready:
        rc = await proc.wait()
        print("!!! empty ready line, worker exited with rc=", rc)
        return 1

    print("=== success")
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
