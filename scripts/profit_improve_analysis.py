# -*- coding: utf-8 -*-
"""盈利改善分析①：按策略/性质/出场通道计算真实预期（干净数据口径）。"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession

NAT = "coalesce(decision_context ->> 'nature', '?')"

set_system_identity()
s = ScopedSession()
try:
    print("=== 1. 按交易性质：笔数/胜率/均笔/合计/平均持有 ===")
    rows = s.execute(text(f"""
        SELECT {NAT} AS nature,
               count(*) AS n,
               count(*) FILTER (WHERE pnl > 0) AS wins,
               round(avg(pnl)::numeric, 4) AS avg_pnl,
               round(sum(pnl)::numeric, 2) AS sum_pnl,
               round(avg(coalesce(holding_period,0))::numeric, 1) AS avg_hold_s
        FROM strategy_trades
        WHERE pnl IS NOT NULL
        GROUP BY 1 ORDER BY sum_pnl DESC
    """)).fetchall()
    for r in rows:
        wr = r.wins / r.n if r.n else 0
        print(f"  {(r.nature or '?'):<12} n={r.n:>4} 胜率={wr*100:5.1f}% 均笔={float(r.avg_pnl):+.4f} 合计={float(r.sum_pnl):+8.2f} 均持={float(r.avg_hold_s):8.0f}s")

    print("\n=== 2. 中长线策略池（n>=10） ===")
    rows = s.execute(text(f"""
        SELECT strategy_id, {NAT} AS nature,
               count(*) AS n, round(avg(pnl)::numeric, 4) AS avg_pnl,
               round(sum(pnl)::numeric, 2) AS sum_pnl, count(*) FILTER (WHERE pnl>0) AS wins
        FROM strategy_trades
        WHERE pnl IS NOT NULL AND ({NAT} IN ('trend_follow','swing','position'))
        GROUP BY 1, 2 HAVING count(*) >= 10
        ORDER BY sum_pnl DESC LIMIT 25
    """)).fetchall()
    for r in rows:
        wr = r.wins / r.n
        print(f"  {r.strategy_id[:44]:<44} {(r.nature or ''):<12} n={r.n:>3} 胜率={wr*100:5.1f}% 均笔={float(r.avg_pnl):+.4f} 合计={float(r.sum_pnl):+8.2f}")

    print("\n=== 3. scalp 前缀簇 ===")
    rows = s.execute(text("""
        SELECT CASE WHEN strategy_id LIKE 'scalp_mr_%' THEN 'MR' WHEN strategy_id LIKE 'scalp_lane_%' THEN 'LANE' ELSE 'other' END AS cls,
               count(*) AS n, round(avg(pnl)::numeric,4) AS avg_pnl,
               round(sum(pnl)::numeric,2) AS sum_pnl, count(*) FILTER (WHERE pnl>0) AS wins
        FROM strategy_trades WHERE pnl IS NOT NULL AND strategy_id LIKE 'scalp_%'
        GROUP BY 1 ORDER BY sum_pnl DESC
    """)).fetchall()
    for r in rows:
        wr = r.wins / r.n if r.n else 0
        print(f"  {r.cls:<6} n={r.n:>4} 胜率={wr*100:5.1f}% 均笔={float(r.avg_pnl):+.4f} 合计={float(r.sum_pnl):+8.2f}")

    print("\n=== 4. 出场通道效率（TP/BE vs 回撤通道 vs 超时） ===")
    rows = s.execute(text("""
        SELECT trade_nature,
               count(*) FILTER (WHERE close_reason IN ('tp','breakeven_tp')) AS tp_be_n,
               round(sum(pnl) FILTER (WHERE close_reason IN ('tp','breakeven_tp'))::numeric,2) AS tp_be_sum,
               count(*) FILTER (WHERE close_reason IN ('profit_drawdown_full','profit_drawdown_partial')) AS dd_n,
               round(sum(pnl) FILTER (WHERE close_reason IN ('profit_drawdown_full','profit_drawdown_partial'))::numeric,2) AS dd_sum,
               count(*) FILTER (WHERE close_reason IN ('max_hold_timeout','hold_timeout_review')) AS timeout_n,
               round(sum(pnl) FILTER (WHERE close_reason IN ('max_hold_timeout','hold_timeout_review'))::numeric,2) AS timeout_sum
        FROM paper_orders
        WHERE tenant_id IN (326,327) AND pnl IS NOT NULL AND trade_nature IS NOT NULL
        GROUP BY trade_nature ORDER BY 1
    """)).fetchall()
    for r in rows:
        def _f(v):
            return float(v) if v is not None else 0.0
        print(f"  {(r.trade_nature or '?'):<12} TP/BE: n={r.tp_be_n or 0:>4} 合计={_f(r.tp_be_sum):+8.2f} | "
              f"回撤通道: n={r.dd_n or 0:>3} 合计={_f(r.dd_sum):+7.2f} | "
              f"超时: n={r.timeout_n or 0:>4} 合计={_f(r.timeout_sum):+7.2f}")

    print("\n=== 5. 手续费成本（按 nature） ===")
    rows = s.execute(text("""
        SELECT trade_nature, round(sum(fee)::numeric,2) AS fees, round(sum(pnl)::numeric,2) AS gross
        FROM paper_orders WHERE tenant_id IN (326,327) AND pnl IS NOT NULL
        GROUP BY trade_nature ORDER BY 1
    """)).fetchall()
    for r in rows:
        g = float(r.gross or 0); f = float(r.fees or 0)
        print(f"  {(r.trade_nature or '?'):<12} 费={f:+8.2f} 毛利={g:+8.2f} 净={g-f:+8.2f}")
finally:
    s.close()
