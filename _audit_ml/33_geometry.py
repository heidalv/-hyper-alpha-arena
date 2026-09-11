# -*- coding: utf-8 -*-
"""止损/持有期几何 vs 波动：证明「1×ATR 止损 + 数小时持有」在结构上必亏。"""
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


def main():
    with ARENA.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        pos = [dict(r._mapping) for r in c.execute(text("""
            select id,symbol,side,entry_price,close_price,sl_price,tp_price,leverage,margin,original_margin,
                   timeframe_tier,trade_nature,close_reason,opened_at,closed_at,partial_realized_pnl,size,original_size
            from paper_positions where timeframe_tier in ('mid','long') and status='closed'
        """)).fetchall()]
        th = [dict(r._mapping) for r in c.execute(text("""
            select thesis_id,symbol,tier,direction,llm_conviction,recommend_open,should_close,created_at,updated_at
            from brain_theses where tier in ('mid','long') order by updated_at desc limit 500
        """)).fetchall()]
    print(f"positions={len(pos)} theses={len(th)}")

    # 拉 K 线
    syms = sorted({p["symbol"] for p in pos} | {t["symbol"] for t in th})
    data = defaultdict(list)
    with MARKET.connect() as c:
        c.execute(text("set statement_timeout='600000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": syms}).fetchall():
                data[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))

    def pick(sym):
        for ex in ("asterdex", "binance"):
            v = data.get((ex, sym))
            if v and len(v) > 500:
                return v
        return None

    # 1) SL 距离 / ATR
    ratios = []
    hits = []
    for p in pos:
        series = pick(p["symbol"])
        if not series or not p["opened_at"] or not p["entry_price"] or not p["sl_price"]:
            continue
        i = next((k for k, row in enumerate(series) if row[0] >= int(p["opened_at"].timestamp())), None)
        if i is None or i < 24:
            continue
        entry = series[i][1]
        atr = st.mean([(series[k][2] - series[k][3]) for k in range(i - 14, i)]) / entry * 100
        sl_d = abs(float(p["sl_price"]) - entry) / entry * 100
        tp_d = abs(float(p["tp_price"]) - entry) / entry * 100 if p["tp_price"] else None
        if atr > 0:
            ratios.append((sl_d / atr, sl_d, atr, tp_d, p["symbol"], p["close_reason"]))
        # SL 在 N 小时内被触及的频率（仅用价格，不看引擎）
        side = p["side"]
        for h in (1, 2, 4, 8, 24, 48):
            if i + h >= len(series):
                continue
            seg = series[i:i + h + 1]
            if side == "long":
                hit = min(r[3] for r in seg) <= entry * (1 - sl_d / 100)
            else:
                hit = max(r[2] for r in seg) >= entry * (1 + sl_d / 100)
            hits.append((h, hit))
    print("\n=== 止损距离 / 1h ATR14 ===")
    if ratios:
        r = [x[0] for x in ratios]
        print(f"  n={len(r)} 中位={st.median(r):.2f}×ATR 均值={sum(r)/len(r):.2f} "
              f"p10={sorted(r)[len(r)//10]:.2f} p90={sorted(r)[len(r)*9//10]:.2f} "
              f"<1×ATR占比={sum(1 for x in r if x<1)/len(r):.2f} <1.5×ATR占比={sum(1 for x in r if x<1.5)/len(r):.2f}")
        print(f"  SL 距离中位={st.median([x[1] for x in ratios]):.2f}%  1h ATR 中位={st.median([x[2] for x in ratios]):.2f}%")
    print("\n=== 纯价格口径：止损在 N 小时内被触及的频率（噪声穿透率） ===")
    g = defaultdict(lambda: [0, 0])
    for h, hit in hits:
        g[h][0] += 1
        g[h][1] += 1 if hit else 0
    for h in sorted(g):
        n, k = g[h]
        print(f"  {h:>3}h 内触及 SL: {k}/{n} = {k/n:.2f}")

    # 2) LLM 方向的前瞻性
    print("\n=== LLM 论题方向的前瞻性（1h K 线，方向调整后 %） ===")
    for h in (12, 24, 72):
        vals = []
        for t in th:
            if t["direction"] not in ("long", "short") or not t["updated_at"]:
                continue
            series = pick(t["symbol"])
            if not series:
                continue
            i = next((k for k, row in enumerate(series) if row[0] >= int(t["updated_at"].timestamp())), None)
            if i is None or i + h >= len(series):
                continue
            entry = series[i][1]
            sgn = 1 if t["direction"] == "long" else -1
            vals.append((series[i + h][4] - entry) / entry * 100 * sgn)
        if vals:
            w = sum(1 for x in vals if x > 0)
            print(f"  {h:>3}h: n={len(vals)} 均值={sum(vals)/len(vals):+.3f}% 中位={st.median(vals):+.3f}% 胜率={w/len(vals):.3f}")
    print("\n=== LLM 方向按 tier 拆分（24h） ===")
    for tier in ("mid", "long"):
        for d in ("long", "short", "neutral"):
            vals = []
            for t in th:
                if t["tier"] != tier or t["direction"] != d or not t["updated_at"]:
                    continue
                series = pick(t["symbol"])
                if not series:
                    continue
                i = next((k for k, row in enumerate(series) if row[0] >= int(t["updated_at"].timestamp())), None)
                if i is None or i + 24 >= len(series):
                    continue
                entry = series[i][1]
                sgn = 1 if d == "long" else (-1 if d == "short" else 1)
                vals.append((series[i + 24][4] - entry) / entry * 100 * sgn)
            if vals:
                w = sum(1 for x in vals if x > 0)
                print(f"  {tier}/{d}: n={len(vals)} 均值={sum(vals)/len(vals):+.3f}% 胜率={w/len(vals):.3f}")

    # 3) 持有期 vs 止损被触及的必然性
    print("\n=== 结论性检查：同一批信号在不同持有期下的最优 SL ===")
    trades = []
    with ARENA.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select symbol,side,entry_price,decision_context,opened_at,strategy_id
            from strategy_trades order by id desc limit 3000
        """)).fetchall()]
    for r in rows:
        if str(r["strategy_id"] or "").startswith("e2e_"):
            continue
        d = parse(r["decision_context"])
        if d.get("nature") not in ("swing", "trend_follow", "position"):
            continue
        if not r["opened_at"] or not r["entry_price"]:
            continue
        trades.append(r)
    for h in (24, 72, 168):
        print(f"  --- 持有上限 {h}h ---")
        for sl in (2, 3, 4.5, 6, 8, 12, 999):
            vals = []
            for t in trades:
                series = pick(t["symbol"])
                if not series:
                    continue
                i = next((k for k, row in enumerate(series) if row[0] >= int(t["opened_at"].timestamp())), None)
                if i is None or i + h >= len(series):
                    continue
                entry = series[i][1]
                side = t["side"]
                g = None
                for k in range(i, i + h + 1):
                    _ts, _o, hi, lo, cl = series[k]
                    if side == "long":
                        if lo <= entry * (1 - sl / 100):
                            g = -sl
                            break
                    else:
                        if hi >= entry * (1 + sl / 100):
                            g = -sl
                            break
                if g is None:
                    g = (series[i + h][4] - entry) / entry * 100 * (1 if side == "long" else -1)
                vals.append(g)
            if vals:
                print(f"      SL={sl:>5}%: n={len(vals)} 毛均值={sum(vals)/len(vals):>+7.3f}% 胜率={sum(1 for x in vals if x>0)/len(vals):.3f}")


if __name__ == "__main__":
    main()
