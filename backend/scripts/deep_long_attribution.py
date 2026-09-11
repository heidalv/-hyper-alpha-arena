# -*- coding: utf-8 -*-
"""多头深度解析（第十六轮）：历史多头逐笔归因 + counterfactual。

空头侧第十五轮（deep_short_attribution.py）发现：入场毒性而非出场造成负期望。
本脚本是多头侧镜像：把 165 笔 paper_positions 多头 + strategy_trades 真实多头
逐笔分解：
  A. 入场质量：同一入场点持有 72h / 14d（无 SL/成本）能赚多少？
  B. 出场贡献：实际出场 vs 持有反事实的差异（正值=出场更好）。
  C. 入场特征（regime / pos24 / chg24 / RSI14 / MFE / MAE）分桶归因。

输出：`data/long_attribution.json`
用法：.venv\\Scripts\\python.exe backend/scripts/deep_long_attribution.py
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "long_attribution.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005


def load_longs():
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, close_price, sl_price, original_size, size,
                   partial_realized_pnl, timeframe_tier, trade_nature, close_reason,
                   opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and side='long' and status='closed'
            order by opened_at
        """)).fetchall()]
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        st_rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, exit_price as close_price, null as sl_price,
                   position_size as original_size, position_size as size,
                   null as partial_realized_pnl, null as timeframe_tier, null as trade_nature,
                   null as close_reason, opened_at, closed_at, decision_context, strategy_id
            from strategy_trades
            where side='long' and status='closed' and entry_price is not null
            order by opened_at desc limit 3000
        """)).fetchall()]
    have = {(r["symbol"], r["opened_at"]) for r in rows}
    for r in st_rows:
        if str(r["strategy_id"] or "").startswith("e2e_"):
            continue
        dc = r.get("decision_context")
        if isinstance(dc, str):
            try:
                dc = json.loads(dc)
            except Exception:
                try:
                    dc = eval(dc)  # noqa: S307
                except Exception:
                    dc = {}
        nature = (dc or {}).get("nature")
        if nature not in ("swing", "trend_follow", "position"):
            continue
        r["timeframe_tier"] = "mid" if nature == "swing" else "long"
        if (r["symbol"], r["opened_at"]) in have:
            continue
        have.add((r["symbol"], r["opened_at"]))
        rows.append(r)
    return rows


def load_klines(symbols):
    h1 = defaultdict(list)
    d1 = defaultdict(list)
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                h1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1d' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                d1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return h1, d1


def pick(series, sym):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 300:
            return v
    return None


def regime_at(dseries, ts):
    if not dseries:
        return "unknown"
    i = next((k for k, row in enumerate(dseries) if row[0] >= ts), len(dseries) - 1)
    if i > 0:
        i -= 1
    if i < 60:
        return "unknown"
    closes = [row[4] for row in dseries[max(0, i - 200): i + 1]]
    ema = sum(closes) / len(closes)
    px = closes[-1]
    base = dseries[i - 60][4] if i >= 60 else closes[0]
    mom60 = (px / base - 1.0) if base > 0 else 0.0
    if px > ema and mom60 > 0.05:
        return "up"
    if px < ema and mom60 < -0.05:
        return "down"
    return "chop"


def main() -> int:
    rows = load_longs()
    h1, d1 = load_klines({r["symbol"] for r in rows})
    recs = []
    for r in rows:
        s = pick(h1, r["symbol"])
        ds = pick(d1, r["symbol"])
        if not s or not ds:
            continue
        ts = int(r["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i < 24:
            continue
        entry = float(r["entry_price"] or 0)
        close = float(r["close_price"] or 0)
        if entry <= 0:
            continue
        actual_ret = (close - entry) / entry * 100
        hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600 if r["closed_at"] else 0
        cost = (FEE_SIDE + SLIP_SIDE) * 2 * 100
        actual_net = actual_ret - cost
        # counterfactual：72h / 14d 持有（无 SL）
        k72 = i + 72
        ret72 = (s[min(k72, len(s) - 1)][4] - entry) / entry * 100
        net72 = ret72 - cost
        j = i + 336
        if j >= len(s):
            continue
        ret14 = (s[j][4] - entry) / entry * 100
        net14 = ret14 - cost
        # MFE / MAE（实际持仓期内）
        end = min(i + int(hold_h) + 1, len(s) - 1)
        mfe = max((s[k][2] - entry) / entry * 100 for k in range(i, end + 1))
        mae = min((s[k][3] - entry) / entry * 100 for k in range(i, end + 1))
        # 入场特征
        win = s[max(0, i - 23): i + 1]
        hi = max(x[2] for x in win)
        lo = min(x[3] for x in win)
        pos24 = (entry - lo) / (hi - lo) * 100 if hi > lo else 50.0
        chg24 = (s[i][4] / s[i - 24][4] - 1) * 100 if s[i - 24][4] > 0 else 0.0
        rsi14 = 50.0
        if i >= 100:
            gains, losses = [], []
            for k in range(i - 14, i + 1):
                d = s[k][4] - s[k - 1][4]
                gains.append(max(d, 0.0))
                losses.append(max(-d, 0.0))
            ag = sum(gains) / 14.0
            al = sum(losses) / 14.0
            rsi14 = 100.0 - 100.0 / (1.0 + ag / al) if al > 0 else 100.0
        regime = regime_at(ds, ts)
        learned_pass = (regime == "up" and pos24 < 60.0 and 35.0 <= rsi14 <= 65.0
                        and -5.0 <= chg24 < 6.0)
        recs.append({
            "id": r["id"], "symbol": r["symbol"], "regime": regime,
            "pos24": round(pos24, 1), "chg24": round(chg24, 2),
            "rsi14": round(rsi14, 1), "learned_pass": learned_pass,
            "hold_h": round(hold_h, 2),
            "actual_ret": round(actual_ret, 3), "actual_net": round(actual_net, 3),
            "ret72": round(ret72, 3), "net72": round(net72, 3),
            "ret14d": round(ret14, 3), "net14d": round(net14, 3),
            "mfe": round(mfe, 3), "mae": round(mae, 3),
            "exit_loss": round(actual_ret - ret72, 3),
            "exit_loss14": round(actual_ret - ret14, 3),
        })

    print(f"可解析样本: {len(recs)}")

    def agg(key):
        g = defaultdict(list)
        for r in recs:
            g[r[key]].append(r)
        print(f"\n=== 按 {key} ===")
        for k in sorted(g, key=lambda x: str(x)):
            v = g[k]
            if len(v) < 3:
                continue
            print(f"  {k:<8} n={len(v):>3} 实际净={sum(x['actual_net'] for x in v)/len(v):>+8.2f}% "
                  f"72h净={sum(x['net72'] for x in v)/len(v):>+8.2f}% "
                  f"14d净={sum(x['net14d'] for x in v)/len(v):>+8.2f}% "
                  f"出场差(实际-72h)={sum(x['exit_loss'] for x in v)/len(v):>+8.2f}% "
                  f"MFE={sum(x['mfe'] for x in v)/len(v):>+8.2f}% MAE={sum(x['mae'] for x in v)/len(v):>+8.2f}%")
    agg("regime")

    print("\n=== 按入场分位 ===")
    for lo, hi, label in [(0, 40, "<40"), (40, 60, "40-60"), (60, 80, "60-80"), (80, 101, ">=80")]:
        v = [r for r in recs if lo <= r["pos24"] < hi]
        if len(v) < 3:
            continue
        print(f"  {label:<7} n={len(v):>3} 实际净={sum(x['actual_net'] for x in v)/len(v):>+8.2f}% "
              f"72h净={sum(x['net72'] for x in v)/len(v):>+8.2f}% 14d净={sum(x['net14d'] for x in v)/len(v):>+8.2f}% "
              f"出场差(实际-72h)={sum(x['exit_loss'] for x in v)/len(v):>+8.2f}%")

    print("\n=== 按入场前24h涨跌 ===")
    for lo, hi, label in [(-1e9, -5, "<-5%"), (-5, 0, "-5~0%"), (0, 3, "0~3%"),
                          (3, 6, "3~6%"), (6, 1e9, ">=+6%")]:
        v = [r for r in recs if lo <= r["chg24"] < hi]
        if len(v) < 3:
            continue
        print(f"  {label:<7} n={len(v):>3} 实际净={sum(x['actual_net'] for x in v)/len(v):>+8.2f}% "
              f"72h净={sum(x['net72'] for x in v)/len(v):>+8.2f}% "
              f"中位持有={st.median(x['hold_h'] for x in v):>5.1f}h")

    print("\n=== learned 多头准入测试（up regime）===")
    up = [r for r in recs if r["regime"] == "up"]
    for label, v in [("全量up", up), ("通过(pos<60&RSI35-65&chg-5~6)", [r for r in up if r["learned_pass"]]),
                     ("不通过", [r for r in up if not r["learned_pass"]])]:
        if len(v) < 3:
            continue
        print(f"  {label:<34} n={len(v):>3} 实际净={sum(x['actual_net'] for x in v)/len(v):>+8.2f}% "
              f"72h净={sum(x['net72'] for x in v)/len(v):>+8.2f}% "
              f"14d净={sum(x['net14d'] for x in v)/len(v):>+8.2f}% "
              f"MFE={sum(x['mfe'] for x in v)/len(v):>+6.2f}%")

    print("\n=== 时间切分（2026-08-01 前后）===")
    half = "2026-08-01T00:00:00+00:00"
    ids_a = {r["id"] for r in rows if str(r["opened_at"]) < half}
    v_a = [r for r in recs if r["id"] in ids_a]
    v_b = [r for r in recs if r["id"] not in ids_a]
    for label, v in [("2026-08前", v_a), ("2026-08后", v_b)]:
        if len(v) < 3:
            continue
        print(f"  {label:<10} n={len(v):>3} 实际净={sum(x['actual_net'] for x in v)/len(v):>+8.2f}% "
              f"72h净={sum(x['net72'] for x in v)/len(v):>+8.2f}% "
              f"14d净={sum(x['net14d'] for x in v)/len(v):>+8.2f}%")

    print("\n=== 逐币 (n>=4) ===")
    bysym = defaultdict(list)
    for r in recs:
        bysym[r["symbol"]].append(r)
    for sym in sorted(bysym, key=lambda k: -len(bysym[k])):
        v = bysym[sym]
        if len(v) < 4:
            continue
        print(f"  {sym:<12} n={len(v):>3} 实际净={sum(x['actual_net'] for x in v)/len(v):>+8.2f}% "
              f"72h净={sum(x['net72'] for x in v)/len(v):>+8.2f}% "
              f"learned通过占比={sum(1 for x in v if x['learned_pass'])/len(v):.2f}")

    print("\n=== 总体 ===")
    n = len(recs)
    print(f"  实际净={sum(x['actual_net'] for x in recs)/n:+.2f}% | "
          f"72h持有净={sum(x['net72'] for x in recs)/n:+.2f}% | "
          f"14d持有净={sum(x['net14d'] for x in recs)/n:+.2f}%")
    print(f"  出场差(vs72h)={sum(x['exit_loss'] for x in recs)/n:+.2f}% | "
          f"出场差(vs14d)={sum(x['exit_loss14'] for x in recs)/n:+.2f}%")
    print(f"  MFE均值={sum(x['mfe'] for x in recs)/n:+.2f}% | MAE均值={sum(x['mae'] for x in recs)/n:+.2f}%")
    print(f"  MFE>50bp 占比={sum(1 for x in recs if x['mfe']>0.5)/n:.2f} | MFE>1% 占比={sum(1 for x in recs if x['mfe']>1)/n:.2f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": n, "recs": recs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
