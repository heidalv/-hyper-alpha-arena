# -*- coding: utf-8 -*-
"""Z49：净口径（含真实交易费）重跑关键对照（第 6 轮 (e) 项接续）。

背景（§43）：逐笔 `unrealized_pnl` 是**毛**的、`partial_fee_paid` 仅覆盖部分腿（93.7% 为 0），
而我的反事实脚本给**模拟腿**计了 `(FEE_SIDE+SLIP)×2 = 10bp`——两边口径不一致，
使 baseline 白拿 ~10bp/笔。本脚本把**真实成交也按同一费率计费**，重跑两个承重结论：

  A. 门的放行/拦截对照（§33.9/§35.3 的核心）
  B. 同向并发上限（§38 的线上配置依据）

同时给出毛/净两套数字，量化口径对结论的影响。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, learned_ok_prod, load_klines  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
# 费率档：真实记账(3.5bp) / 费(5bp) / 费+滑点(10bp，与反事实脚本一致)
RATES = {"净@3.5bp(实记)": 0.00035, "净@5bp": 0.0005, "净@10bp(费+滑)": 0.001}


def pick(series, sym, ts):
    best = None
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if not v or len(v) <= 200:
            continue
        if v[0][0] <= ts <= v[-1][0] + 86400:
            return v
        if best is None or v[-1][0] > best[-1][0]:
            best = v
    return best


def load(days=75):
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, timeframe_tier, entry_price, close_price, original_size, size,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   opened_at, closed_at, status
            from paper_positions
            where timeframe_tier in ('mid','long')
              and (status='open' or (status='closed'
                   and closed_at >= now() - interval '{int(days)} days'))
            order by opened_at
        """)).fetchall()]
    h1, d1 = load_klines({p["symbol"] for p in rows})
    cache = {}
    recs = []
    for p in rows:
        sym = p["symbol"]
        ts = int(p["opened_at"].timestamp())
        if sym not in cache:
            s = pick(h1, sym, ts)
            ds = pick(d1, sym, ts)
            cache[sym] = (s, build_bar_features(s, ds)) if (
                s and ds and len(s) >= 300 and len(ds) >= 70) else None
        allow = None
        cc = cache.get(sym)
        if cc:
            s, (reg, pos, chg) = cc
            i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
            if i is not None and i > 0 and abs(s[i][0] - ts) <= 7200:
                allow = bool(learned_ok_prod(reg[i], pos[i], chg[i]))
        sz0 = float(p["original_size"] or p["size"] or 0)
        entry = float(p["entry_price"] or 0)
        close = float(p["close_price"] or 0)
        if sz0 <= 0 or entry <= 0:
            continue
        gross = (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                 - float(p["partial_fee_paid"] or 0))
        # 交易费名义：开仓 + 平仓两腿（平仓价缺失时用 entry 兜底）
        notional = sz0 * entry + sz0 * (close if close > 0 else entry)
        recs.append({
            "id": p["id"], "symbol": sym, "tier": str(p["timeframe_tier"]),
            "gross": gross, "notional": notional,
            "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "open_ts": ts, "close_ts": (10**12 if str(p["status"]) == "open"
                                        else int(p["closed_at"].timestamp())),
            "mon": str(p["opened_at"])[:7], "opened": str(p["opened_at"])[:19],
            "allow": allow, "is_open": str(p["status"]) == "open",
        })
    recs.sort(key=lambda r: r["open_ts"])
    return recs


def net(r, rate):
    return r["gross"] - rate * r["notional"]


def agg(sub, rate):
    if not sub:
        return None
    v = [net(r, rate) for r in sub]
    pat = sum(1 for r, x in zip(sub, v) if r["peak"] >= 0.5 and x < 0)
    return {"n": len(sub), "usd": sum(v), "mean": sum(v) / len(v),
            "win": sum(1 for x in v if x > 0) / len(v), "pat": pat / len(sub)}


def replay(recs, cap):
    open_list, keep, skip = [], [], []
    for r in recs:
        open_list = [x for x in open_list if x["close_ts"] > r["open_ts"]]
        if r["is_open"]:
            open_list.append(r)
            continue
        if cap and len(open_list) >= cap:
            skip.append(r)
        else:
            keep.append(r)
            open_list.append(r)
    return keep, skip


def main() -> int:
    recs_all = load(75)
    recs = [r for r in recs_all if not r["is_open"]]
    print(f"样本：已平仓 {len(recs)}（另 {len(recs_all)-len(recs)} 笔持仓）")
    print(f"Σ毛 USD = {sum(r['gross'] for r in recs):+.2f}；"
          f"Σ两腿名义 = {sum(r['notional'] for r in recs):,.0f}")

    print("\n===== A. 毛/净口径总览 =====")
    print(f"{'口径':<18}{'n':>5}{'总USD':>11}{'均值USD':>10}{'胜率':>7}{'模式率':>8}")
    for label, rate in [("毛（现口径）", 0.0)] + list(RATES.items()):
        a = agg(recs, rate)
        print(f"{label:<18}{a['n']:>5}{a['usd']:>+11.2f}{a['mean']:>+10.3f}"
              f"{a['win']:>7.3f}{a['pat']:>8.3f}")

    print("\n===== B. 门放行/拦截（毛 vs 净@10bp）=====")
    print(f"{'口径':<16}{'放行n':>6}{'放行USD':>11}{'拦截n':>6}{'拦截USD':>11}"
          f"{'放行均值':>10}{'拦截均值':>10}{'差':>9}")
    for label, rate in [("毛", 0.0), ("净@5bp", 0.0005), ("净@10bp", 0.001)]:
        a = [r for r in recs if r["allow"]]
        b = [r for r in recs if r["allow"] is False]
        aa, bb = agg(a, rate), agg(b, rate)
        print(f"{label:<16}{aa['n']:>6}{aa['usd']:>+11.2f}{bb['n']:>6}{bb['usd']:>+11.2f}"
              f"{aa['mean']:>+10.3f}{bb['mean']:>+10.3f}{aa['mean']-bb['mean']:>+9.3f}")

    print("\n===== C. 同向并发上限（毛 vs 净@10bp）=====")
    print(f"{'口径':<12}{'cap':<6}{'保留n':>6}{'总USD':>11}{'均值USD':>10}{'模式率':>8}")
    for label, rate in [("毛", 0.0), ("净@5bp", 0.0005), ("净@10bp", 0.001)]:
        for cap in (2, 3, 4, 5, 6, None):
            keep, _ = replay(recs_all, cap)
            a = agg([r for r in keep if not r["is_open"]], rate)
            print(f"{label:<12}{str(cap):<6}{a['n']:>6}{a['usd']:>+11.2f}{a['mean']:>+10.3f}"
                  f"{a['pat']:>8.3f}")
        print()

    print("===== D. 结论是否被口径翻转 =====")
    base = agg(recs, 0.0)
    for label, rate in RATES.items():
        a = agg(recs, rate)
        flip = "**翻转**（由正转负）" if base["usd"] > 0 and a["usd"] <= 0 else "符号不变"
        print(f"  {label}: 毛 {base['usd']:+.2f} → 净 {a['usd']:+.2f}  {flip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
