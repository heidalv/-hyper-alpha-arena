# -*- coding: utf-8 -*-
"""V9 位置闸前提的交叉验证：样本(strategy_trades vs paper_positions) × 指标(前向收益 vs 已实现PnL)。

目的：V8 显示「闸应拒的高分位」反而赚钱、闸放行的低分位在亏，与 9/9 报告相反。
本脚本用 9/9 报告的原始方法（21_timing.py：入场价取 1h bar 开盘价、pos24 取近 24 根）
在【两个样本】上分别算【两种指标】，定位矛盾来源。
"""
import json

import numpy as np
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


# ── 样本 1：strategy_trades（9/9 报告用的样本）──
with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    st_rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, exit_price, pnl, opened_at, closed_at, strategy_id, decision_context
        from strategy_trades order by id desc limit 3000
    """)).fetchall()]
st = []
for r in st_rows:
    if str(r["strategy_id"] or "").startswith("e2e_"):
        continue
    d = parse(r["decision_context"])
    if d.get("nature") not in ("swing", "trend_follow", "position"):
        continue
    if not r["opened_at"] or not r["entry_price"]:
        continue
    st.append(r)

# ── 样本 2：paper_positions（paper 账户 14，mid/long）──
with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    pp_rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, close_price as exit_price,
               (unrealized_pnl+partial_realized_pnl) as pnl, opened_at, closed_at
        from paper_positions
        where account_id=14 and timeframe_tier in ('mid','long')
          and opened_at >= '2026-08-01'
    """)).fetchall()]

# ── 1h K 线（9/9 同口径：优先 asterdex，缺失回退 binance）──
syms = sorted({r["symbol"] for r in st} | {r["symbol"] for r in pp_rows})
kl = {}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for exch in ("asterdex", "binance"):
        for s, ts, o, h, l, cl in c.execute(text("""
            select symbol, timestamp, open_price, high_price, low_price, close_price
            from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
            order by symbol, timestamp
        """), {"ex": exch, "syms": syms}).fetchall():
            kl.setdefault((exch, s), []).append((int(ts), float(o), float(h), float(l), float(cl)))


def pick(sym):
    for ex in ("asterdex", "binance"):
        v = kl.get((ex, sym))
        if v and len(v) > 500:
            return v
    return None


def idx_at(series, ts):
    for i, row in enumerate(series):
        if row[0] >= ts:
            return i
    return None


def analyze(rows, name):
    recs = []
    for t in rows:
        ser = pick(t["symbol"])
        if not ser:
            continue
        i = idx_at(ser, int(t["opened_at"].timestamp()))
        if i is None or i < 24:
            continue
        entry = ser[i][1]                      # 9/9 口径：bar 开盘价
        win = ser[max(0, i - 23):i + 1]
        hi = max(r[2] for r in win)
        lo = min(r[3] for r in win)
        pos = (entry - lo) / (hi - lo) * 100 if hi > lo else 50.0
        sgn = 1 if t["side"] == "long" else -1
        r24 = (ser[i + 24][4] - entry) / entry * 100 * sgn if i + 24 < len(ser) else None
        r72 = (ser[i + 72][4] - entry) / entry * 100 * sgn if i + 72 < len(ser) else None
        recs.append({"pos": pos, "r24": r24, "r72": r72,
                     "pnl": float(t["pnl"] or 0), "side": t["side"]})
    print(f"\n===== {name}（可用样本 {len(recs)}）=====")
    print(f"{'分位桶':>10} {'n':>4} {'r24均值':>9} {'r24胜率':>8} {'r72均值':>9} {'已实现PnL均值':>13} {'PnL合计':>10}")
    for b in range(5):
        sub = [x for x in recs if b * 20 <= x["pos"] < b * 20 + 20]
        if not sub:
            continue
        r24s = [x["r24"] for x in sub if x["r24"] is not None]
        r72s = [x["r72"] for x in sub if x["r72"] is not None]
        r24m = np.mean(r24s) if r24s else float("nan")
        wr = (sum(1 for v in r24s if v > 0) / len(r24s) * 100) if r24s else 0
        r72m = np.mean(r72s) if r72s else float("nan")
        print(f"{b*20:>4}-{b*20+20:>3}% {len(sub):>4} {r24m:>+8.3f}% {wr:>7.1f}% {r72m:>+8.3f}% "
              f"{np.mean([x['pnl'] for x in sub]):>+13.2f} {sum(x['pnl'] for x in sub):>+10.2f}")
    return recs


st_recs = analyze(st, "样本1 strategy_trades（9/9 报告样本）")
pp_recs = analyze(pp_rows, "样本2 paper_positions 账户14（8/1 起）")

# ── 位置闸口径的净效果（仅 midlong 位置闸适用档：ranging/unknown 无法从历史取，
#    这里给"全体"口径，便于与 V8 对照）──
print("\n===== 汇总：闸口径下的「应拒/放行」两半 =====")
for nm, recs in (("strategy_trades", st_recs), ("paper_positions", pp_recs)):
    hi = [x for x in recs if x["pos"] >= 60]
    lo = [x for x in recs if x["pos"] < 60]
    for lab, sub in (("分位≥60%(闸应拒)", hi), ("分位<60%(闸放行)", lo)):
        if not sub:
            continue
        r24s = [x["r24"] for x in sub if x["r24"] is not None]
        print(f"  {nm:<18} {lab:<18} n={len(sub):>3} r24均={np.mean(r24s) if r24s else float('nan'):>+7.3f}% "
              f"PnL合计={sum(x['pnl'] for x in sub):>+9.2f} PnL均={np.mean([x['pnl'] for x in sub]):>+7.2f}")
