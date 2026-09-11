# -*- coding: utf-8 -*-
"""中长线信号：入场时机解剖（是否在追高/杀跌？）+ 数据一致性校验。"""
from __future__ import annotations

import json
import statistics as st
from collections import defaultdict

from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")


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
            select id,symbol,side,entry_price,exit_price,pnl,leverage,decision_context,
                   opened_at,closed_at,strategy_id
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


def idx_at(series, ts):
    for i, row in enumerate(series):
        if row[0] >= ts:
            return i
    return None


def main():
    trades = load_trades()
    data = load_klines({t["symbol"] for t in trades})
    recs = []
    for t in trades:
        ex, series = pick(data, t["symbol"])
        if not series:
            continue
        i = idx_at(series, int(t["opened_at"].timestamp()))
        if i is None or i < 24:
            continue
        entry = series[i][1]
        # 数据一致性：成交入场价 vs K线开盘价
        px = float(t["entry_price"])
        dev = abs(px - entry) / entry * 100
        side = t["side"]
        sgn = 1 if side == "long" else -1

        def ret(h):
            j = i + h
            if j >= len(series):
                return None
            return (series[j][4] - entry) / entry * 100 * sgn

        # 入场前动量
        pre_1h = (series[i][4] - series[i - 1][4]) / series[i - 1][4] * 100 * sgn
        pre_4h = (series[i][4] - series[i - 4][4]) / series[i - 4][4] * 100 * sgn
        pre_24h = (series[i][4] - series[i - 24][4]) / series[i - 24][4] * 100 * sgn
        # 入场价在最近 N 根 K 线的分位（1.0=最高）
        win24 = series[max(0, i - 23): i + 1]
        hi24 = max(r[2] for r in win24)
        lo24 = min(r[3] for r in win24)
        pos24 = (entry - lo24) / (hi24 - lo24) * 100 if hi24 > lo24 else 50.0
        # 是否在 24h 高点/低点附近
        near_hi = (hi24 - entry) / entry * 100
        near_lo = (entry - lo24) / entry * 100

        recs.append({
            "t": t, "i": i, "entry": entry, "dev": dev, "side": side,
            "pre_1h": pre_1h, "pre_4h": pre_4h, "pre_24h": pre_24h,
            "pos24": pos24, "near_hi": near_hi, "near_lo": near_lo,
            "r1": ret(1), "r3": ret(3), "r6": ret(6), "r12": ret(12),
            "r24": ret(24), "r72": ret(72), "r168": ret(168),
        })

    print(f"样本 {len(recs)}")
    devs = [r["dev"] for r in recs]
    print(f"\n=== 数据一致性：成交入场价 vs 1h K线开盘价 偏差% ===")
    print(f"  n={len(devs)} med={st.median(devs):.3f} mean={sum(devs)/len(devs):.3f} "
          f"p90={sorted(devs)[int(0.9*len(devs))]:.3f} max={max(devs):.3f} >1%的笔数={sum(1 for x in devs if x>1)}")

    def s(key, filt=None):
        v = [r[key] for r in recs if r[key] is not None and (filt is None or filt(r))]
        if not v:
            return None
        w = sum(1 for x in v if x > 0)
        return f"n={len(v):>3} mean={sum(v)/len(v):>+7.3f} med={st.median(v):>+7.3f} wr={w/len(v):.3f}"

    print("\n=== 入场后收益（方向调整） ===")
    for k in ("r1", "r3", "r6", "r12", "r24", "r72", "r168"):
        print(f"  {k:>5}: {s(k)}")
    print("  long  r24:", s("r24", lambda r: r["side"] == "long"))
    print("  short r24:", s("r24", lambda r: r["side"] == "short"))
    print("  long  r72:", s("r72", lambda r: r["side"] == "long"))
    print("  short r72:", s("r72", lambda r: r["side"] == "short"))

    print("\n=== 入场前动量（方向调整）：是否追高/杀跌 ===")
    for k in ("pre_1h", "pre_4h", "pre_24h"):
        print(f"  {k:>7}: {s(k)}")
    print("  long  pre_24h:", s("pre_24h", lambda r: r["side"] == "long"))
    print("  short pre_24h:", s("pre_24h", lambda r: r["side"] == "short"))

    print("\n=== 入场价在 24h 高低区间的分位（0=最低,100=最高） ===")
    pos = [r["pos24"] for r in recs]
    print(f"  all: n={len(pos)} mean={sum(pos)/len(pos):.1f} med={st.median(pos):.1f}")
    for side in ("long", "short"):
        p = [r["pos24"] for r in recs if r["side"] == side]
        if p:
            print(f"  {side}: n={len(p)} mean={sum(p)/len(p):.1f} med={st.median(p):.1f}")

    print("\n=== 分位分桶 → r24 ===")
    buckets = defaultdict(list)
    for r in recs:
        b = int(r["pos24"] // 20)
        if r["r24"] is not None:
            buckets[b].append(r["r24"])
    for b in sorted(buckets):
        v = buckets[b]
        print(f"  分位{b*20:>3}-{b*20+20:>3}%: n={len(v):>3} mean={sum(v)/len(v):>+7.3f} wr={sum(1 for x in v if x>0)/len(v):.3f}")

    print("\n=== 前24h动量分桶 → r24（追涨/追跌检验） ===")
    buckets = defaultdict(list)
    for r in recs:
        if r["r24"] is None or r["pre_24h"] is None:
            continue
        m = r["pre_24h"]
        b = "跌>5%" if m < -5 else ("跌2-5%" if m < -2 else ("跌0-2%" if m < 0 else ("涨0-2%" if m < 2 else ("涨2-5%" if m < 5 else "涨>5%"))))
        buckets[b].append(r["r24"])
    for b in ("跌>5%", "跌2-5%", "跌0-2%", "涨0-2%", "涨2-5%", "涨>5%"):
        v = buckets.get(b) or []
        if v:
            print(f"  {b:>7}: n={len(v):>3} mean={sum(v)/len(v):>+7.3f} wr={sum(1 for x in v if x>0)/len(v):.3f}")

    print("\n=== 按实际持有时间 vs r24 ===")
    g = defaultdict(list)
    for r in recs:
        h = (r["t"]["closed_at"] - r["t"]["opened_at"]).total_seconds() / 3600
        k = "<1h" if h < 1 else ("1-3h" if h < 3 else ("3-8h" if h < 8 else ("8-24h" if h < 24 else ">24h")))
        if r["r24"] is not None:
            g[k].append((h, r["r24"], float(r["t"]["pnl"] or 0)))
    for k in ("<1h", "1-3h", "3-8h", "8-24h", ">24h"):
        v = g.get(k) or []
        if v:
            print(f"  {k:>6}: n={len(v):>3} 实际pnl均值={sum(x[2] for x in v)/len(v):>+7.3f} "
                  f"r24均值={sum(x[1] for x in v)/len(v):>+7.3f}")

    print("\n=== 出场过早检验：实际平仓价 vs 平仓后 24h 价格 ===")
    early = []
    for r in recs:
        i2 = idx_at(series_for(data, r["t"]["symbol"]), int(r["t"]["closed_at"].timestamp())) if r["t"]["closed_at"] else None
        if i2 is None:
            continue
        _, ser = pick(data, r["t"]["symbol"])
        if i2 + 24 >= len(ser):
            continue
        px_exit = float(r["t"]["exit_price"] or 0)
        if px_exit <= 0:
            continue
        after = ser[i2 + 24][4]
        sgn = 1 if r["side"] == "long" else -1
        missed = (after - px_exit) / px_exit * 100 * sgn
        early.append((r["t"]["d"].get("close_reason"), missed, r["t"]["symbol"]))
    g = defaultdict(list)
    for reason, missed, _sym in early:
        k = str(reason or "?")[:28]
        g[k].append(missed)
    print(f"  总样本 {len(early)}；正值=平仓后价格继续朝持仓方向走了多少")
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
        print(f"  {k:<30} n={len(v):>3} 平均错失={sum(v)/len(v):>+7.3f}% ")
    allv = [x[1] for x in early]
    if allv:
        print(f"  合计: n={len(allv)} mean={sum(allv)/len(allv):+.3f}% med={st.median(allv):+.3f}%")


def series_for(data, symbol):
    _, ser = pick(data, symbol)
    return ser or []


if __name__ == "__main__":
    main()
