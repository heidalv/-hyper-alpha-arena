# -*- coding: utf-8 -*-
"""Z30：限价回调入场 A/B——「信号 bar 收盘市价」vs「挂 -X% 限价等回调」。

§36 发现 hub 的真实优势是**等回调**（中位买在信号 bar 下方 1.1%）。
若把这一行为系统化，就可能在**放宽信号**的同时不牺牲质量（甚至更好）。
本脚本在满足门的 episode 上做同口径 A/B（同一持有窗口，只换入场方式）：

  A. 市价：episode 起点 bar 收盘价成交；
  B. 限价：在收盘价下方 X% 挂单，有效期 N 小时；成交后同样持有到 +24h/+72h。

对 B 未成交的 episode 记为「未参与」（不占用资金），并单独报告成交率。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, learned_ok_prod  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

MARKET_URL = os.getenv("MARKET_DATABASE_URL",
                       "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
DAYS = 45
COST_PCT = (0.0005 + 0.0005) * 2 * 100
CURRENT_UNIVERSE = {"XPL", "UNI", "VIRTUAL", "BTC", "XRP", "SOL", "BNB", "ASTER", "ETH"}


def main() -> int:
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        liq = {}
        for sym, med in c.execute(text("""
            select symbol, percentile_cont(0.5) within group (order by volume * close_price) med
            from crypto_klines
            where period='1h' and timestamp >= extract(epoch from now() - interval '30 days')
            group by 1
        """)).fetchall():
            liq[sym] = float(med or 0)
        h1 = defaultdict(list)
        for ex, sym, ts, o, h, l, cl in c.execute(text("""
            select exchange, symbol, timestamp, open_price, high_price, low_price, close_price
            from crypto_klines
            where period='1h' and timestamp >= extract(epoch from now() - interval '70 days')
            order by symbol, timestamp
        """)).fetchall():
            h1[(ex, sym)].append((int(ts), float(o), float(h), float(l), float(cl)))
        d1 = defaultdict(list)
        for ex, sym, ts, o, h, l, cl in c.execute(text("""
            select exchange, symbol, timestamp, open_price, high_price, low_price, close_price
            from crypto_klines
            where period='1d' and timestamp >= extract(epoch from now() - interval '400 days')
            order by symbol, timestamp
        """)).fetchall():
            d1[(ex, sym)].append((int(ts), float(o), float(h), float(l), float(cl)))

    eps = []
    for sym in {s for (_e, s) in h1}:
        best = None
        for ex in ("asterdex", "binance"):
            s = h1.get((ex, sym))
            ds = d1.get((ex, sym))
            if s and ds and len(s) >= 400 and len(ds) >= 70:
                best = (s, ds)
                break
        if not best:
            continue
        s, ds = best
        reg, pos, chg = build_bar_features(s, ds)
        cutoff = s[-1][0] - DAYS * 86400
        in_ep = False
        for k in range(len(s)):
            if s[k][0] < cutoff:
                continue
            if not learned_ok_prod(reg[k], pos[k], chg[k]):
                in_ep = False
                continue
            if in_ep:
                continue
            in_ep = True
            eps.append({"sym": sym, "k": k, "s": s, "reg": reg[k],
                        "liq": liq.get(sym, 0.0),
                        "in_cur": sym in CURRENT_UNIVERSE})

    print(f"episode 数 = {len(eps)}（近 {DAYS} 天）")

    def market_ret(e, hz):
        k = e["k"]
        px = e["s"][k][4]
        j = k + hz
        return ((e["s"][j][4] - px) / px * 100 - COST_PCT) if j < len(e["s"]) else None

    def limit_ret(e, gap_pct, wait_h, hz):
        """在 px*(1-gap) 挂限价，wait_h 内成交则持有到成交后 hz 小时。"""
        k = e["k"]
        px = e["s"][k][4]
        target = px * (1 - gap_pct / 100.0)
        s = e["s"]
        fill = None
        for m in range(k + 1, min(k + wait_h + 1, len(s))):
            if s[m][3] <= target:          # low ≤ 限价 → 成交
                fill = m
                break
        if fill is None:
            return None
        j = fill + hz
        if j >= len(s):
            return None
        return ((s[j][4] - target) / target * 100 - COST_PCT)

    print(f"\n{'方案':<26}{'n':>6}{'成交率':>8}{'+24h均值%':>11}{'+72h均值%':>11}{'胜率':>8}")
    for label, sub in (("当前宇宙", [e for e in eps if e["in_cur"]]),
                       ("宇宙外", [e for e in eps if not e["in_cur"]]),
                       ("宇宙外·≥$0.5M/h", [e for e in eps if not e["in_cur"]
                                           and e["liq"] >= 5e5])):
        for mode in ("市价", "限价-0.5%/12h", "限价-1.0%/12h", "限价-1.5%/24h"):
            if mode == "市价":
                vals24 = [market_ret(e, 24) for e in sub]
                vals72 = [market_ret(e, 72) for e in sub]
            else:
                gap = float(mode.split("-")[1].split("%")[0])
                wait = int(mode.split("/")[1].rstrip("h"))
                vals24 = [limit_ret(e, gap, wait, 24) for e in sub]
                vals72 = [limit_ret(e, gap, wait, 72) for e in sub]
            v24 = [x for x in vals24 if x is not None]
            v72 = [x for x in vals72 if x is not None]
            fill_rate = len(v24) / len(sub) if sub else float("nan")
            print(f"{label + ' · ' + mode:<26}{len(v24):>6}{fill_rate:>8.1%}"
                  f"{sum(v24)/len(v24) if v24 else float('nan'):>+11.3f}"
                  f"{sum(v72)/len(v72) if v72 else float('nan'):>+11.3f}"
                  f"{sum(1 for x in v24 if x>0)/len(v24) if v24 else float('nan'):>8.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
