# -*- coding: utf-8 -*-
"""方向分离 + 持有期/出场结构交叉验证 + 对照基准（随机入场）。"""
from __future__ import annotations

import json
import random
import statistics as st
from collections import defaultdict

from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
FEE_SIDE = 0.0005
SLIP_SIDE = 0.0003
FUND_8H = 0.0001


def parse(v):
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except Exception:
        try:
            return eval(v)
        except Exception:
            return {}


def load_trades():
    with ARENA.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id,symbol,side,entry_price,exit_price,pnl,leverage,decision_context,opened_at,closed_at,strategy_id
            from strategy_trades order by id desc limit 3000
        """)).fetchall()]
    out = []
    for r in rows:
        if str(r["strategy_id"] or "").startswith("e2e_"):
            continue
        d = parse(r["decision_context"])
        if d.get("nature") not in ("swing", "trend_follow", "position"):
            continue
        if not r["opened_at"] or not r["entry_price"]:
            continue
        r["d"] = d
        out.append(r)
    return out


def load_klines(symbols):
    data = defaultdict(list)
    syms = list(sorted(symbols))
    with MARKET.connect() as c:
        c.execute(text("set statement_timeout='600000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": syms}).fetchall():
                data[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return data


def pick(data, symbol):
    for ex in ("asterdex", "binance"):
        v = data.get((ex, symbol))
        if v and len(v) > 500:
            return ex, v
    return None, None


def cost_pct(hold_h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (hold_h / 8.0) * FUND_8H * 100


def sim(series, i, side, sl_pct, trig, gap, mh):
    entry = series[i][1]
    sgn = 1 if side == "long" else -1
    peak = 0.0
    for k in range(i, min(i + int(mh) + 1, len(series))):
        _ts, _o, h, l, c = series[k]
        hold = k - i
        if side == "long":
            mfe = (h - entry) / entry * 100
            sl_hit = l <= entry * (1 - sl_pct / 100)
        else:
            mfe = (entry - l) / entry * 100
            sl_hit = h >= entry * (1 + sl_pct / 100)
        peak = max(peak, mfe)
        if sl_hit:
            return -sl_pct, hold, "sl"
        if trig is not None and peak >= trig:
            trail = peak - gap
            if mfe <= trail:
                return max(trail, 0.0), hold, "trail"
        if k == min(i + int(mh), len(series) - 1):
            return (c - entry) / entry * 100 * sgn, hold, "timeout"
    return 0.0, 0, "nodata"


def rep(name, rows):
    if not rows:
        print(f"  {name}: 无样本")
        return
    nets = [r[2] for r in rows]
    w = sum(1 for x in nets if x > 0)
    kinds = defaultdict(int)
    for r in rows:
        kinds[r[1]] += 1
    print(f"  {name:<40} n={len(nets):>3} 净均值={sum(nets)/len(nets):>+7.3f}% 总净={sum(nets):>+8.2f}% "
          f"胜率={w/len(nets):.3f} 中位={st.median(nets):>+7.3f}% {dict(kinds)}")


def main():
    trades = load_trades()
    data = load_klines({t["symbol"] for t in trades})
    prep = []
    for t in trades:
        _ex, series = pick(data, t["symbol"])
        if not series:
            continue
        ts = int(t["opened_at"].timestamp())
        i = next((k for k, row in enumerate(series) if row[0] >= ts), None)
        if i is None or i + 2 >= len(series):
            continue
        prep.append((t, series, i, t["side"]))

    print(f"样本 {len(prep)}\n")
    print("=== 方向分离 × 固定持有期（费后净，价格口径 %） ===")
    for side in ("long", "short"):
        for h in (12, 24, 48, 72, 168):
            rows = []
            for t, series, i, sd in prep:
                if sd != side or i + h >= len(series):
                    continue
                entry = series[i][1]
                sgn = 1 if side == "long" else -1
                g = (series[i + h][4] - entry) / entry * 100 * sgn
                rows.append((g, "timeout", g - cost_pct(h)))
            rep(f"{side} 持有{h}h", rows)
        print()

    print("=== 方向分离 × 追踪止盈（SL5% 触发2% 回撤1.5% max168h） ===")
    for side in ("long", "short"):
        rows = []
        for t, series, i, sd in prep:
            if sd != side:
                continue
            g, hh, kind = sim(series, i, side, 5, 2, 1.5, 168)
            rows.append((g, kind, g - cost_pct(hh)))
        rep(side, rows)

    print("\n=== 对照基准：随机入场（同 symbol 同时间窗内随机时间点，同方向） ===")
    random.seed(42)
    for side in ("long", "short"):
        rows = []
        for t, series, i, sd in prep:
            if sd != side:
                continue
            j = random.randint(24, max(25, len(series) - 73))
            if j + 72 >= len(series):
                continue
            entry = series[j][1]
            sgn = 1 if side == "long" else -1
            g = (series[j + 72][4] - entry) / entry * 100 * sgn
            rows.append((g, "timeout", g - cost_pct(72)))
        rep(f"随机 {side} 持有72h", rows)

    print("\n=== 基准：无条件买入并持有（同批 symbol 同时段，随机时点，72h） ===")
    rows = []
    for t, series, i, sd in prep:
        j = random.randint(24, max(25, len(series) - 73))
        if j + 72 >= len(series):
            continue
        entry = series[j][1]
        g = (series[j + 72][4] - entry) / entry * 100
        rows.append((g, "timeout", g - cost_pct(72)))
    rep("无条件 long 72h", rows)

    print("\n=== 信号方向 vs 无条件方向（超额） ===")
    for side in ("long", "short"):
        a = []
        b = []
        for t, series, i, sd in prep:
            if sd != side or i + 72 >= len(series):
                continue
            entry = series[i][1]
            sgn = 1 if side == "long" else -1
            g = (series[i + 72][4] - entry) / entry * 100 * sgn
            a.append(g - cost_pct(72))
            g0 = (series[i + 72][4] - entry) / entry * 100
            b.append(g0 - cost_pct(72))
        if a:
            print(f"  {side}: 信号方向净={sum(a)/len(a):+.3f}%  无条件long净={sum(b)/len(b):+.3f}%  "
                  f"超额={sum(a)/len(a)-sum(b)/len(b):+.3f}%")

    print("\n=== 按入场时 4h 趋势方向分组（信号是否与趋势一致） ===")
    for side in ("long", "short"):
        g_align, g_against = [], []
        for t, series, i, sd in prep:
            if sd != side or i < 25 or i + 72 >= len(series):
                continue
            entry = series[i][1]
            sgn = 1 if side == "long" else -1
            trend_up = series[i][4] > series[i - 24][4]
            g = (series[i + 72][4] - entry) / entry * 100 * sgn
            (g_align if (trend_up == (side == "long")) else g_against).append(g - cost_pct(72))
        if g_align or g_against:
            print(f"  {side}: 顺24h趋势 n={len(g_align)} 净均值={(sum(g_align)/len(g_align) if g_align else 0):+.3f}% | "
                  f"逆24h趋势 n={len(g_against)} 净均值={(sum(g_against)/len(g_against) if g_against else 0):+.3f}%")


if __name__ == "__main__":
    main()
