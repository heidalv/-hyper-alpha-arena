"""详情页接口形状速查（`/arbitrage/lanes?lane=<id>` 的解引用面）。"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000/api"
LANE = sys.argv[1] if len(sys.argv) > 1 else "mm_asterdex"


def get(path: str):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=25) as r:
        return json.loads(r.read())


def t(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return f"array[{len(v)}]"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


for path in (f"/trading/config/lanes/{LANE}", f"/trading/lanes/{LANE}",
             f"/trading/lanes/{LANE}/shadow", f"/trading/risk/summary?days=30",
             f"/trading/positions?days=30&lane_id={LANE}"):
    print("=" * 90)
    print(path)
    try:
        d = get(path)
    except Exception as e:
        print(f"  ✗ 请求失败: {e}")
        continue
    if not isinstance(d, dict):
        print(f"  顶层类型 {t(d)}")
        continue
    for k, v in d.items():
        extra = ""
        if isinstance(v, dict):
            extra = "  键=" + ",".join(sorted(v.keys())[:14])
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            extra = "  [0]键=" + ",".join(sorted(v[0].keys())[:14])
        print(f"  {k:<24} {t(v):<12}{extra}")
    # 关键字段专查
    for key in ("params", "limits", "meta", "promotion", "promotion_cached"):
        if key in d:
            v = d[key]
            print(f"  ·· {key} 类型={t(v)}"
                  + (f" 非空键={sorted(v.keys())[:10]}" if isinstance(v, dict) else ""))
