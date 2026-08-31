"""短线修复后样本质量报告（⑥ 正盈利验证判据，2026-09-01）。

判据：
  1. 修复后（2026-08-31 00:00 起）短线每笔期望 ≥ 0 且样本 ≥ 30 笔；或
  2. 修复后多日滚动窗口净额为正。

用法: backend\\.venv\\Scripts\\python.exe scripts\\short_tier_postfix_report.py
只读；用 admin 租户 326 查询 trade_facts（该表无 tenant 列，全量）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402

_POSTFIX_TS = "2026-08-31 00:00:00"


def main() -> int:
    con = psycopg.connect(
        "host=localhost port=5432 dbname=alpha_arena user=laobao password=alpha_pass"
    )
    cur = con.cursor()
    cur.execute(
        """
        SELECT ts::date AS d, COUNT(*) AS n,
               ROUND(SUM(pnl)::numeric,2) AS pnl,
               ROUND(AVG(pnl)::numeric,3) AS avg_p,
               ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS wr
        FROM trade_facts
        WHERE tier='short' AND ts >= %s::timestamp
        GROUP BY 1 ORDER BY 1
        """,
        (_POSTFIX_TS,),
    )
    rows = cur.fetchall()
    con.close()

    total_n = sum(int(r[1]) for r in rows)
    total_pnl = float(sum(r[2] for r in rows))
    avg_p = (total_pnl / total_n) if total_n else 0.0
    wr = (100.0 * sum(int(r[1]) * (float(r[4] or 0) / 100.0) for r in rows) / total_n) if total_n else 0.0

    print("== 短线修复后样本（%s 起）==" % _POSTFIX_TS)
    print(f"{'日期':<12}{'笔数':>6}{'净盈亏':>10}{'每笔':>9}{'胜率%':>8}")
    for d, n, pnl, avg, w in rows:
        print(f"{str(d):<12}{n:>6}{pnl:>10}{avg:>9}{w:>8}")
    print("-" * 48)
    print(f"{'合计':<12}{total_n:>6}{total_pnl:>10}{avg_p:>9}{wr:>8.1f}")
    print()
    print("判据1（样本≥30 且每笔期望≥0）: %s" % (
        "达标" if total_n >= 30 and avg_p >= 0 else
        f"未达标（样本 {total_n}/30，每笔 {avg_p:+.3f}）"
    ))
    pos_days = sum(1 for r in rows if r[2] > 0)
    print("判据2（修复后窗口净额>0）: %s" % (
        "达标" if total_pnl > 0 else f"未达标（净 {total_pnl:+.2f}，正日 {pos_days}/{len(rows)}）"
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
