# -*- coding: utf-8 -*-
"""出场规则反事实模拟：同一批入场信号，不同出场结构下的费后期望。

目的：把「信号质量」与「出场结构」对负期望的贡献分离。
"""
from __future__ import annotations

import json
import statistics as st
from collections import defaultdict

from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

# 成本假设：asterdex taker 0.05% 单边（实测费率中心）+ 滑点 0.03% 单边 + 资金费按 0.01%/8h 计
FEE_SIDE = 0.0005
SLIP_SIDE = 0.0003
FUND_8H = 0.0001
LEV = 4.0  # 典型中长线杠杆（保证金口径收益 = 价格收益 × 杠杆）


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
    """往返成本（价格口径 %）。"""
    c = (FEE_SIDE + SLIP_SIDE) * 2 * 100
    c += (hold_h / 8.0) * FUND_8H * 100
    return c


def simulate(series, i, side, *, sl_pct, tp_pct, max_h, trail_trigger, trail_gap, breakeven_at):
    """逐 1h bar 模拟。返回 (price_ret_pct, hold_h, exit_kind)。"""
    entry = series[i][1]
    sgn = 1 if side == "long" else -1
    sl_price = entry * (1 - sl_pct / 100 * sgn) if side == "long" else entry * (1 + sl_pct / 100)
    tp_price = entry * (1 + tp_pct / 100 * sgn) if side == "long" else entry * (1 - tp_pct / 100)
    peak = 0.0
    moved_be = False
    for k in range(i, min(i + int(max_h) + 1, len(series))):
        ts, o, h, l, c = series[k]
        hold_h = k - i
        if side == "long":
            mfe = (h - entry) / entry * 100
            mae = (l - entry) / entry * 100
        else:
            mfe = (entry - l) / entry * 100
            mae = (entry - h) / entry * 100
        peak = max(peak, mfe)
        # 止损（含保本上移）
        cur_sl = sl_price
        if breakeven_at is not None and peak >= breakeven_at:
            cur_sl = entry if not moved_be else cur_sl
            moved_be = True
        if side == "long":
            if l <= cur_sl:
                return (cur_sl - entry) / entry * 100, hold_h, "sl"
            if h >= tp_price:
                return (tp_price - entry) / entry * 100, hold_h, "tp"
        else:
            if h >= cur_sl:
                return (entry - cur_sl) / entry * 100, hold_h, "sl"
            if l <= tp_price:
                return (entry - tp_price) / entry * 100, hold_h, "tp"
        # 追踪止盈
        if trail_trigger is not None and peak >= trail_trigger:
            trail = peak - trail_gap
            if mfe <= trail:
                return max(trail, 0.0), hold_h, "trail"
        if k == min(i + int(max_h), len(series) - 1):
            ret = (c - entry) / entry * 100 * sgn
            return ret, hold_h, "timeout"
    return 0.0, 0, "no_data"


def summarize(name, rows):
    vals = [r[0] for r in rows]
    if not vals:
        print(f"  {name}: 无样本")
        return
    nets = [r[2] for r in rows]
    w = sum(1 for x in nets if x > 0)
    print(f"  {name:<42} n={len(vals):>3} 毛均值={sum(vals)/len(vals):>+7.3f}% "
          f"费后净均值={sum(nets)/len(nets):>+7.3f}% 胜率={w/len(nets):.3f} "
          f"中位={st.median(nets):>+7.3f}% 总净={sum(nets):>+8.2f}%")
    kinds = defaultdict(int)
    for r in rows:
        kinds[r[1]] += 1
    print(f"      出场分布: {dict(kinds)}")


