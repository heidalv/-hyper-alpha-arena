# -*- coding: utf-8 -*-
"""H354 P2 试跑基线快照：部署时刻前 12h 的车道账本口径。

产出 research_l1/out/h354_p2_baseline.json —— 12h 判定脚本把试跑期同口径
数字与它对比（net_bp/腿、fill 频率、分币种、分 exit_path）。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h354_p2_baseline.json"


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    import psycopg

    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                WITH win AS (
                  SELECT * FROM lane_ledger
                  WHERE lane_id='mm_asterdex'
                    AND ts > now() - interval '12 hours'
                    AND ts <= now()
                )
                SELECT symbol,
                       count(*) AS legs,
                       sum(net_bp)::float8 AS net_bp,
                       sum(price_bp)::float8 AS price_bp,
                       sum(fee_bp)::float8 AS fee_bp,
                       sum(spread_bp)::float8 AS spread_bp,
                       sum(notional)::float8 AS notional,
                       count(*) FILTER (WHERE meta_json->>'exit_path' LIKE '%%flatten%%')
                         AS flatten_legs
                FROM win GROUP BY symbol ORDER BY symbol
            """)
            rows = cur.fetchall()
            cur.execute("""
                SELECT meta_json->>'exit_path' AS ep, count(*), avg(net_bp)::float8
                FROM lane_ledger
                WHERE lane_id='mm_asterdex'
                  AND ts > now() - interval '12 hours'
                  AND ts <= now()
                GROUP BY 1 ORDER BY 3 DESC
            """)
            by_exit = cur.fetchall()

    total = {
        "legs": sum(r[1] for r in rows),
        "net_bp": sum(r[2] or 0 for r in rows),
        "price_bp": sum(r[3] or 0 for r in rows),
        "fee_bp": sum(r[4] or 0 for r in rows),
        "spread_bp": sum(r[5] or 0 for r in rows),
        "notional": sum(r[6] or 0 for r in rows),
    }
    if total["legs"]:
        total["net_bp_per_leg"] = total["net_bp"] / total["legs"]
        total["legs_per_hour"] = total["legs"] / 12.0
    out = {
        "window": "deploy_t - 12h .. deploy_t",
        "total": total,
        "per_symbol": [
            {"symbol": r[0], "legs": r[1], "net_bp": r[2], "price_bp": r[3],
             "fee_bp": r[4], "spread_bp": r[5], "notional": r[6],
             "flatten_legs": r[7]} for r in rows],
        "by_exit_path": [{"exit": r[0], "legs": r[1], "avg_net_bp": r[2]}
                         for r in by_exit],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n基线已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
