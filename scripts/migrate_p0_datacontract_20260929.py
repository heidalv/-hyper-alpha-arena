# -*- coding: utf-8 -*-
"""P0 数据契约迁移 2026-09-29（幂等，可重复执行）。

1. paper_positions 增加 exit_module / peak_at / trough_at 列；
2. strategy_trades 增加 paper_position_id 列 + 回填（symbol+side+opened_at ±120s 匹配）；
3. 历史 close_reason → exit_module 回填；
4. 建索引。
"""
import sys

import psycopg2

PG = dict(host="localhost", user="laobao", password="alpha_pass", dbname="alpha_arena")


def main():
    conn = psycopg2.connect(**PG)
    conn.set_session(autocommit=True)
    cur = conn.cursor()
    cur.execute("SET app.is_admin='on'")

    cur.execute("ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS exit_module VARCHAR(32)")
    cur.execute("ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS peak_at TIMESTAMP WITHOUT TIME ZONE")
    cur.execute("ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS trough_at TIMESTAMP WITHOUT TIME ZONE")
    cur.execute("ALTER TABLE strategy_trades ADD COLUMN IF NOT EXISTS paper_position_id INTEGER")
    print("columns ensured")

    # 回填 strategy_trades.paper_position_id
    # [2026-09-29 修正2] 学习行 st.opened_at 来源复杂（决策时刻/限价成交差异），
    # 而 st.closed_at = 平仓处理时刻 ≈ p.closed_at − 8h（p 为 naive 北京钟面）。
    # 实测 30d 内 closed_at 匹配 460/560、opened_at 匹配仅 7/560 → 以 closed_at 为主键，
    # opened_at(+8h) 作兜底；且限定 p.account_id=14 防跨账户误挂。
    cur.execute("""
        UPDATE strategy_trades st SET paper_position_id = sub.pid
        FROM (
            SELECT DISTINCT ON (st2.id)
                st2.id AS sid, p.id AS pid,
                CASE WHEN st2.closed_at IS NOT NULL AND p.closed_at IS NOT NULL
                     THEN abs(extract(epoch from (st2.closed_at - (p.closed_at - interval '8 hours'))))
                     ELSE abs(extract(epoch from (st2.opened_at - (p.opened_at + interval '8 hours'))))
                END AS d
            FROM strategy_trades st2
            JOIN paper_positions p
              ON p.symbol = st2.symbol AND p.side = st2.side AND p.account_id = 14
             AND (
               (st2.closed_at IS NOT NULL AND p.closed_at IS NOT NULL
                AND abs(extract(epoch from (st2.closed_at - (p.closed_at - interval '8 hours')))) < 21600)
               OR abs(extract(epoch from (st2.opened_at - (p.opened_at + interval '8 hours')))) < 300
             )
            WHERE st2.paper_position_id IS NULL
            ORDER BY st2.id, d ASC
        ) sub
        WHERE st.id = sub.sid
    """)
    n = cur.rowcount
    print(f"strategy_trades.paper_position_id backfilled: {n}")

    # 历史 exit_module 回填（closed 行，按 close_reason 前缀）
    cur.execute("""
        UPDATE paper_positions SET exit_module = CASE
            WHEN close_reason LIKE 'barrier:%' THEN substring(close_reason from 9 for 24)
            WHEN close_reason IN ('sl','breakeven_sl','breakeven_tp') THEN 'hard_sl'
            WHEN close_reason LIKE 'exit_policy:%' THEN substring(close_reason from 14 for 24)
            WHEN close_reason LIKE 'staged_tp%' OR close_reason LIKE 'tp_safety_net%' THEN 'staged_tp'
            WHEN close_reason LIKE 'thesis%' OR close_reason LIKE 'trend_broken%' OR close_reason LIKE 'trend_weaken%' THEN 'thesis_rule'
            WHEN close_reason LIKE 'profit_drawdown%' THEN 'profit_drawdown'
            WHEN close_reason LIKE 'max_hold%' OR close_reason LIKE 'hold_timeout%' OR close_reason LIKE '%no_progress%' THEN 'time_stop'
            WHEN close_reason LIKE 'manual%' THEN 'manual'
            ELSE left(coalesce(close_reason,''), 24)
        END
        WHERE status='closed' AND exit_module IS NULL AND close_reason IS NOT NULL
    """)
    print(f"exit_module backfilled: {cur.rowcount}")

    cur.execute("CREATE INDEX IF NOT EXISTS ix_strategy_trades_paper_position ON strategy_trades(paper_position_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_paper_positions_exit_module ON paper_positions(exit_module)")
    print("indexes ensured")
    conn.close()
    print("P0 migration done")


if __name__ == "__main__":
    main()