def main():
    trades = load_trades()
    data = load_klines({t["symbol"] for t in trades})
    prepared = []
    for t in trades:
        ex, series = pick(data, t["symbol"])
        if not series:
            continue
        ts = int(t["opened_at"].timestamp())
        i = None
        for k, row in enumerate(series):
            if row[0] >= ts:
                i = k
                break
        if i is None or i + 2 >= len(series):
            continue
        prepared.append((t, series, i, t["side"]))
    print(f"样本 {len(prepared)}\n")

    print("=== 实际结果（对照） ===")
    act = [(float(t["pnl"] or 0), str(t["d"].get("close_reason") or "?")[:28],
            float(t["pnl"] or 0)) for t, _s, _i, _side in prepared]
    vals = [a[0] for a in act]
    print(f"  实际: n={len(vals)} 均值={sum(vals)/len(vals):+.3f} 总={sum(vals):+.2f} 胜率={sum(1 for x in vals if x>0)/len(vals):.3f}")

    print("\n=== 固定持有期（无止损止盈，仅成本） ===")
    for h in (4, 8, 12, 24, 48, 72, 168):
        rows = []
        for t, series, i, side in prepared:
            j = i + h
            if j >= len(series):
                continue
            entry = series[i][1]
            sgn = 1 if side == "long" else -1
            gross = (series[j][4] - entry) / entry * 100 * sgn
            net = gross - cost_pct(h)
            rows.append((gross, "timeout", net))
        summarize(f"持有 {h}h", rows)

    print("\n=== 固定 SL/TP（当前结构近似：SL 5% / TP 10% / 最长 72h） ===")
    for sl, tp, mh in ((5, 10, 72), (5, 10, 168), (3, 10, 72), (2, 4, 72), (1.5, 3, 48),
                       (5, 15, 168), (8, 24, 336)):
        rows = []
        for t, series, i, side in prepared:
            g, hh, kind = simulate(series, i, side, sl_pct=sl, tp_pct=tp, max_h=mh,
                                   trail_trigger=None, trail_gap=0, breakeven_at=None)
            net = g - cost_pct(hh)
            rows.append((g, kind, net))
        summarize(f"SL{sl}%/TP{tp}%/max{mh}h", rows)

    print("\n=== 追踪止盈结构（无固定 TP） ===")
    for sl, trig, gap, mh in ((5, 2, 1.5, 168), (5, 3, 2, 168), (4, 2, 2, 336),
                              (3, 1.5, 1, 168), (6, 4, 3, 336)):
        rows = []
        for t, series, i, side in prepared:
            g, hh, kind = simulate(series, i, side, sl_pct=sl, tp_pct=999, max_h=mh,
                                   trail_trigger=trig, trail_gap=gap, breakeven_at=None)
            net = g - cost_pct(hh)
            rows.append((g, kind, net))
        summarize(f"SL{sl}% 追踪触发{trig}% 回撤{gap}% max{mh}h", rows)

    print("\n=== 反手对照：同样的出场结构，但方向取反 ===")
    for sl, tp, mh in ((5, 10, 72), (5, 15, 168)):
        rows = []
        for t, series, i, side in prepared:
            flip = "short" if side == "long" else "long"
            g, hh, kind = simulate(series, i, flip, sl_pct=sl, tp_pct=tp, max_h=mh,
                                   trail_trigger=None, trail_gap=0, breakeven_at=None)
            net = g - cost_pct(hh)
            rows.append((g, kind, net))
        summarize(f"反手 SL{sl}%/TP{tp}%/max{mh}h", rows)

    print("\n=== 空头单独看 ===")
    for name, side_filter in (("仅 long 信号", "long"), ("仅 short 信号", "short")):
        for h in (24, 72):
            rows = []
            for t, series, i, side in prepared:
                if side != side_filter:
                    continue
                j = i + h
                if j >= len(series):
                    continue
                entry = series[i][1]
                sgn = 1 if side == "long" else -1
                gross = (series[j][4] - entry) / entry * 100 * sgn
                rows.append((gross, "timeout", gross - cost_pct(h)))
            summarize(f"{name} 持有{h}h", rows)

    print("\n=== 杠杆放大后（保证金口径，×4） ===")
    print("  说明：以上均为价格口径%；乘 4 倍杠杆即为保证金口径收益。成本同样放大。")
    for h in (24, 72):
        rows = []
        for t, series, i, side in prepared:
            j = i + h
            if j >= len(series):
                continue
            entry = series[i][1]
            sgn = 1 if side == "long" else -1
            gross = (series[j][4] - entry) / entry * 100 * sgn
            net = (gross - cost_pct(h)) * LEV
            rows.append((gross * LEV, "timeout", net))
        summarize(f"×{LEV} 持有{h}h", rows)


if __name__ == "__main__":
    main()
