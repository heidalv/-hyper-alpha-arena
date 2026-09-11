# -*- coding: utf-8 -*-
"""P3 震荡 MR 信号预测力深度解析（scalp_signal_log，只读）。
问题：MR 信号（区间位置+RSI 极值打分）对 30 分钟前向收益有没有预测力？
"""
import psycopg

CONN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"

def q(sql, params=None):
    with psycopg.connect(CONN, autocommit=True) as conn:
        conn.execute("set app.is_admin='on'")
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
    return cols, rows

cols, rows = q("""
    SELECT count(*) AS n,
           sum(CASE WHEN settled THEN 1 ELSE 0 END) AS settled_n,
           sum(CASE WHEN settled AND win THEN 1 ELSE 0 END) AS win_n
    FROM scalp_signal_log WHERE features_json LIKE '%%range_position%%'
""")
print("MR signals:", dict(zip(cols, rows[0])))

# ── direction × range_position 五分位 ──
cols, rows = q("""
    SELECT direction, width_bucket((features_json::jsonb->>'range_position')::float, 0, 1, 5) AS b,
           count(*) AS n,
           sum(CASE WHEN settled AND win THEN 1 ELSE 0 END) AS w_n,
           sum(CASE WHEN settled THEN 1 ELSE 0 END) AS s_n,
           round(avg(CASE WHEN settled THEN fwd_ret END)::numeric, 6) AS fwd,
           round(avg(CASE WHEN settled THEN net_ret END)::numeric, 6) AS net
    FROM scalp_signal_log
    WHERE features_json LIKE '%%range_position%%' AND settled
    GROUP BY 1, 2 ORDER BY 1, 2
""")
print("\n== direction x range_position 五分位 ==")
print(f"{'dir':>5} {'pos':>5} {'n':>7} {'win%':>7} {'fwd_ret':>9} {'net_ret':>9}")
for r in rows:
    s, w = r[4], r[3]
    print(f"{r[0]:>5} {r[1]:>5} {r[2]:>7} {(w/s*100) if s else 0:6.1f}% "
          f"{float(r[5] or 0)*100:+8.4f}% {float(r[6] or 0)*100:+8.4f}%")

# ── direction × rsi 桶 ──
cols, rows = q("""
    SELECT direction, width_bucket((features_json::jsonb->>'rsi')::float, 0, 100, 5) AS b,
           count(*) AS n,
           sum(CASE WHEN settled AND win THEN 1 ELSE 0 END) AS w_n,
           sum(CASE WHEN settled THEN 1 ELSE 0 END) AS s_n,
           round(avg(CASE WHEN settled THEN fwd_ret END)::numeric, 6) AS fwd
    FROM scalp_signal_log
    WHERE features_json LIKE '%%range_position%%' AND settled
    GROUP BY 1, 2 ORDER BY 1, 2
""")
print("\n== direction x rsi 桶 ==")
print(f"{'dir':>5} {'rsi':>5} {'n':>7} {'win%':>7} {'fwd_ret':>9}")
for r in rows:
    s, w = r[4], r[3]
    print(f"{r[0]:>5} {r[1]:>5} {r[2]:>7} {(w/s*100) if s else 0:6.1f}% "
          f"{float(r[5] or 0)*100:+8.4f}%")

# ── direction × amplitude_pct 桶 ──
cols, rows = q("""
    SELECT direction, width_bucket((features_json::jsonb->>'amplitude_pct')::float, 0, 0.08, 8) AS b,
           count(*) AS n,
           sum(CASE WHEN settled AND win THEN 1 ELSE 0 END) AS w_n,
           sum(CASE WHEN settled THEN 1 ELSE 0 END) AS s_n,
           round(avg(CASE WHEN settled THEN fwd_ret END)::numeric, 6) AS fwd
    FROM scalp_signal_log
    WHERE features_json LIKE '%%range_position%%' AND settled
    GROUP BY 1, 2 ORDER BY 1, 2
""")
print("\n== direction x amplitude 桶（每桶 1%）==")
print(f"{'dir':>5} {'amp':>5} {'n':>7} {'win%':>7} {'fwd_ret':>9}")
for r in rows:
    s, w = r[4], r[3]
    print(f"{r[0]:>5} {r[1]:>5} {r[2]:>7} {(w/s*100) if s else 0:6.1f}% "
          f"{float(r[5] or 0)*100:+8.4f}%")

# ── 双确认 vs 单边极端 ──
cols, rows = q("""
    SELECT direction,
           CASE
             WHEN direction='long' AND (features_json::jsonb->>'range_position')::float<=0.3
                  AND (features_json::jsonb->>'rsi')::float<=40 THEN 'long_dual'
             WHEN direction='long' THEN 'long_partial'
             WHEN direction='short' AND (features_json::jsonb->>'range_position')::float>=0.7
                  AND (features_json::jsonb->>'rsi')::float>=60 THEN 'short_dual'
             ELSE 'short_partial'
           END AS grp,
           count(*) AS n,
           sum(CASE WHEN settled AND win THEN 1 ELSE 0 END) AS w_n,
           sum(CASE WHEN settled THEN 1 ELSE 0 END) AS s_n,
           round(avg(CASE WHEN settled THEN fwd_ret END)::numeric, 6) AS fwd
    FROM scalp_signal_log
    WHERE features_json LIKE '%%range_position%%' AND settled
    GROUP BY 1, 2 ORDER BY 1, 2
""")
print("\n== 双确认(位置+RSI) vs 单边 ==")
for r in rows:
    s, w = r[4], r[3]
    print(f"{r[0]:>5} {r[1]:>14} n={r[2]:>7} win={(w/s*100) if s else 0:5.1f}% "
          f"fwd={float(r[5] or 0)*100:+.4f}%")

# ── 信号分 vs 前向收益 ──
cols, rows = q("""
    SELECT corr(factor_score, fwd_ret), corr(factor_score, win::int)
    FROM scalp_signal_log
    WHERE features_json LIKE '%%range_position%%' AND settled
""")
print("\ncorr(score, fwd_ret) =", rows[0][0], " corr(score, win) =", rows[0][1])

# ── 时间外稳健性 ──
cols, rows = q("""
    SELECT (signal_ts < (SELECT (min(signal_ts)+max(signal_ts))/2 FROM scalp_signal_log
                          WHERE features_json LIKE '%%range_position%%')) AS early,
           direction,
           count(*) AS n,
           sum(CASE WHEN settled AND win THEN 1 ELSE 0 END) AS w_n,
           sum(CASE WHEN settled THEN 1 ELSE 0 END) AS s_n,
           round(avg(CASE WHEN settled THEN fwd_ret END)::numeric, 6) AS fwd
    FROM scalp_signal_log
    WHERE features_json LIKE '%%range_position%%' AND settled
    GROUP BY 1, 2 ORDER BY 1, 2
""")
print("\n== 时间外稳健性（前半 vs 后半）==")
for r in rows:
    s, w = r[4], r[3]
    print(f"{'early' if r[0] else 'late '} {r[1]:>5} n={r[2]:>7} win={(w/s*100) if s else 0:5.1f}% "
          f"fwd={float(r[5] or 0)*100:+.4f}%")
