#!/usr/bin/env python3
"""训练期升级 — 运行时冒烟测试（需后端已启动）。"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000")


def get(path: str) -> dict:
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    checks = [
        ("/api/training-phase/status", ["active", "symbols", "funnel"]),
        ("/api/opencode/governor/funnel", ["funnel", "rollback_rate", "training_phase"]),
        ("/api/opencode/status", ["pace", "bridge"]),
    ]
    ok = 0
    for path, keys in checks:
        try:
            data = get(path)
            missing = [k for k in keys if k not in data]
            if missing:
                print(f"FAIL {path} missing keys: {missing}")
            else:
                print(f"OK   {path}")
                ok += 1
        except urllib.error.URLError as err:
            print(f"SKIP {path} — backend unreachable: {err}")
            return 2
        except Exception as err:
            print(f"FAIL {path}: {err}")
    print(f"\n{ok}/{len(checks)} API checks passed")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
