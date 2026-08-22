# -*- coding: utf-8 -*-
"""归因：mid/long 层退出通道分布 + 持有时长 vs TP 到达率。"""
import sys
import numpy as np
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

from backend.database.connection import SessionLocal
from sqlalchemy import text

with SessionLocal() as db:
    rows = db.execute(text("""
        SELECT trade_nature, close_reason,
               EXTRACT(EPOCH FROM (closed_at - opened_at)) as hold_sec,
               partial_realized_pnl, size, entry_price,
               tp_price, sl_price
        FROM paper_positions
        WHERE status = 'closed' AND close_price IS NOT NULL
          AND trade_nature IN ('swing', 'trend_follow', 'scalp')
        ORDER BY closed_at DESC
    """)).fetchall()

from collections import defaultdict
buckets = defaultdict(lambda: dict(n=0, hold_sum=0.0, pnl_sum=0.0, reaches=0, total=0))
for r in rows:
    nature = r[0] or "?"
    reason = (r[1] or "?").lower()
    hold = float(r[2] or 0) / 3600
    pnl = float(r[3] or 0)
    size = float(r[5] or 0)
    ep = float(r[6] or 0)
    tp = r[7]
    # 归类
    if "sl" in reason or "stop" in reason:
        cat = "SL"
    elif "tp" in reason or "take_profit" in reason:
        cat = "TP/止盈"
    elif "timeout" in reason or "超时" in reason:
        cat = "TIMEOUT"
    elif "break" in reason or "breakeven" in reason:
        cat = "BE(保本)"
    elif "invalid" in reason or "review" in reason or "no_progress" in reason or "progress" in reason:
        cat = "复查/失效"
    elif "dust" in reason or "cleanup" in reason:
        cat = "清理"
    else:
        cat = "其他"
    b = buckets[(nature, cat)]
    b["n"] += 1
    b["hold_sum"] += hold
    b["pnl_sum"] += pnl
    b["total"] += 1

print(f"{'nature':<14}{'退出通道':<14}{'n':>5}{'均持仓h':>9}{'累计pnl':>10}{'占该层%':>8}")
for (nature, cat), b in sorted(buckets.items(), key=lambda kv: -kv[1]["pnl_sum"]):
    total_layer = sum(v["n"] for (n_, _), v in buckets.items() if n_ == nature)
    print(f"  {nature:<14}{cat:<14}{b['n']:>5}{b['hold_sum']/max(b['n'],1):>8.1f}{b['pnl_sum']:>+10.1f}{100*b['n']/max(total_layer,1):>7.1f}%")

# swing/trend 的 TP 距离 vs 实际到达：以 TP/SL 价格位计算潜在 RR
print("\n  swing/trend 建仓参数（TP/SL 距离、区间持仓时长、TP 到达率）:")
for nature in ("swing", "trend_follow"):
    sub = [r for r in rows if r[0] == nature]
    if not sub:
        continue
    hold_all = np.array([float(r[2] or 0) / 3600 for r in sub])
    pnl_all = np.array([float(r[3] or 0) for r in sub])
    tps = []
    sls = []
    for r in sub:
        ep = float(r[5] or 0)
        tp, sl = r[6], r[7]
        if ep > 0 and tp:
            tps.append((float(tp) / ep - 1) * 100)
        if ep > 0 and sl:
            sls.append((float(sl) / ep - 1) * 100)
    tps = np.array(tps); sls = np.array(sls)
    # 到达 TP 的：close_reason 含 tp
    tp_hit = sum(1 for r in sub if "tp" in (r[1] or "").lower() and "break" not in (r[1] or "").lower())
    sl_hit = sum(1 for r in sub if "sl" in (r[1] or "").lower() and "stop" not in (r[1] or "").lower())
    print(f"  {nature:<14} n={len(sub)} hold中位={np.median(hold_all):.1f}h 均值={hold_all.mean():.1f}h "
          f"累计pnl={pnl_all.sum():+.1f} 均TP={np.abs(tps).mean():.2f}% 均SL={np.abs(sls).mean():.2f}% "
          f"TP命中={tp_hit} SL命中={sl_hit} 其余={len(sub)-tp_hit-sl_hit}")
