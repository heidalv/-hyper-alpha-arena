# -*- coding: utf-8 -*-
"""V16 按币 K 线新鲜度：有多少币的 1h/1d 数据滞后到会让闸 fail-open。"""
import datetime as dt

from sqlalchemy import create_engine, text

MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
now = dt.datetime.now()

with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    rows = c.execute(text("""
        select exchange, period, symbol, max(timestamp) as mx
        from crypto_klines
        where period in ('1h','1d') and exchange in ('binance','asterdex')
        group by 1,2,3
    """)).fetchall()

agg = {}
for exch, period, sym, mx in rows:
    lag_min = (now - dt.datetime.fromtimestamp(int(mx))).total_seconds() / 60 if mx else 1e9
    agg.setdefault((exch, period), []).append((sym, lag_min))

for (exch, period), items in sorted(agg.items()):
    fresh = [x for x in items if x[1] <= 130]        # 1h: ≤2.2h；1d: 同样阈值下会大量"过期"
    stale = [x for x in items if x[1] > 130]
    thr = "2.2h" if period == "1h" else "2.2h(=当日未更新)"
    print(f"{exch:<10} {period:>3}: 币数={len(items):>4} 新鲜(≤{thr})={len(fresh):>4} 滞后={len(stale):>4}")
    if stale and len(stale) <= 40:
        print("     滞后币:", ", ".join(f"{s}({l/60:.1f}h)" for s, l in sorted(stale, key=lambda x: -x[1])[:40]))

# 中长线当前候选池（AI 选币 + 固定币）在 1h 上的新鲜度
print("\n== 关键币 1h 滞后（binance / asterdex）==")
targets = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "ADA", "AVAX", "UNI", "TON", "VIRTUAL", "ASTER"]
for sym in targets:
    line = f"  {sym:>8}: "
    for exch in ("binance", "asterdex"):
        r = next((x for x in rows if x[0] == exch and x[1] == "1h" and x[2] == sym), None)
        if r and r[3]:
            lag = (now - dt.datetime.fromtimestamp(int(r[3]))).total_seconds() / 60
            line += f"{exch}={lag:>6.0f}min  "
        else:
            line += f"{exch}=  none  "
    print(line)
