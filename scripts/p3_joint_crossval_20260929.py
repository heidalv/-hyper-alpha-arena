# -*- coding: utf-8 -*-
"""P3 交叉验证：高 m（funding_z + btc_mom）∩ 剔微盘币（price>=1）∩ 长仓-only 的回放。"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import numpy as np
import pandas as pd
import counterfactual_exit_replay_20260929 as R

pos = R.load_positions()
csv = pd.read_csv(r"D:\001Alpha\Hyper-Alpha-Arena\docs\p3_signal_prestudy_features_20260929.csv")
m = csv.set_index("id")["m"] if "m" in csv.columns else None
if m is None:
    # 重建 m = z(funding_z7d) + z(btc_1d_mom)
    zf = (csv.funding_z7d - csv.funding_z7d.mean()) / csv.funding_z7d.std()
    zb = (csv.btc_1d_mom - csv.btc_1d_mom.mean()) / csv.btc_1d_mom.std()
    m = (zf + zb) / np.sqrt(2)
    m.index = csv.id

m1, m2 = np.nanquantile(m, [1 / 3, 2 / 3])

bars_cache = {}
t_min = pos.opened_ts.min() - 14 * 86400
t_max = pos.closed_ts.max() + 86400
for sym in sorted(set(pos.symbol)):
    ex = pos[pos.symbol == sym].exchange.iloc[0] or ""
    for cand in ([ex] if ex else []) + R.EXCH_PREF:
        df = R.load_klines(sym, cand, t_min, t_max)
        if df is not None and len(df) > 500:
            bars_cache[sym] = df
            break

subsets = {
    "全样本": lambda r, mv: True,
    "高m": lambda r, mv: mv > m2,
    "高m∩price>=1": lambda r, mv: mv > m2 and r.entry_price >= 1.0,
    "高m∩price>=1∩long": lambda r, mv: mv > m2 and r.entry_price >= 1.0 and r.side == "long",
    "高m∩price>=1∩not_microcap_ls": lambda r, mv: mv > m2 and r.entry_price >= 1.0 and r.symbol not in ("ASTER", "XPL", "VIRTUAL"),
}
print(f"m2={m2:.2f}")
for name, fn in subsets.items():
    pnls, fees = [], []
    for _, r in pos.iterrows():
        mv = m.get(r.id, np.nan)
        if mv != mv:
            continue
        if not fn(r, mv):
            continue
        if r.symbol not in bars_cache:
            continue
        bars = bars_cache[r.symbol]
        if R.price_sane(r, bars) is None:
            continue
        pnl, fee, _, _ = R.replay_one(r, bars, 1.0, 2.0, 24, True, True)
        if pnl is None:
            continue
        pnls.append(pnl)
        fees.append(fee)
    pnls = np.array(pnls)
    w = (pnls > 0).sum()
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    pay = (wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else float("nan")
    print(f"{name:28s} n={len(pnls):3d} 净={pnls.sum():+8.2f} 每笔={pnls.mean():+6.2f} WR={100*w/max(len(pnls),1):4.1f}% 盈亏比={pay:.2f} 费={np.sum(fees):.1f}")
