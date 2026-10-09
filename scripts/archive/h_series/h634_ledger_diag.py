"""h634 — 腿量口径对账（只读）：h633 说"6 小时无腿"与 ② 判定说"12h/692 腿"互相矛盾。

矛盾必须先解开再谈归因（否则就是又一次"读数看一半"✗）：
  h633：`max(ts)` = 19:02 本地、近 6 小时 0 腿
  ② 判定：692 腿 / 11.967 小时覆盖（判定时刻 21:48 本地）

两个可能的解释：
  (a) h633 的 `interval '%s hours'` 参数化写法有问题（占位符落在字符串字面量里）
      ⇒ 本脚本一律用**字面量** interval（`interval '24 hours'`）对照 ✗→✓
  (b) `lane_id` 不同（判定脚本与我的口径不是同一个键）
      ⇒ 本脚本按 lane_id 分组，把每个 lane 的行数与最新时间都摆出来

只读，不写任何表。
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]


def main() -> int:
    import psycopg

    with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
        print("=" * 92)
        print("h634 — 腿量口径对账")
        print("=" * 92)
        print(f"  判定脚本用的 lane = {h.LANE!r}")
        print(f"  本机 now()        = ", end="")
        cur.execute("SELECT now()")
        print(cur.fetchone()[0])

        print("\n[1] 全表按 lane_id 分组（近 48h）")
        cur.execute("""
            SELECT lane_id, count(*) AS n, max(ts) AS last_ts
            FROM lane_ledger
            WHERE ts > now() - interval '48 hours'
            GROUP BY lane_id ORDER BY n DESC""")
        rows = cur.fetchall()
        if not rows:
            print("    （近 48h 一行都没有 ✗）")
        for lid, n, last in rows:
            print(f"    {str(lid):<24} n={n:>6}  last={last}")

        print("\n[2] 不加 lane 过滤，按小时分行（近 26 小时；空行 = 该小时 0 腿）")
        cur.execute("""
            SELECT date_trunc('hour', ts) AS hh, count(*) AS n
            FROM lane_ledger
            WHERE ts > now() - interval '26 hours'
            GROUP BY hh ORDER BY hh DESC""")
        got = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT date_trunc('hour', now())")
        cur_h = cur.fetchone()[0]
        import datetime as dt
        for k in range(0, 26):
            hh = cur_h - dt.timedelta(hours=k)
            n = got.get(hh, 0)
            flag = "" if n else "   ← 0 腿 ✗"
            print(f"    {hh:%m-%d %H:%M}  {n:>5}{flag}")

        print("\n[3] 同一条 6 小时口径，字面量 vs 参数化（h633 用的参数化）")
        for sql, tag in (
            ("SELECT count(*) FROM lane_ledger WHERE lane_id=%s "
             "AND ts > now() - interval '6 hours'", "字面量 6 hours"),
            ("SELECT count(*) FROM lane_ledger WHERE lane_id=%s "
             "AND ts > now() - interval '%s hours'", "参数化 6 hours"),
        ):
            try:
                if "%s hours'" in sql and sql.count("%s") == 2:
                    cur.execute(sql, (h.LANE, 6.0))
                else:
                    cur.execute(sql, (h.LANE,))
                print(f"    {tag:<18} ⇒ {cur.fetchone()[0]} 行")
            except Exception as e:
                print(f"    {tag:<18} ⇒ ✗ {type(e).__name__}: {e}")

        print("\n[4] 逐事件类型（近 6 小时 / 近 48 小时）")
        cur.execute("""
            SELECT event,
                   count(*) FILTER (WHERE ts > now() - interval '6 hours') AS h6,
                   count(*) FILTER (WHERE ts > now() - interval '48 hours') AS h48,
                   max(ts) AS last
            FROM lane_ledger GROUP BY event ORDER BY h48 DESC""")
        for ev, h6, h48, last in cur.fetchall():
            print(f"    {str(ev):<16} 6h={h6:>5}  48h={h48:>6}  last={last}")

        print("\n[5] 最后一腿的明细（最近 3 行）")
        cur.execute("""
            SELECT ts, symbol, event, notional, net_bp
            FROM lane_ledger ORDER BY ts DESC LIMIT 3""")
        for r in cur.fetchall():
            print(f"    {r[0]}  {r[1]:<8} {r[2]:<10} notional={r[3]} net_bp={r[4]}")

    print("\n" + "=" * 92)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
