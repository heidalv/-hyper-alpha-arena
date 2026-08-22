# -*- coding: utf-8 -*-
"""盈利改善分析②：出场回吐量化。

对每笔已平仓（含峰值记录）：若采用「peak − 2×ATR(≈2%) 追踪离场」替代
「保本收割/固定 TP」，多赚多少？ 这说明底层出场的改进空间。
"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession

set_system_identity()
s = ScopedSession()
try:
    rows = s.execute(text("""
        SELECT symbol, side, trade_nature,
               entry_price, close_price, unrealized_pnl,
               peak_unrealized_pnl, peak_pnl_pct, original_size,
               status, close_reason
        FROM paper_positions
        WHERE status = 'closed' AND tenant_id IN (326,327)
          AND peak_unrealized_pnl IS NOT NULL AND peak_unrealized_pnl > 0
    """)).fetchall()
    print(f"有峰值记录的已平仓单: {len(rows)}")
    buckets = {"scalp": [0, 0.0, 0.0], "swing": [0, 0.0, 0.0], "trend_follow": [0, 0.0, 0.0]}
    total_gain = 0.0
    noted = 0
    for r in rows:
        nature = (r.trade_nature or "?").lower()
        if nature not in buckets:
            continue
        peak = float(r.peak_unrealized_pnl or 0)
        total_pnl = float(r.unrealized_pnl or 0)
        # 近似：峰值 PnL 百分比（峰值浮盈 / 名义）
        notional = float(r.entry_price or 0) * float(r.original_size or r.size or 0)
        if notional <= 0:
            continue
        peak_pct = peak / notional
        final_pct = total_pnl / notional if total_pnl else 0.0
        # 规则：peak ≥ 2.0% 后，回撤到 peak − 2.0%（价格百分点）即离场
        if peak_pct >= 0.02:
            improved_pct = min(peak_pct, 0.02 + (peak_pct - 0.02) * 0.5)  # 保守：只拿回一半回吐量
            cand_gain = (improved_pct - final_pct) * notional
            if cand_gain > 0:
                buckets[nature][0] += 1
                buckets[nature][1] += cand_gain
                buckets[nature][2] += notional
                total_gain += cand_gain
                if nature == "scalp" and noted < 8:
                    print(f"  例: {r.symbol} {nature} peak={peak_pct*100:.1f}% 实际={final_pct*100:+.1f}% "
                          f"潜在改进≈{cand_gain:+.2f}U (close={r.close_reason})")
                    noted += 1
    print("\n=== 出场改进（peak≥2% 后回撤 2% 追踪离场 vs 实际） ===")
    for k, (n, gain, notional_sum) in buckets.items():
        base = gain / notional_sum * 100 if notional_sum else 0
        print(f"  {k:<12} 受益单 {n:>4} 笔 | 合计潜在增益 {gain:+9.2f}U | 占名义 {base:+.3f}%")
    print(f"\n  总潜在增益 ≈ {total_gain:+.2f} USDT（历史单口径，非未来保证）")

    # 现状结算：每次平仓平均拿回多少峰值
    rows2 = s.execute(text("""
        SELECT trade_nature,
               count(*) AS n,
               round(avg(CASE WHEN peak_unrealized_pnl > 0
                              THEN (unrealized_pnl / nullif(peak_unrealized_pnl,0)) END)::numeric,3) AS retention
        FROM paper_positions
        WHERE status='closed' AND tenant_id IN (326,327) AND peak_unrealized_pnl > 0
        GROUP BY trade_nature
    """)).fetchall()
    print("\n=== 峰值保留率（最终盈亏 ÷ 峰值浮盈；<1 即回吐） ===")
    for r in rows2:
        print(f"  {(r.trade_nature or '?'):<12} n={r.n:>4} 保留率={float(r.retention or 0):.1%}")
finally:
    s.close()
