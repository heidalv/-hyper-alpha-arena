# -*- coding: utf-8 -*-
"""中长线信号事件研究：入场信号是否有前瞻边际？（只读）

对每笔真实 mid/long 交易（排除 e2e 测试产物），取入场时刻与方向的 1h K 线，
计算多个持有期后的前瞻收益、最大有利/不利偏移（MFE/MAE），
并与实际成交结果、反手（相反方向）对照。
"""
from __future__ import annotations

import json
import statistics as st
from collections import defaultdict

from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

HORIZONS = [1, 4, 12, 24, 48, 72, 168]


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
            select id,symbol,side,entry_price,exit_price,pnl,pnl_pct,leverage,
                   decision_context,signal_context,ai_reasoning,opened_at,closed_at,strategy_id
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
        r["s"] = parse(r["signal_context"])
        out.append(r)
    return out


def load_klines(symbols):
    """返回 {symbol: [(ts, o,h,l,c), ...]} 按时间升序。"""
    data = defaultdict(list)
    syms = tuple(sorted(symbols))
    with MARKET.connect() as c:
        c.execute(text("set statement_timeout='600000'"))
        for exch in ("asterdex", "binance"):
            rows = c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines
                where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(syms)}).fetchall()
            for s, ts, o, h, l, cl in rows:
                data[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return data


def pick_series(data, symbol):
    """优先 asterdex（实盘交易所），回退 binance；返回 (name, list)。"""
    for ex in ("asterdex", "binance"):
        v = data.get((ex, symbol))
        if v and len(v) > 500:
            return ex, v
    return None, None


def fwd(series, entry_ts, side, horizon_h):
    """返回 (ret_pct, mfe_pct, mae_pct) —— 相对入场价，方向调整后。"""
    # 找 >= entry_ts 的第一根
    lo, hi = 0, len(series) - 1
    start = None
    for i, row in enumerate(series):
        if row[0] >= entry_ts:
            start = i
            break
    if start is None:
        return None
    end = start + horizon_h
    if end >= len(series):
        return None
    entry = series[start][1]  # 入场 bar 的 open
    seg = series[start:end + 1]
    if side == "long":
        ret = (seg[-1][4] - entry) / entry * 100
        mfe = (max(r[2] for r in seg) - entry) / entry * 100
        mae = (min(r[3] for r in seg) - entry) / entry * 100
    else:
        ret = (entry - seg[-1][4]) / entry * 100
        mfe = (entry - min(r[3] for r in seg)) / entry * 100
        mae = (entry - max(r[2] for r in seg)) / entry * 100
    return ret, mfe, mae


def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    wins = [x for x in xs if x > 0]
    return {
        "n": len(xs),
        "mean": round(sum(xs) / len(xs), 3),
        "med": round(st.median(xs), 3),
        "wr": round(len(wins) / len(xs), 3),
        "p10": round(sorted(xs)[max(0, len(xs) // 10)], 3),
        "p90": round(sorted(xs)[min(len(xs) - 1, 9 * len(xs) // 10)], 3),
    }


def main():
    trades = load_trades()
    print(f"真实 mid/long 交易: {len(trades)}")
    syms = {t["symbol"] for t in trades}
    print("symbols:", sorted(syms))
    data = load_klines(syms)

    rows = []
    for t in trades:
        ex, series = pick_series(data, t["symbol"])
        if not series:
            continue
        entry_ts = int(t["opened_at"].timestamp())
        rec = {"t": t, "ex": ex, "side": t["side"]}
        for h in HORIZONS:
            rec[h] = fwd(series, entry_ts, t["side"], h)
            rec[-h] = fwd(series, entry_ts, "short" if t["side"] == "long" else "long", h)
        rows.append(rec)

    print(f"可评估样本: {len(rows)}\n")
    print("=== 前瞻收益（方向调整后，%）—— 信号是否有效 ===")
    print(f"{'H':>5} {'n':>4} {'mean':>8} {'med':>8} {'wr':>6} {'p10':>8} {'p90':>8} | {'反向mean':>9} {'反向wr':>7}")
    for h in HORIZONS:
        s = stats([r[h][0] for r in rows if r[h]])
        sr = stats([r[-h][0] for r in rows if r[-h]])
        if s:
            print(f"{h:>5} {s['n']:>4} {s['mean']:>8} {s['med']:>8} {s['wr']:>6} {s['p10']:>8} {s['p90']:>8} | "
                  f"{sr['mean'] if sr else 0:>9} {sr['wr'] if sr else 0:>7}")

    print("\n=== MFE / MAE（%）—— 出场结构是否匹配信号 ===")
    print(f"{'H':>5} {'MFE均值':>9} {'MAE均值':>9} {'MFE中位':>9} {'MAE中位':>9} {'MFE>5%占比':>10} {'MAE<-5%占比':>11}")
    for h in HORIZONS:
        mfe = [r[h][1] for r in rows if r[h]]
        mae = [r[h][2] for r in rows if r[h]]
        if mfe:
            print(f"{h:>5} {sum(mfe)/len(mfe):>9.2f} {sum(mae)/len(mae):>9.2f} "
                  f"{st.median(mfe):>9.2f} {st.median(mae):>9.2f} "
                  f"{sum(1 for x in mfe if x>5)/len(mfe):>10.2f} {sum(1 for x in mae if x<-5)/len(mae):>11.2f}")

    print("\n=== 按方向拆分（24h 前瞻） ===")
    for side in ("long", "short"):
        s = stats([r[24][0] for r in rows if r[24] and r["side"] == side])
        print(f"  {side}: {s}")

    print("\n=== 按 regime 拆分（24h 前瞻） ===")
    g = defaultdict(list)
    for r in rows:
        if r[24]:
            g[r["t"]["d"].get("regime")].append(r[24][0])
    for k in sorted(g, key=lambda x: str(x)):
        print(f"  {k}: {stats(g[k])}")

    print("\n=== 按 close_reason 拆分（实际 pnl vs 24h 前瞻） ===")
    g = defaultdict(lambda: {"pnl": [], "fwd": []})
    for r in rows:
        k = str(r["t"]["d"].get("close_reason") or "?")[:32]
        g[k]["pnl"].append(float(r["t"]["pnl"] or 0))
        if r[24]:
            g[k]["fwd"].append(r[24][0])
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]["pnl"])):
        p = v["pnl"]
        f = v["fwd"]
        print(f"  {k:<34} n={len(p):>3} pnl_avg={sum(p)/len(p):>7.3f} "
              f"fwd24_avg={(sum(f)/len(f) if f else 0):>7.3f} n_fwd={len(f)}")

    print("\n=== 信号是否有区分度：signal_context 特征分桶 → 24h 前瞻 ===")
    feats = defaultdict(list)
    for r in rows:
        if not r[24]:
            continue
        for k, v in r["t"]["s"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                feats[k].append((float(v), r[24][0]))
    for k, pairs in sorted(feats.items(), key=lambda kv: -len(kv[1])):
        if len(pairs) < 20:
            continue
        pairs.sort()
        n = len(pairs)
        thirds = [pairs[: n // 3], pairs[n // 3: 2 * n // 3], pairs[2 * n // 3:]]
        parts = []
        for seg in thirds:
            if seg:
                vals = [y for _, y in seg]
                parts.append(f"{sum(vals)/len(vals):+.2f}")
        print(f"  {k:<20} n={n:>3} 低/中/高桶 fwd24: {' | '.join(parts)}")

    # 汇总：实际 vs 反事实
    print("\n=== 实际 vs 反事实（同一批信号） ===")
    act = [float(r["t"]["pnl"] or 0) for r in rows]
    print(f"  实际 pnl: n={len(act)} sum={sum(act):.2f} avg={sum(act)/len(act):.3f}")
    for h in (24, 72, 168):
        f = [r[h][0] for r in rows if r[h]]
        if f:
            print(f"  固定持有 {h}h（同方向）: n={len(f)} avg={sum(f)/len(f):+.3f}% 胜率={sum(1 for x in f if x>0)/len(f):.3f}")
            fr = [r[-h][0] for r in rows if r[-h]]
            print(f"  固定持有 {h}h（反方向）: n={len(fr)} avg={sum(fr)/len(fr):+.3f}% 胜率={sum(1 for x in fr if x>0)/len(fr):.3f}")


if __name__ == "__main__":
    main()
