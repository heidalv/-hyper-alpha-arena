# -*- coding: utf-8 -*-
"""Debug: 定位反事实回放 pnl 爆炸的根因。"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import counterfactual_exit_replay_20260929 as M

pos = M.load_positions()
pos["notional"] = pos["size"] * pos["entry_price"]
print("=== notional describe ===")
print(pos["notional"].describe())
print("=== size describe ===")
print(pos["size"].describe())
print("=== top notional rows ===")
print(pos.nlargest(8, "notional")[["symbol", "side", "entry_price", "size", "leverage", "margin", "notional", "closed_at"]].to_string())
print("=== bottom notional rows ===")
print(pos.nsmallest(8, "notional")[["symbol", "side", "entry_price", "size", "leverage", "margin", "notional"]].to_string())

# kline 覆盖
t_min = pos.opened_ts.min() - 14 * 86400
t_max = pos.closed_ts.max() + 86400
for sym in sorted(set(pos.symbol)):
    ex = pos[pos.symbol == sym].exchange.iloc[0] or ""
    got = None
    for cand in ([ex] if ex else []) + M.EXCH_PREF:
        df = M.load_klines(sym, cand, t_min, t_max)
        if df is not None and len(df) > 500:
            got = (cand, df)
            break
    if got is None:
        print(sym, "NO KLINES")
        continue
    name, bars = got
    r0 = pos[pos.symbol == sym].iloc[0]
    ratio = M.price_sane(r0, bars)
    print(f"{sym:8s} ex={name:12s} bars={len(bars):6d} first_price={bars.c.iloc[0]:.6g} paper_entry={r0.entry_price:.6g} ratio={ratio}")

# 单组合明细
_, barsA = None, None
out = []
for _, r in pos.iterrows():
    if r.symbol == "ASTER":
        pass
for _, r in pos.iterrows():
    sym = r.symbol
    ex = pos[pos.symbol == sym].exchange.iloc[0] or ""
    df = M.load_klines(sym, ex if ex else M.EXCH_PREF[0], t_min, t_max)
    if df is None or len(df) <= 500:
        continue
    pnl, fee, kind, fills = M.replay_one(r, df, 1.5, 2.0, 24, True, True)
    if pnl is None:
        continue
    out.append((r.symbol, r.id, r.side, r.entry_price, r.size, round(r.size * r.entry_price, 1), pnl, fee, kind, fills))
out.sort(key=lambda x: -abs(x[6]))
print("=== top |pnl| replayed (k=1.5 r2=2 t=24) ===")
for row in out[:12]:
    print(f"sym={row[0]:8s} id={row[1]} side={row[2]:5s} entry={row[3]:.6g} size={row[4]:.6g} notional={row[5]:.1f} pnl={row[6]:.1f} fee={row[7]:.1f} kind={row[8]} fills={row[9]}")
