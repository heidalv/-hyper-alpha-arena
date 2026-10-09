# -*- coding: utf-8 -*-
r"""影子观察监控（全面执行后 30 天观察期用）2026-09-29。

一条命令收集目标（§107.4）的全部证据：
  T1 mid 开/平仓计数与 barrier 活动（自 13:00 执行起）
  T2 空头样本计数（自 09-28 解禁起）
  T3 学习覆盖率（strategy_trades.paper_position_id 30d 覆盖）
  T4 滑点分布（近 7d entry/exit p50/p90，目标 p50 ≤10bp/边）
  T5 边际闸运行时判定（z_btc / funding_z / 对 BTC 的多空判定）
  T6 日志证据（midlong_edge_* 拦截计数、[Paper][Barrier] 行、cycle_conflict 计数）

用法：.venv\Scripts\python.exe scripts\shadow_observe_20260929.py
"""
import collections
import glob
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

PG = dict(host="localhost", user="laobao", password="alpha_pass", dbname="alpha_arena")


def q(sql, params=()):
    conn = psycopg2.connect(**PG)
    conn.set_session(autocommit=True)
    cur = conn.cursor()
    cur.execute("SET app.is_admin='on'")
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def main():
    print("=" * 70)
    print("影子观察快照", time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 70)

    # T1 mid 开/平仓 + 当前持仓
    r = q("""
        SELECT
          (SELECT count(*) FROM paper_positions WHERE account_id=14 AND timeframe_tier='mid' AND opened_at >= '2026-09-29 13:00'),
          (SELECT count(*) FROM paper_positions WHERE account_id=14 AND timeframe_tier='mid' AND status='closed' AND closed_at >= '2026-09-29 13:00'),
          (SELECT count(*) FROM paper_positions WHERE account_id=14 AND status='open'),
          (SELECT coalesce(round(sum(unrealized_pnl)::numeric,2),0) FROM paper_positions WHERE account_id=14 AND status='open')
    """)
    mid_open, mid_close, open_n, open_upnl = r[0]
    print(f"[T1] mid 新开 {mid_open} / 新平 {mid_close}（自 13:00）；当前在仓 {open_n}（浮盈 {open_upnl}）")

    # T2 空头样本
    r = q("""
        SELECT count(*) FROM paper_positions
        WHERE account_id=14 AND timeframe_tier='mid' AND side='short' AND opened_at >= '2026-09-28 00:00'
    """)
    print(f"[T2] mid 空头新开 {r[0][0]} 笔（自 09-28 解禁；目标 ≥60）")

    # T3 学习覆盖率（30d 平仓 vs 挂接）
    r = q("""
        WITH c AS (
          SELECT count(*) n FROM paper_positions
          WHERE account_id=14 AND status='closed' AND closed_at >= now() - interval '30 days')
        SELECT c.n,
          (SELECT count(DISTINCT st.paper_position_id) FROM strategy_trades st
           JOIN paper_positions p ON p.id = st.paper_position_id
           WHERE p.account_id=14 AND p.status='closed' AND p.closed_at >= now() - interval '30 days')
        FROM c
    """)
    n_closed, n_linked = r[0]
    pct = 100.0 * n_linked / n_closed if n_closed else 0.0
    print(f"[T3] 学习覆盖：30d 平仓 {n_closed} 笔，已挂接 paper_position_id {n_linked}（{pct:.0f}%，目标 ≥90%）")

    # T4 滑点分布（近 7d，按车道：mid 限价入场滑点应≈0/NULL，long 市价入场按模型计）
    r = q("""
        SELECT timeframe_tier,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY entry_slippage_bp),
          percentile_cont(0.9) WITHIN GROUP (ORDER BY entry_slippage_bp),
          count(*) FILTER (WHERE entry_slippage_bp IS NOT NULL),
          count(*)
        FROM paper_positions
        WHERE account_id=14 AND opened_at >= now() - interval '7 days'
        GROUP BY 1 ORDER BY 1
    """)
    for tier, e50, e90, ne, nt in r:
        print(f"[T4] 滑点({tier or '?'}, 近7d): 入场 p50={e50 if e50 is not None else '—'}bp p90={e90 if e90 is not None else '—'}bp "
              f"(有值 {ne}/{nt}；mid 目标 p50 ≤10bp/边，NULL=限价成交无模型滑点)")

    # T5 边际闸实时判定
    from backend.services.full_auto import edge_gate as eg
    now = time.time()
    zb = eg.z_btc_mom(now)
    print(f"[T5] z_btc = {zb:.2f}" if zb is not None else "[T5] z_btc = None(数据不足)")
    for sym in ("BTC", "ETH", "SOL", "UNI", "XRP", "BNB"):
        zf = eg.funding_z(sym, now)
        if zf is not None:
            print(f"      funding_z({sym}) = {zf:+.2f}")
    for side in ("long", "short"):
        ok, reason = eg.check_edge_entry(14, "BTC", side, "mid", entry_price=60000.0)
        print(f"      BTC {side:5s} → {'放行' if ok else '拦截'} | {reason}")

    # T6 日志证据
    logs = sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "backend.pid*.log")),
                  key=os.path.getmtime)
    if not logs:
        print("[T6] 无日志")
        return
    latest = logs[-1]
    print(f"[T6] 日志 {os.path.basename(latest)}（自 13:00）")
    edge_cnt = collections.Counter()
    barrier_cnt = 0
    cycle_cnt = 0
    with open(latest, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "2026-09-29 13:" not in line and "2026-09-29 14:" not in line and "2026-09-29 15:" not in line:
                continue
            m = re.search(r"reason=(midlong_edge_\w+)", line)
            if m:
                edge_cnt[m.group(1)] += 1
            if "[Paper][Barrier]" in line:
                barrier_cnt += 1
            if "cycle_conflict" in line:
                cycle_cnt += 1
    print(f"      midlong_edge_* 拦截：{dict(edge_cnt) if edge_cnt else '（无）'}")
    print(f"      [Paper][Barrier] 行数：{barrier_cnt}")
    print(f"      cycle_conflict 拦截：{cycle_cnt}")
    print("=" * 70)


if __name__ == "__main__":
    main()
