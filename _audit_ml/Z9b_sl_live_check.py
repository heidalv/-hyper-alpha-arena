# -*- coding: utf-8 -*-
"""Z9b：验证 sl_price 是否是活体字段（未来函数）。

若 mid 层开仓 SL 恒为某个固定比例，则 R% 分布应在该值处出现尖峰，
而「R% 很小」的样本只出现在盈利笔里 —— 那就是追踪上移留下的痕迹。
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
eng = create_engine(URL)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [dict(r._mapping) for r in c.execute(text("""
        select id, symbol, timeframe_tier, entry_price, sl_price, close_price, size,
               original_size, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
               peak_pnl_pct, close_reason, opened_at
        from paper_positions
        where timeframe_tier in ('mid','long') and status='closed'
          and closed_at >= now() - interval '75 days'
        order by opened_at
    """)).fetchall()]

print(f"n={len(rows)}")
for tier in ("mid", "long"):
    sub = [r for r in rows if r["timeframe_tier"] == tier]
    if not sub:
        continue
    print(f"\n===== {tier} n={len(sub)} =====")
    hist = Counter()
    for r in sub:
        e = float(r["entry_price"] or 0)
        sl = float(r["sl_price"] or 0)
        if e <= 0 or sl <= 0:
            hist["无SL"] += 1
            continue
        rp = abs(e - sl) / e * 100
        hist[round(rp * 2) / 2] += 1  # 0.5% 分箱
    print("  R% 分布（0.5% 分箱）：")
    for k in sorted(hist, key=lambda x: (isinstance(x, str), x)):
        print(f"    {str(k):>5}%: {hist[k]:>4}  {'#' * min(hist[k], 60)}")
    # SL 相对入场的位置：是否被推到盈利区
    above = sum(1 for r in sub if float(r["sl_price"] or 0) > 0
                and ((float(r["sl_price"]) > float(r["entry_price"]))
                     if r["timeframe_tier"] == "mid" else False))
    print(f"  SL 高于入场价（被上移）笔数（mid 多头口径）={above}")
    # 与结局的关系
    def usd(r):
        return (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
                - float(r["partial_fee_paid"] or 0))
    for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 8), (8, 99)):
        g = [r for r in sub if float(r["sl_price"] or 0) > 0
             and lo <= abs(float(r["entry_price"]) - float(r["sl_price"]))
             / float(r["entry_price"]) * 100 < hi]
        if not g:
            continue
        w = sum(1 for r in g if usd(r) > 0)
        print(f"  R%∈[{lo},{hi}) n={len(g):>3} 胜率={w/len(g):.3f} "
              f"总USD=${sum(usd(r) for r in g):+.2f}")
