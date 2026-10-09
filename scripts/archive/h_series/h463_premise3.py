"""h463 前提检查 ③（只读）：先搞清 lane_ledger 的行结构，再谈持仓时长。

②的配对失败（4604 腿只配出 42 组、还有负时长）⇒ position_id 不是"往返键"。
本脚本只做**结构诊断**：position_id / position_id_state / event 的取值分布、
前后行样本，找出真正能配对的键。

用法：python scripts/h463_premise3.py
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")
import pathlib

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


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
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT count(*), count(position_id), count(position_id_state), "
                "count(DISTINCT position_id) FROM lane_ledger "
                "WHERE lane_id=%s AND ts > now() - interval '30 hours'", (LANE,))
            print("行数/position_id非空/state非空/去重:",
                  cur.fetchone())
            cur.execute(
                "SELECT event, count(*) FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - interval '30 hours' GROUP BY 1 ORDER BY 2 DESC",
                (LANE,))
            print("event 分布:", cur.fetchall())
            cur.execute(
                "SELECT CASE WHEN meta_json->>'exit_path' IS NULL THEN 'entry' "
                "ELSE 'exit' END AS k, count(*), count(position_id), "
                "count(DISTINCT position_id) FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - interval '30 hours' GROUP BY 1", (LANE,))
            print("entry/exit × position_id:", cur.fetchall())
            print("=" * 74)
            cur.execute(
                "SELECT ts, symbol, position_id, position_id_state, event, net_bp, "
                "COALESCE(meta_json->>'exit_path','') AS ep, "
                "COALESCE(meta_json->>'source','') AS src, "
                "COALESCE(meta_json->>'side','') AS side, "
                "COALESCE(meta_json->>'fill_px','') AS px, "
                "COALESCE(meta_json->>'qty','') AS qty, "
                "COALESCE(meta_json->>'flatten','') AS flat "
                "FROM lane_ledger WHERE lane_id=%s AND ts > now() - interval '3 hours' "
                "ORDER BY ts DESC LIMIT 22", (LANE,))
            print("近 3h 末 22 行（倒序）:")
            for r in cur.fetchall():
                print(f"  {r[0]:%H:%M:%S} {r[1]:5s} pid={str(r[2])[:8]:8s} "
                      f"st={str(r[3])[:8]:8s} {r[4]:9s} net={r[5]:8.2f} "
                      f"ep={r[6]:20s} src={r[7]:10s} side={r[8]:4s} "
                      f"qty={r[10][:8]:8s} flat={r[11][:4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
