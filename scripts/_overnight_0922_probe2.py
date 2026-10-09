# -*- coding: utf-8 -*-
"""侦察 2：找权益曲线 / 配对 / 表结构。只读。"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


import psycopg  # noqa: E402

with psycopg.connect(dsn()) as c:
    cur = c.cursor()

    for t in ("lane_registry", "lane_runtime_state", "lane_shadow_report",
              "lane_ledger_pairing_gaps", "lane_breaker_log"):
        cur.execute("""select column_name, data_type from information_schema.columns
                       where table_name=%s order by ordinal_position""", (t,))
        cols = cur.fetchall()
        print(f"\n== {t} 列 ==")
        print("  ", [x[0] for x in cols])
        try:
            cur.execute(f"select count(*) from {t}")
            print("   rows:", cur.fetchone()[0])
        except Exception as e:
            print("   count err:", e)

    print("\n== lane_registry mm_asterdex 行 ==")
    cur.execute("select * from lane_registry where lane_id='mm_asterdex'")
    names = [d[0] for d in cur.description]
    for row in cur.fetchall():
        for k, v in zip(names, row):
            s = str(v)
            print(f"   {k:28s}= {s[:400]}")

    print("\n== 权益曲线候选：lane_runtime_state ==")
    try:
        cur.execute("select * from lane_runtime_state limit 5")
        names = [d[0] for d in cur.description]
        for row in cur.fetchall():
            print("  ", {k: str(v)[:120] for k, v in zip(names, row)})
    except Exception as e:
        print("   err:", e)

    print("\n== position_id 覆盖率（窗口内） ==")
    cur.execute("""
        select count(*), count(position_id),
               min(ts) filter (where position_id is not null),
               max(ts) filter (where position_id is not null)
        from lane_ledger
        where lane_id='mm_asterdex' and ts >= '2026-09-21 18:00'
    """)
    print("  ", cur.fetchone())

    print("\n== pairing_gaps 窗口内 ==")
    try:
        cur.execute("""select count(*) from lane_ledger_pairing_gaps
                       where created_at >= '2026-09-21 18:00'""")
        print("   n:", cur.fetchone()[0])
    except Exception as e:
        print("   err:", e)

    print("\n== breaker_log 窗口内 ==")
    try:
        cur.execute("""select * from lane_breaker_log
                       where ts >= '2026-09-21 18:00' order by ts limit 30""")
        names = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print("   n:", len(rows))
        for row in rows:
            print("  ", {k: str(v)[:80] for k, v in zip(names, row)})
    except Exception as e:
        print("   err:", e)

p = ROOT / "logs" / "mm_fill_basis.jsonl"
print("\n== mm_fill_basis.jsonl ==")
print("   size MB:", round(p.stat().st_size / 1e6, 2))
lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
print("   lines:", len(lines))
print("   head:", lines[0][:400])
print("   tail:", lines[-1][:400])

for name in ("mm_health_report", "mm_daily_digest", "monthly_digest"):
    for cand in (ROOT / "logs" / f"{name}.json", ROOT / "data" / f"{name}.json",
                 ROOT / "logs" / f"{name}.jsonl", ROOT / "data" / f"{name}.jsonl"):
        if cand.exists():
            print(f"\n== {cand} (mtime {datetime.fromtimestamp(cand.stat().st_mtime)}) ==")
            txt = cand.read_text(encoding="utf-8", errors="replace").splitlines()
            print("   lines:", len(txt))
            print("   tail:", txt[-1][:500] if txt else "")
