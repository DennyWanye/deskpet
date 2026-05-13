"""Quick debug: test repair strategies against latest malformed args
from tauri-dev.log. Bypasses bash escaping problems."""
from __future__ import annotations
import codecs
import json
import re
import sys


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    with open(r"G:\projects\deskpet\tauri-dev.log", "rb") as f:
        data = f.read()
    positions = []
    i = 0
    while True:
        p = data.find(b"p5s2_tool_call_args_malformed", i)
        if p < 0:
            break
        positions.append(p)
        i = p + 1
    print(f"found {len(positions)} malformed events")
    if not positions:
        return
    idx = positions[-1]
    af = data.find(b"args_full='", idx)
    end = data.find(b"' parse_error=", af)
    literal = data[af + len(b"args_full='"):end].decode("utf-8", errors="replace")
    raw = codecs.decode(literal, "unicode_escape")
    print(f"raw len: {len(raw)}")
    print("first 100 raw chars:", repr(raw[:100]))
    print()

    # Strategy 1
    print("=== S1: \\' -> ' ===")
    if "\\'" in raw:
        try:
            r = json.loads(raw.replace("\\'", "'"))
            print(f"  OK content[:80]: {r.get('content','')[:80]!r}")
        except json.JSONDecodeError as e:
            print(f"  FAIL: {e}")
    else:
        print(r'  no \' in raw')

    # Strategy 2
    print()
    print("=== S2: regex invalid-escape ===")
    invalid = re.compile(r'\\([^"\\/bfnrtu])')
    if invalid.search(raw):
        fixed = invalid.sub(r"\1", raw)
        try:
            r = json.loads(fixed)
            print(f"  OK content[:80]: {r.get('content','')[:80]!r}")
        except json.JSONDecodeError as e:
            print(f"  FAIL: {e}")
            # Show context around fail
            print(f"  ctx: {fixed[max(0,e.pos-20):e.pos+20]!r}")
    else:
        print("  no invalid escape pattern")

    # Strategy 3
    print()
    print("=== S3: strict=False on raw ===")
    try:
        r = json.JSONDecoder(strict=False).decode(raw)
        print(f"  OK content[:80]: {r.get('content','')[:80]!r}")
    except json.JSONDecodeError as e:
        print(f"  FAIL: {e}")
        print(f"  ctx: {raw[max(0,e.pos-20):e.pos+20]!r}")


if __name__ == "__main__":
    main()
