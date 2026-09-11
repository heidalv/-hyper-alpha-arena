# -*- coding: utf-8 -*-
"""Z29：扩宇宙的质量验证——当前 9 币 vs 宇宙外币种的「门条件前向收益」对比。

Z28 显示全市场每天有 125.5 段满足门的机会，当前 9 币宇宙只覆盖 5.3%。
若宇宙外币种的门条件同样有正前向收益，则「扩宇宙」是**不放宽门**的提流量杠杆。

方法（与 §26/§27 条件验证同口径）：对每个满足门的 episode 起点 bar 收盘价，
计算 +4h/+24h/+72h 前向收益（扣单边 5bp 费 + 5bp 滑点），按
「当前宇宙 / 宇宙外」× 「up / chop」分组，并做 bootstrap。
"""
from __future__ import annotations

import os
import random
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
SEED = 20260910
DAYS = 45
COST_PCT = (0.0005 + 0.0005) * 2 * 100  # 双边费+滑点
CURRENT_UNIVERSE = {"XPL", "UNI", "VIRTUAL", "BTC", "XRP", "SOL", "BNB", "ASTER", "ETH"}


def main() -> int:
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        # 流动性代理：近 30 天「小时美元成交额」中位数
        liq = {}
        for sym, ex, med in c.execute(text("""
            select symbol, exchange,
                   percentile_cont(0.5) within group (order by volume * close_price) med
            from crypto_klines
            where period='1h' and timestamp >= extract(epoch from now() - interval '30 days')
            group by 1,2
        """)).fetchall():
            v = float(med or 0)
            if sym not in liq or v > liq[sym][1]:
                liq[sym] = (ex, v)
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

    syms = {s for (_e, s) in h1.keys()}
    recs = []
    for sym in syms:
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
            ok = learned_ok_prod(reg[k], pos[k], chg[k])
            if not ok:
                in_ep = False
                continue
            if in_ep:          # 只取段落起点，避免同一段重复计数
                continue
            in_ep = True
            px = s[k][4]
            if px <= 0:
                continue
            rec = {"sym": sym, "reg": reg[k], "ts": s[k][0], "px": px,
                   "in_cur": sym in CURRENT_UNIVERSE,
                   "liq": liq.get(sym, ("", 0.0))[1]}
            for hz in (4, 24, 72):
                j = k + hz
                rec[f"f{hz}"] = ((s[j][4] - px) / px * 100 - COST_PCT) if j < len(s) else None
            recs.append(rec)

    print(f"episode 数 = {len(recs)}（近 {DAYS} 天，{len({r['sym'] for r in recs})} 个币）")
    print(f"\n{'分组':<26}{'n':>6}{'+4h均值%':>10}{'+24h均值%':>11}{'+72h均值%':>11}"
          f"{'+24h胜率':>10}")
    groups = [
        ("当前宇宙 · 全部", [r for r in recs if r["in_cur"]]),
        ("当前宇宙 · up", [r for r in recs if r["in_cur"] and r["reg"] == "up"]),
        ("当前宇宙 · chop", [r for r in recs if r["in_cur"] and r["reg"] == "chop"]),
        ("宇宙外 · 全部", [r for r in recs if not r["in_cur"]]),
        ("宇宙外 · up", [r for r in recs if not r["in_cur"] and r["reg"] == "up"]),
        ("宇宙外 · chop", [r for r in recs if not r["in_cur"] and r["reg"] == "chop"]),
    ]
    for label, sub in groups:
        if not sub:
            continue
        def m(key):
            v = [r[key] for r in sub if r[key] is not None]
            return sum(v) / len(v) if v else float("nan")
        win = [r["f24"] for r in sub if r["f24"] is not None]
        print(f"{label:<26}{len(sub):>6}{m('f4'):>+10.3f}{m('f24'):>+11.3f}"
              f"{m('f72'):>+11.3f}{sum(1 for x in win if x>0)/len(win) if win else float('nan'):>10.3f}")

    print("\n=== bootstrap：宇宙外 − 当前宇宙（+24h / +72h 前向收益）===")
    a = [r["f24"] for r in recs if not r["in_cur"] and r["f24"] is not None]
    b = [r["f24"] for r in recs if r["in_cur"] and r["f24"] is not None]
    for key, x, y in (("+24h", a, b),
                      ("+72h", [r["f72"] for r in recs if not r["in_cur"] and r["f72"] is not None],
                       [r["f72"] for r in recs if r["in_cur"] and r["f72"] is not None])):
        if len(x) < 10 or len(y) < 10:
            continue
        rnd = random.Random(SEED)
        nx, ny = len(x), len(y)
        boots = sorted(sum(x[rnd.randrange(nx)] for _ in range(nx)) / nx
                       - sum(y[rnd.randrange(ny)] for _ in range(ny)) / ny
                       for _ in range(4000))
        obs = sum(x) / nx - sum(y) / ny
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  {key}: 差={obs:+.3f}% CI[{lo:+.3f},{hi:+.3f}] "
              f"{'显著' if lo > 0 or hi < 0 else '不显著(跨0)'}（宇宙外 n={nx} / 宇宙内 n={ny}）")

    print("\n=== 宇宙外：按流动性分档（小时美元额中位数）===")
    print(f"  {'档位':<22}{'n':>6}{'+24h均值%':>11}{'+72h均值%':>11}{'胜率':>8}")
    tiers = [(2e6, 1e12, "≥$2M/h"), (5e5, 2e6, "$0.5-2M/h"),
             (1e5, 5e5, "$0.1-0.5M/h"), (0, 1e5, "<$0.1M/h")]
    for lo, hi, label in tiers:
        sub = [r for r in recs if not r["in_cur"] and lo <= r["liq"] < hi]
        if not sub:
            continue
        def mm(key):
            v = [r[key] for r in sub if r[key] is not None]
            return sum(v) / len(v) if v else float("nan")
        win = [r["f24"] for r in sub if r["f24"] is not None]
        print(f"  {label:<22}{len(sub):>6}{mm('f24'):>+11.3f}{mm('f72'):>+11.3f}"
              f"{sum(1 for x in win if x>0)/len(win) if win else float('nan'):>8.3f}")

    print("\n=== 高流动性宇宙外 × regime（≥$2M/h）===")
    print(f"  {'分组':<26}{'n':>6}{'+24h均值%':>11}{'+72h均值%':>11}{'胜率':>8}")
    for label, sub in (
        ("≥$2M/h · 全部", [r for r in recs if not r["in_cur"] and r["liq"] >= 2e6]),
        ("≥$2M/h · up", [r for r in recs if not r["in_cur"] and r["liq"] >= 2e6
                         and r["reg"] == "up"]),
        ("≥$2M/h · chop", [r for r in recs if not r["in_cur"] and r["liq"] >= 2e6
                           and r["reg"] == "chop"]),
        ("当前宇宙 · up", [r for r in recs if r["in_cur"] and r["reg"] == "up"]),
    ):
        if not sub:
            continue
        def m3(key):
            v = [r[key] for r in sub if r[key] is not None]
            return sum(v) / len(v) if v else float("nan")
        win = [r["f24"] for r in sub if r["f24"] is not None]
        print(f"  {label:<26}{len(sub):>6}{m3('f24'):>+11.3f}{m3('f72'):>+11.3f}"
              f"{sum(1 for x in win if x>0)/len(win) if win else float('nan'):>8.3f}")

    print("\n=== 宇宙外 TOP20 币种的 +24h 前向 ===")
    bysym = defaultdict(list)
    for r in recs:
        if not r["in_cur"] and r["f24"] is not None:
            bysym[r["sym"]].append(r["f24"])
    print(f"  {'symbol':<12}{'n':>4}{'+24h均值%':>11}{'胜率':>8}")
    for sym, v in sorted(bysym.items(), key=lambda x: -sum(x[1]) / len(x[1]))[:20]:
        print(f"  {sym:<12}{len(v):>4}{sum(v)/len(v):>+11.3f}"
              f"{sum(1 for x in v if x>0)/len(v):>8.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
