"""After the account was restored to $300, is the engine actually sizing for $300?

Checks:
  1. current open positions and their notional (reset_account clears positions)
  2. legs placed AFTER the reset -> are they ~0.05 x 300 = $15, or still ~$495?
  3. what the engine reports for equity / fill_notional
"""
from __future__ import annotations

import io
import json
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"

print("=" * 88)
print("账户已回到 $300 后，引擎是否在为 $300 定规模？")
print("=" * 88)

s = json.load(open(ROOT + r"\logs\mm_lane_status.json", encoding="utf-8",
                   errors="replace"))
print(f"  引擎 equity        = {s.get('equity')}")
print(f"  compound_ratio     = {s.get('compound_ratio')}")
print(f"  fill_notional      = {s.get('fill_notional')}"
      f"   (= compound_ratio x equity)")

st = s.get("states") or {}
tot = 0.0
rows = []
for sym, v in st.items():
    q = float(v.get("qty") or 0.0)
    if abs(q) > 1e-12:
        px = float(v.get("quote_mid") or v.get("avg_px") or 0.0)
        notl = abs(q) * px
        tot += notl
        rows.append((sym, q, notl))
print()
print(f"  未平仓仓位: {len(rows)} 个，合计名义 = ${tot:,.2f}")
for sym, q, notl in sorted(rows, key=lambda x: -x[2])[:8]:
    print(f"    {sym:<12} qty={q:<14.8f} notional=${notl:,.2f}")
if not rows:
    print("    （无 —— reset_account 会先清内存持仓，符合预期）")

print()
print("=" * 88)
print("重置后成交的腿（按时间倒序）")
print("=" * 88)
c = psycopg.connect("postgresql://laobao:alpha_pass@localhost:5432/alpha_arena",
                    autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT to_char(ts AT TIME ZONE 'Asia/Shanghai','HH24:MI:SS'),
           symbol, notional, coalesce(net_bp,0)
    FROM lane_ledger
    WHERE lane_id='mm_asterdex' AND event='fill'
    ORDER BY ts DESC LIMIT 8
""")
for t, sym, notl, nb in cur.fetchall():
    print(f"  {t}  {str(sym):<10} ${float(notl):>10,.2f}  {float(nb):+7.1f}bp")
print()
print("  参考：equity=300 时")
print("    probe 上限 = 0.05 x 300 = $15")
print("    风险模型(15bp) = 300 x 0.005/0.0015 = $1,000")
print("  => 若新腿仍是 ~$495，说明还有一层没跟着权益走（需查）")
