"""诊断 2：AI 选币到底还在不在写？（用 DB 写入节奏判断，不依赖进程内状态）

我的第一个诊断里 `scheduler._task = None` 是**我自己脚本进程**的状态，
不能代表后端服务进程 ⇒ 那个结论无效，必须用 DB 的写入节奏重新判断。

本脚本看 `coin_select_candidates` 的**按小时写入量**，判断：
  · 若最近几小时仍在写 ⇒ 有东西在跑
  · 若停在某个时刻 ⇒ 已停
并看写入的 symbol 是否包含 AI 选出的那些（VIRTUAL/PENDLE/SEI/ONDO/ARB）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=" * 92)
    print("coin_select_candidates 按小时写入量（近 48 小时）")
    print("=" * 92)
    cur.execute(
        "SELECT date_trunc('hour', created_at) h, count(*) n,"
        "       count(DISTINCT symbol) nsym, count(DISTINCT scan_id) nscan"
        "  FROM coin_select_candidates"
        " WHERE created_at > now() - interval '48 hours'"
        " GROUP BY 1 ORDER BY 1 DESC LIMIT 30")
    rows = cur.fetchall()
    if not rows:
        print("  近 48 小时无写入")
    for r in rows:
        print(f"  {str(r['h'])[:16]:<18} 行 {r['n']:>6,}  币 {r['nsym']:>3}  "
              f"scan {r['nscan']:>3}")

    print("\n" + "=" * 92)
    print("coin_select_scans 按天（扫描任务本身）")
    print("=" * 92)
    cur.execute(
        "SELECT date_trunc('day', started_at) d, count(*) n,"
        "       max(finished_at) mx,"
        "       string_agg(DISTINCT status, ',') st"
        "  FROM coin_select_scans"
        " WHERE started_at > now() - interval '14 days'"
        " GROUP BY 1 ORDER BY 1 DESC")
    for r in cur.fetchall():
        print(f"  {str(r['d'])[:10]}  扫描 {r['n']:>4} 次   最后完成 {r['mx']}   状态[{r['st']}]")

    print("\n" + "=" * 92)
    print("最近 3 次扫描的详情")
    print("=" * 92)
    cur.execute(
        "SELECT scan_id, status, started_at, finished_at, duration_sec,"
        "       candidates_scanned, candidates_ai, error_message"
        "  FROM coin_select_scans ORDER BY started_at DESC LIMIT 3")
    for r in cur.fetchall():
        print(f"\n  scan_id={r['scan_id']}  status={r['status']}")
        print(f"    {r['started_at']} → {r['finished_at']}  用时 {r['duration_sec']}s")
        print(f"    扫描候选 {r['candidates_scanned']}  AI 评估 {r['candidates_ai']}")
        if r["error_message"]:
            print(f"    error: {str(r['error_message'])[:200]}")

    print("\n" + "=" * 92)
    print("最近写入的候选（symbol / horizon / score）")
    print("=" * 92)
    cur.execute(
        "SELECT created_at, symbol, horizon, score, confidence,"
        "       left(coalesce(ai_verdict,''),20) verdict"
        "  FROM coin_select_candidates ORDER BY created_at DESC LIMIT 12")
    for r in cur.fetchall():
        print(f"  {str(r['created_at'])[:19]}  {str(r['symbol']):<10} "
              f"{str(r['horizon']):<9} score={r['score']} conf={r['confidence']} "
              f"verdict={r['verdict']}")

    cur.execute(
        "SELECT horizon, count(*) n, max(created_at) mx"
        "  FROM coin_select_candidates GROUP BY 1 ORDER BY n DESC")
    print("\n  按 horizon 汇总：")
    for r in cur.fetchall():
        print(f"    {str(r['horizon']):<12} {r['n']:>8,} 行   最新 {r['mx']}")

    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
