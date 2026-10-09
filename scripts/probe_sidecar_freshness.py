# -*- coding: utf-8 -*-
"""探针：边车记录的**年龄**，以及消费端是否做新鲜度判断（F386 取证）。只读。"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
NOW = time.time()
print("=" * 78)
print("F386 取证：回测边车的记录年龄 + 消费端新鲜度")
print("=" * 78)

for rel, key_ts in [("data/backtest_factor_attr.jsonl", "ts"),
                    ("data/backtest_counterfactual.jsonl", "ts")]:
    p = ROOT / rel
    if not p.exists():
        print(f"\n{rel}: 不存在")
        continue
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    ages = [(NOW - float(r.get(key_ts) or 0)) / 3600.0 for r in rows if r.get(key_ts)]
    print(f"\n{rel}")
    print(f"  条数={len(rows)}  带 ts 的={len(ages)}")
    if ages:
        print(f"  最旧={max(ages):.1f} 小时前 ({max(ages)/24:.1f} 天)")
        print(f"  最新={min(ages):.1f} 小时前 ({min(ages)/24:.2f} 天)")
        newest = max(rows, key=lambda r: float(r.get(key_ts) or 0))
        t = datetime.fromtimestamp(float(newest[key_ts]), tz=timezone.utc)
        print(f"  最新一条写入时间(UTC)={t:%Y-%m-%d %H:%M}  run_id={newest.get('run_id')!r}")
    keys = sorted(rows[-1].keys()) if rows else []
    print(f"  含 ts 字段? {'ts' in keys}  字段={keys}")

print("\n[消费端] 有没有把 ts/年龄渲染给 LLM：")
for rel in ["backend/services/learning_readback.py",
            "backend/services/unified_strategy/weekly_loop.py"]:
    t = (ROOT / rel).read_text(encoding="utf-8")
    has_ts = 'get("ts")' in t or "['ts']" in t or 'rec.get("ts")' in t
    has_age = any(k in t for k in ["age_h", "stale", "新鲜", "过期", "age_days", "ts_age"])
    print(f"  {rel:56s} 读 ts={has_ts}  判新鲜度={has_age}")

print("\n[周环用法] weekly_loop 读边车的上下文：")
wl = (ROOT / "backend/services/unified_strategy/weekly_loop.py").read_text(encoding="utf-8")
i = wl.index("load_factor_attr(")
seg = wl[max(0, i - 700):i + 400]
for ln in seg.splitlines():
    if any(k in ln for k in ["load_factor_attr", "def ", "limit", "if ", "for ", "return", "ts", "#"]):
        print("   " + ln.strip()[:100])
