# -*- coding: utf-8 -*-
"""参数标定：用真实 mid/long 入场信号，网格搜索出场参数（SL / 追踪 / 时间上限）。

输出可直接落到 .env 的 EXIT_POLICY_* / AUTO_COIN_MAX_HOLD_HOURS_* 建议值。
口径：价格口径净收益（扣 taker 0.05% + 滑点 0.03% 单边 + 资金费 0.01%/8h）。
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


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load():
    with ARENA.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id,symbol,side,entry_price,decision_context,opened_at,strategy_id
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


def sim(series, i, side, sl, trig, gap, mh):
    entry = series[i][1]
    peak = 0.0
    for k in range(i, min(i + int(mh) + 1, len(series))):
        _t, _o, h, l, c = series[k]
        hold = k - i
        if side == "long":
            mfe = (h - entry) / entry * 100
            if l <= entry * (1 - sl / 100):
                return -sl, hold
        else:
            mfe = (entry - l) / entry * 100
            if h >= entry * (1 + sl / 100):
                return -sl, hold
        peak = max(peak, mfe)
        if trig and peak >= trig:
            tr = peak - gap
            if mfe <= tr:
                return max(tr, 0.0), hold
        if k == min(i + int(mh), len(series) - 1):
            return (c - entry) / entry * 100 * (1 if side == "long" else -1), hold
    return 0.0, 0


def stats_of(vals):
    if not vals:
        return None
    return {
        "n": len(vals),
        "mean": sum(vals) / len(vals),
        "med": st.median(vals),
        "wr": sum(1 for x in vals if x > 0) / len(vals),
        "sum": sum(vals),
    }


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
    print(f"样本 {len(prep)}\n")

    def evaluate(side_filter, sl, trig, gap, mh):
        out = []
        for t, s, i in prep:
            if side_filter and t["side"] != side_filter:
                continue
            g, hh = sim(s, i, t["side"], sl, trig, gap, mh)
            out.append(g - cost_pct(hh))
        return stats_of(out)

    print("=== 网格：只做多（去 short 后）SL × 追踪 × 时间上限 ===")
    print(f"{'SL':>5} {'trig':>5} {'gap':>5} {'maxh':>6} {'n':>4} {'净均值':>9} {'中位':>9} {'胜率':>6} {'总净':>9}")
    best = []
    for sl in (3.0, 4.5, 6.0, 8.0):
        for trig, gap in ((None, None), (2.0, 1.0), (3.0, 1.5), (4.0, 2.0), (6.0, 3.0)):
            for mh in (72, 168, 336):
                stt = evaluate("long", sl, trig, gap, mh)
                if not stt:
                    continue
                print(f"{sl:>5} {str(trig):>5} {str(gap):>5} {mh:>6} {stt['n']:>4} "
                      f"{stt['mean']:>+9.3f} {stt['med']:>+9.3f} {stt['wr']:>6.3f} {stt['sum']:>+9.1f}")
                best.append((stt["mean"], stt["med"], stt["wr"], sl, trig, gap, mh, stt["n"]))
    print("\n=== 按「中位数 × 胜率」稳健排序前 8 ===")
    best.sort(key=lambda x: (x[1] > 0, x[2], x[0]), reverse=True)
    for mean, med, wr, sl, trig, gap, mh, n in best[:8]:
        print(f"  SL={sl}% 追踪触发={trig} 回撤={gap} 上限={mh}h → n={n} 净均值={mean:+.3f}% 中位={med:+.3f}% 胜率={wr:.3f}")

    print("\n=== 当前生产参数对照（mid: SL3% / time_limit 48h / trail 4-1.5 / min_roi 36h:0.8） ===")
    cur = evaluate("long", 3.0, 4.0, 1.5, 48)
    print(f"  只做多+当前参数: {cur}")
    cur2 = evaluate(None, 3.0, 4.0, 1.5, 48)
    print(f"  含空头+当前参数: {cur2}")

    print("\n=== 空头：任何参数组合下是否可能转正 ===")
    for sl in (3.0, 4.5, 6.0, 10.0):
        for trig, gap in ((2.0, 1.0), (4.0, 2.0)):
            stt = evaluate("short", sl, trig, gap, 168)
            if stt:
                print(f"  SL={sl}% 追踪={trig}/{gap} 168h: n={stt['n']} 净均值={stt['mean']:+.3f}% 胜率={stt['wr']:.3f}")

    print("\n=== 建议值（写 .env） ===")
    top = best[0]
    print(f"  EXIT_POLICY_MID_SL_PCT={top[3]}")
    print(f"  EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT={top[4] or 0}")
    print(f"  EXIT_POLICY_MID_TRAILING_CALLBACK_PCT={top[5] or 0}")
    print(f"  EXIT_POLICY_MID_TIME_LIMIT_SEC={top[6]*3600}")
    print("  EXIT_POLICY_MID_MIN_ROI=off   # 时间递减 ROI 实测是「到点小亏强平」的来源")
    print("  MIDLONG_SHORT_MODE=off")
    print(f"  AUTO_COIN_MAX_HOLD_HOURS_MID={top[6]}")


if __name__ == "__main__":
    main()
