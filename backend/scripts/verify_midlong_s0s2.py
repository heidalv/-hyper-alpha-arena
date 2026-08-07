#!/usr/bin/env python
"""
S3 验证脚本：系统重启后 14 天跑此脚本，验证 S0-S2 改动的实际效果。

运行方式：
  cd backend && .venv/Scripts/python.exe scripts/verify_midlong_s0s2.py

验收标准（对应 04 综合方案 §3.2-§3.4）：
  - master_running_close 在 mid/long 占比 ≤ 5%（S0-6）
  - 亏损后 24h 同向再开率 ≤ 20%（S0-1/S0-8）
  - ai_decision_logs 14 天记录数 ≥ 100（S1-12）
  - open 仓位含 exit_state_json(tp_stages_override)比例 ≥ 80%（S2-5）
  - trailing_stop_price > 0 比例 ≥ 30%（S2-6）
  - peak_pnl_pct > 0 比例 ≥ 90%（S2-6）
  - tp_level_reached > 0 比例 ≥ 40%（S2-5/S2-6）
  - prompt_archives 有落盘文件（S1-7/8）
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

CONN = 'postgresql://laobao:alpha_pass@localhost:5432/alpha_arena'

def main():
    conn = psycopg.connect(CONN)
    cur = conn.cursor()

    print("=" * 80)
    print("S0-S2 改动效果验证（系统重启后 14 天数据）")
    print("=" * 80)

    # ── S0 验收 ──
    print("\n【S0 止血验收】")

    # S0-6: master_running_close 在 mid/long 占比 ≤ 5%
    cur.execute("""
    SELECT
      COUNT(*) AS n_total,
      COUNT(*) FILTER (WHERE close_reason = 'master_running_close') AS n_master,
      ROUND(100.0 * COUNT(*) FILTER (WHERE close_reason = 'master_running_close') / NULLIF(COUNT(*), 0), 1) AS pct
    FROM paper_positions
    WHERE closed_at > NOW() - INTERVAL '14 days'
      AND (trade_nature IN ('swing','trend_follow','position') OR timeframe_tier IN ('mid','long'))
      AND status IN ('closed','liquidated')
    """)
    r = cur.fetchone()
    print(f"  master_running_close 占比: {r[2]}% ({r[1]}/{r[0]})  目标: ≤5%  {'✅' if r[2] <= 5 else '❌'}")

    # S0-1/S0-8: 亏损后 24h 同向再开率 ≤ 20%
    cur.execute("""
    WITH closed_loss AS (
      SELECT account_id, symbol, side, closed_at
      FROM paper_positions
      WHERE closed_at > NOW() - INTERVAL '14 days'
        AND status IN ('closed','liquidated')
        AND (trade_nature IN ('swing','trend_follow','position') OR timeframe_tier IN ('mid','long'))
        AND unrealized_pnl < 0
    )
    SELECT
      COUNT(*) AS n_loss,
      COUNT(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM paper_positions p2
        WHERE p2.symbol = closed_loss.symbol AND p2.side = closed_loss.side
          AND p2.opened_at > closed_loss.closed_at
          AND p2.opened_at < closed_loss.closed_at + INTERVAL '24 hours'
          AND (p2.trade_nature IN ('swing','trend_follow','position') OR p2.timeframe_tier IN ('mid','long'))
      )) AS n_reopen
    FROM closed_loss
    """)
    r = cur.fetchone()
    reopen_pct = round(100.0 * r[1] / r[0], 1) if r[0] > 0 else 0
    print(f"  亏损后 24h 同向再开率: {reopen_pct}% ({r[1]}/{r[0]})  目标: ≤20%  {'✅' if reopen_pct <= 20 else '❌'}")

    # ── S1 验收 ──
    print("\n【S1 Prompt v3 验收】")

    # S1-12: ai_decision_logs 14 天记录数 ≥ 100
    cur.execute("SELECT COUNT(*) FROM ai_decision_logs WHERE created_at > NOW() - INTERVAL '14 days'")
    n_logs = cur.fetchone()[0]
    print(f"  ai_decision_logs 14 天: {n_logs} 条  目标: ≥100  {'✅' if n_logs >= 100 else '❌'}")

    # S1-7/8: prompt_archives 落盘
    archive_dir = "data/prompt_archives"
    n_archives = 0
    if os.path.exists(archive_dir):
        for _, _, files in os.walk(archive_dir):
            n_archives += len(files)
    print(f"  prompt_archives 落盘: {n_archives} 个  目标: ≥1  {'✅' if n_archives >= 1 else '❌'}")

    # ── S2 验收 ──
    print("\n【S2 退出统一验收】")

    # S2-5: open 仓位含 exit_state_json(tp_stages_override)比例
    cur.execute("""
    SELECT
      COUNT(*) AS n_total,
      COUNT(*) FILTER (WHERE exit_state_json IS NOT NULL AND exit_state_json::text LIKE '%tp_stages_override%') AS n_tp_stages,
      COUNT(*) FILTER (WHERE exit_state_json IS NOT NULL AND exit_state_json::text LIKE '%invalidation_condition%') AS n_invalidation,
      COUNT(*) FILTER (WHERE exit_state_json IS NOT NULL AND exit_state_json::text LIKE '%lifecycle_state%') AS n_lifecycle
    FROM paper_positions
    WHERE opened_at > NOW() - INTERVAL '14 days'
      AND (trade_nature IN ('swing','trend_follow','position') OR timeframe_tier IN ('mid','long'))
      AND status = 'open'
    """)
    r = cur.fetchone()
    tp_stages_pct = round(100.0 * r[1] / r[0], 1) if r[0] > 0 else 0
    print(f"  open 仓位含 tp_stages_override: {tp_stages_pct}% ({r[1]}/{r[0]})  目标: ≥80%  {'✅' if tp_stages_pct >= 80 else '❌'}")
    inv_pct = round(100.0 * r[2] / r[0], 1) if r[0] > 0 else 0
    print(f"  open 仓位含 invalidation_condition: {inv_pct}% ({r[2]}/{r[0]})  目标: ≥70%  {'✅' if inv_pct >= 70 else '❌'}")

    # S2-6: trailing/peak/tp_level 使用率（已平仓）
    cur.execute("""
    SELECT
      COUNT(*) AS n_total,
      COUNT(*) FILTER (WHERE trailing_stop_price > 0) AS n_trailing,
      COUNT(*) FILTER (WHERE peak_pnl_pct > 0) AS n_peak,
      COUNT(*) FILTER (WHERE tp_level_reached > 0) AS n_tp_level
    FROM paper_positions
    WHERE closed_at > NOW() - INTERVAL '14 days'
      AND (trade_nature IN ('swing','trend_follow','position') OR timeframe_tier IN ('mid','long'))
      AND status IN ('closed','liquidated')
    """)
    r = cur.fetchone()
    trail_pct = round(100.0 * r[1] / r[0], 1) if r[0] > 0 else 0
    peak_pct = round(100.0 * r[2] / r[0], 1) if r[0] > 0 else 0
    tp_lvl_pct = round(100.0 * r[3] / r[0], 1) if r[0] > 0 else 0
    print(f"  trailing_stop_price > 0: {trail_pct}% ({r[1]}/{r[0]})  目标: ≥30%  {'✅' if trail_pct >= 30 else '❌'}")
    print(f"  peak_pnl_pct > 0: {peak_pct}% ({r[2]}/{r[0]})  目标: ≥90%  {'✅' if peak_pct >= 90 else '❌'}")
    print(f"  tp_level_reached > 0: {tp_lvl_pct}% ({r[3]}/{r[0]})  目标: ≥40%  {'✅' if tp_lvl_pct >= 40 else '❌'}")

    # ── 整体效果 ──
    print("\n【整体效果】")
    cur.execute("""
    SELECT
      COUNT(*) AS n,
      COUNT(*) FILTER (WHERE unrealized_pnl > 0) AS n_win,
      ROUND((100.0 * COUNT(*) FILTER (WHERE unrealized_pnl > 0) / NULLIF(COUNT(*), 0))::numeric, 1) AS win_rate,
      ROUND(AVG(unrealized_pnl)::numeric, 2) AS avg_pnl,
      ROUND(SUM(unrealized_pnl)::numeric, 2) AS sum_pnl
    FROM paper_positions
    WHERE closed_at > NOW() - INTERVAL '14 days'
      AND (trade_nature IN ('swing','trend_follow','position') OR timeframe_tier IN ('mid','long'))
      AND status IN ('closed','liquidated')
    """)
    r = cur.fetchone()
    print(f"  胜率: {r[2]}% (目标: ≥35%)  {'✅' if r[2] and r[2] >= 35 else '❌'}")
    print(f"  avg_pnl: {r[3]}  sum_pnl: {r[4]}")

    # close_reason 含 lifecycle 类的比例
    cur.execute("""
    SELECT
      COUNT(*) AS n_total,
      COUNT(*) FILTER (
        WHERE close_reason LIKE '%tp_stage%' OR close_reason LIKE '%trailing%'
        OR close_reason LIKE '%breakeven%' OR close_reason LIKE '%invalidation%'
        OR close_reason LIKE '%nature_tp%' OR close_reason LIKE '%nature_trailing%'
      ) AS n_lifecycle
    FROM paper_positions
    WHERE closed_at > NOW() - INTERVAL '14 days'
      AND (trade_nature IN ('swing','trend_follow','position') OR timeframe_tier IN ('mid','long'))
      AND status IN ('closed','liquidated')
    """)
    r = cur.fetchone()
    lifecycle_pct = round(100.0 * r[1] / r[0], 1) if r[0] > 0 else 0
    print(f"  lifecycle 类 close_reason: {lifecycle_pct}% ({r[1]}/{r[0]})  目标: ≥60%  {'✅' if lifecycle_pct >= 60 else '❌'}")

    conn.close()
    print("\n" + "=" * 80)
    print("验证完成。")
    print("=" * 80)


if __name__ == "__main__":
    main()
