# -*- coding: utf-8 -*-
"""最终归因：把负期望拆成「信号方向 / 持有期 / 出场结构 / 杠杆」四项贡献。

口径：保证金口径净收益（%），杠杆按每笔实际 leverage 计。
成本：taker 0.05% + 滑点 0.03% 单边，资金费 0.01%/8h。
"""
from __future__ import annotations

import json
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


def load():
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
        r["lev"] = float(r["leverage"] or 4.0)
        out.append(r)
    return out


def load_k(symbols):
    data = defaultdict(list)
    with MARKET.connect() as c:
        c.execute(text("set statement_timeout='600000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                data[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return data


def pick(data, sym):
    for ex in ("asterdex", "binance"):
        v = data.get((ex, sym))
        if v and len(v) > 500:
            return v
    return None


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def sim(series, i, side, sl, trig, gap, mh):
    entry = series[i][1]
    peak = 0.0
    for k in range(i, min(i + int(mh) + 1, len(series))):
        _t, _o, h, l, c = series[k]
        hold = k - i
        if side == "long":
            mfe = (h - entry) / entry * 100
            sl_hit = l <= entry * (1 - sl / 100)
        else:
            mfe = (entry - l) / entry * 100
            sl_hit = h >= entry * (1 + sl / 100)
        peak = max(peak, mfe)
        if sl_hit:
            return -sl, hold, "sl"
        if trig and peak >= trig:
            tr = peak - gap
            if mfe <= tr:
                return max(tr, 0.0), hold, "trail"
        if k == min(i + int(mh), len(series) - 1):
            return (c - entry) / entry * 100 * (1 if side == "long" else -1), hold, "timeout"
    return 0.0, 0, "nodata"


def rep(name, rows):
    if not rows:
        print(f"  {name:<46} 无样本")
        return
    nets = [r[0] for r in rows]
    w = sum(1 for x in nets if x > 0)
    print(f"  {name:<46} n={len(nets):>3} 净均值={sum(nets)/len(nets):>+7.3f}% 总净={sum(nets):>+8.1f}% "
          f"胜率={w/len(nets):.3f} 中位={st.median(nets):>+7.3f}%")


def main():
    trades = load()
    data = load_k({t["symbol"] for t in trades})
    prep = []
    for t in trades:
        s = pick(data, t["symbol"])
        if not s:
            continue
        i = next((k for k, row in enumerate(s) if row[0] >= int(t["opened_at"].timestamp())), None)
        if i is None or i + 2 >= len(s):
            continue
        prep.append((t, s, i))
    print(f"样本 {len(prep)} 笔真实 mid/long 交易\n")

    print("=== ① 实际（现状，含全部出场逻辑） ===")
    rep("现状（杠杆口径）", [(float(t["pnl"] or 0) / max(1.0, float(t["original_margin"] or 0)) * 100
                          if False else float(t["pnl"] or 0), 0, 0) for t, _s, _i in prep])
    rep("现状（金额口径，元）", [(float(t["pnl"] or 0), 0, 0) for t, _s, _i in prep])

    print("\n=== ② 拆项：同一批信号、逐项修正（保证金口径净收益%） ===")
    variants = []
    # A 现状：用真实 pnl 换算成保证金口径近似（用 4x 杠杆反推价格口径）
    # 直接用价格口径对照更干净：全部用同一成本口径
    def run(side_filter, h, sl, trig, gap):
        out = []
        for t, s, i in prep:
            if side_filter and t["side"] != side_filter:
                continue
            g, hh, _kind = sim(s, i, t["side"], sl, trig, gap, h)
            out.append(((g - cost_pct(hh)) * t["lev"], 0, 0))
        return out

    rep("A 现状近似(SL4.5/TP8/最长72h ×10x)", run(None, 72, 4.5, None, None))
    rep("B 去掉 short（只做多，其余不变）", run("long", 72, 4.5, None, None))
    rep("C 只做多 + 持有期放到 168h", run("long", 168, 4.5, None, None))
    rep("D 只做多 + 168h + 追踪止盈(SL5/触发2/回撤1.5)", run("long", 168, 5, 2, 1.5))
    rep("E 全部信号 + 168h + 追踪止盈", run(None, 168, 5, 2, 1.5))

    print("\n=== ③ 杠杆敏感度（只做多 + 168h + 追踪止盈，保证金口径） ===")
    for lev in (1, 2, 3, 5, 10):
        out = []
        for t, s, i in prep:
            if t["side"] != "long":
                continue
            g, hh, _k = sim(s, i, "long", 5, 2, 1.5, 168)
            out.append(((g - cost_pct(hh)) * lev, 0, 0))
        rep(f"杠杆 ×{lev}", out)

    print("\n=== ④ short 单项归因（价格口径净%） ===")
    for h in (24, 72, 168):
        out = []
        for t, s, i in prep:
            if t["side"] != "short" or i + h >= len(s):
                continue
            entry = s[i][1]
            g = (entry - s[i + h][4]) / entry * 100
            out.append((g - cost_pct(h), 0, 0))
        rep(f"short 固定持有 {h}h", out)

    print("\n=== ⑤ 入场时机：入场价在 24h 区间分位 vs 168h 净收益（只做多） ===")
    buckets = defaultdict(list)
    for t, s, i in prep:
        if t["side"] != "long" or i < 24 or i + 168 >= len(s):
            continue
        entry = s[i][1]
        win = s[i - 23:i + 1]
        hi = max(r[2] for r in win)
        lo = min(r[3] for r in win)
        pos = (entry - lo) / (hi - lo) * 100 if hi > lo else 50
        g = (s[i + 168][4] - entry) / entry * 100
        b = int(pos // 20) * 20
        buckets[b].append(g - cost_pct(168))
    for b in sorted(buckets):
        v = buckets[b]
        print(f"  分位{b:>3}-{b+20:>3}%: n={len(v):>3} 净均值={sum(v)/len(v):>+7.3f}% 胜率={sum(1 for x in v if x>0)/len(v):.3f}")


if __name__ == "__main__":
    main()
