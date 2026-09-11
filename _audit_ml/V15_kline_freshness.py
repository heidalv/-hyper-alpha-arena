# -*- coding: utf-8 -*-
"""V15 K 线新鲜度核查：各交易所 × 周期的最后时间戳与滞后（闸依赖它）。"""
import datetime as dt

from sqlalchemy import create_engine, text

MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
now = dt.datetime.now()

with MARKET.connect() as c:
    c.execute(text("set statement_timeout='300000'"))
    rows = c.execute(text("""
        select exchange, period, count(*) as n, max(timestamp) as mx
        from crypto_klines
        where period in ('1m','5m','15m','1h','4h','1d')
        group by 1,2 order by 1,2
    """)).fetchall()
    print(f"{'exchange':<12} {'period':>6} {'rows':>10} {'last_ts':>20} {'滞后':>10}")

for exch, period, n, mx in rows:
    ts = dt.datetime.fromtimestamp(int(mx)) if mx else None
    lag = (now - ts).total_seconds() if ts else None
    lag_s = f"{lag/60:.0f}min" if lag is not None and lag < 86400 else (f"{lag/3600:.1f}h" if lag else "n/a")
    print(f"{exch:<12} {period:>6} {n:>10} {str(ts)[:19]:>20} {lag_s:>10}")

print("\n== 中长线候选币（BTC/ETH/SOL/BNB/XRP/ADA/TON）在 binance 与 asterdex 的 1h 最新 ==")
for sym in ("BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "TON"):
    line = f"  {sym:>5}: "
    for exch in ("binance", "asterdex"):
        r = c.execute(text("""select max(timestamp) from crypto_klines
                              where period='1h' and exchange=:e and symbol=:s"""),
                      {"e": exch, "s": sym}).fetchone()
        if r and r[0]:
            t = dt.datetime.fromtimestamp(int(r[0]))
            line += f"{exch}={str(t)[:16]}({(now-t).total_seconds()/60:.0f}min)  "
        else:
            line += f"{exch}=none  "
    print(line)
