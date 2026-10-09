"""Are the engine's resting quotes AT the real market?

The engine quotes (status) but nothing fills while the market trades.
Decisive test: engine quote vs live asterdex_book_ticker for the same symbols.
"""
from __future__ import annotations

import io
import json
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"
MKT = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"

s = json.load(open(ROOT + r"\logs\mm_lane_status.json", encoding="utf-8",
                   errors="replace"))
st = s.get("states") or {}
print("=" * 96)
print("引擎挂单价 vs 真实盘口（同一时刻附近）")
print("=" * 96)
print(f"  {'sym':<12}{'引擎bid':>13}{'引擎ask':>13}{'真实bid':>13}{'真实ask':>13}"
      f"{'bid偏差bp':>11}{'ask偏差bp':>11}")
n = 0
with psycopg.connect(MKT, autocommit=True) as m, m.cursor() as mc:
    for sym, v in st.items():
        b = float(v.get("quote_bid") or 0.0)
        a = float(v.get("quote_ask") or 0.0)
        if b <= 0 and a <= 0:
            continue
        s2 = str(sym).upper()
        if not s2.endswith("USDT"):
            s2 += "USDT"
        mc.execute("""
            SELECT bid_px, ask_px FROM asterdex_book_ticker
            WHERE symbol=%s ORDER BY event_ts_ms DESC LIMIT 1
        """, (s2,))
        r = mc.fetchone()
        if not r:
            print(f"  {sym:<12}  真实盘口无数据")
            continue
        rb, ra = float(r[0]), float(r[1])
        db = (b - rb) / rb * 1e4 if b > 0 else float("nan")
        da = (a - ra) / ra * 1e4 if a > 0 else float("nan")
        flag = ""
        if b > 0 and (b > ra + rb * 0.001):
            flag = "  <= 引擎bid高于真实ask??"
        print(f"  {sym:<12}{b:>13.6g}{a:>13.6g}{rb:>13.6g}{ra:>13.6g}"
              f"{db:>11.1f}{da:>11.1f}{flag}")
        n += 1
        if n >= 14:
            break
print()
print("  读法：若引擎挂单价偏离真实盘口很远（数十 bp）⇒ 引擎的 mid 冻结/错误，")
print("        报价不会成交。若贴得很近 ⇒ 价格没问题，是成交模型/数量的问题。")
