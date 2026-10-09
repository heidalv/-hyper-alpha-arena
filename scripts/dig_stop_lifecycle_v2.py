# -*- coding: utf-8 -*-
"""深挖①v2：库存回放重建止损仓（回溯到库存归零点 = 仓位起点）。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

cur.execute("""
    SELECT ts, symbol, meta_json->>'side', (meta_json->>'qty')::float8,
           (meta_json->>'fill_px')::float8, meta_json->>'exit_path'
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '14 hours'
    ORDER BY ts
""")
rows = cur.fetchall()

# 按币序回放，记录每个时点的累计库存
stops = []
hist = {}
for ts, sym, side, qty, px, ep in rows:
    h = hist.setdefault(sym, [])
    h.append((ts, side, qty or 0.0, px or 0.0, ep))
    if ep == "stop_loss_taker":
        stops.append((ts, sym))

print(f"14h 内止损 {len(stops)} 笔\n")
print("== 回溯重建（币 / 仓位历时 / 入场腿数 / 加仓轨迹 / 峰名义 / 首腿→止损价漂移）==")
for ts0, sym in stops:
    h = hist[sym]
    i = len(h) - 1
    # 找到该止损腿在 h 里的位置（最近的 stop 腿）
    while i >= 0 and h[i][0] != ts0:
        i -= 1
    inv = 0.0
    entries = []
    j = i
    while j >= 0:
        ts, side, qty, px, ep = h[j]
        d = qty if side == "buy" else -qty
        if j == i:
            inv += d          # 止损腿本身（平仓方向）
        else:
            inv += d
        if ep == "stop_loss_taker" and j != i:
            break             # 上一个止损/全平点 = 仓位边界
        if abs(inv) < 1e-9:
            j -= 1
            break
        if j != i and (side == "sell" if inv > 0 else side == "buy"):
            entries.append((ts, px, qty))   # 加仓腿（与净方向同侧）
        j -= 1
    # 重新算净方向与入场腿
    stop_qty = h[i][2]
    stop_px = h[i][3]
    stop_side = h[i][1]
    pos_side = "buy" if stop_side == "sell" else "sell"   # 止损平仓的反侧 = 持仓方向
    entry_legs = []
    k = i - 1
    inv = 0.0
    while k >= 0 and abs(inv) < stop_qty - 1e-9:
        ts, side, qty, px, ep = h[k]
        d = qty if side == "buy" else -qty
        if ep == "stop_loss_taker":
            break
        inv += d if side == pos_side else -d
        if side == pos_side:
            entry_legs.append((ts, px, qty))
        k -= 1
    if not entry_legs:
        print(f"  {sym:<6} 重建失败（skip）")
        continue
    first_ts = entry_legs[-1][0]
    dur = (ts0 - first_ts).total_seconds() / 60.0
    tot_qty = sum(e[2] for e in entry_legs)
    avg_px = sum(e[1] * e[2] for e in entry_legs) / tot_qty
    drift = (stop_px - avg_px) / avg_px * 1e4 * (1 if pos_side == "buy" else -1)
    peak_notional = max(e[1] * e[2] for e in entry_legs)
    add_times = [f"{(e[0]-first_ts).total_seconds()/60:.0f}m" for e in entry_legs]
    print(f"  {sym:<6} {pos_side:<4} 历时={dur:>5.1f}min 入场腿={len(entry_legs):>2} "
          f"总qty={tot_qty:>8.3g} 均入={avg_px:>10.5g} 止={stop_px:>10.5g} "
          f"漂移={drift:>+7.1f}bp 加仓时点={add_times}")
