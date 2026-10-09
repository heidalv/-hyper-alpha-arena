"""Why zero fills for 40+ minutes while the engine quotes 16 symbols?

Hypotheses to check:
  H1  quotes are stale (quote_ts old -> not re-armed each tick)
  H2  quote quantity rounds to zero (cap too small vs stepSize)
  H3  quotes are at wrong prices (far from market)
"""
from __future__ import annotations

import io
import json
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"

s = json.load(open(ROOT + r"\logs\mm_lane_status.json", encoding="utf-8",
                   errors="replace"))
st = s.get("states") or {}
now = time.time()
print("=" * 92)
print(f"挂单状态核查（status 文件 age ≈ {(now - float(s.get('ts') or 0)):.0f}s）")
print("=" * 92)
print(f"  {'sym':<12}{'bid':>12}{'ask':>12}{'quote_ts 龄(s)':>16}{'opened_ts':>14}")
rows = []
for sym, v in st.items():
    b = float(v.get("quote_bid") or 0.0)
    a = float(v.get("quote_ask") or 0.0)
    if b <= 0 and a <= 0:
        continue
    qts = float(v.get("quote_ts") or 0.0)
    age = (now - qts) if qts > 0 else -1.0
    op = float(v.get("opened_ts") or 0.0)
    rows.append((sym, b, a, qts, age, op))
rows.sort(key=lambda r: r[4] if r[4] >= 0 else 9e9)
for sym, b, a, qts, age, op in rows[:16]:
    print(f"  {sym:<12}{b:>12.6g}{a:>12.6g}{age:>16.0f}{op:>14.0f}")
print()
print("  读法：quote_ts 龄若很大（几分钟以上）⇒ 挂单**没有每拍刷新**（卡住）。")
print("        quote_ts 龄≈0 ⇒ 在刷新，只是没成交（市场不扫我们的价）。")
