# -*- coding: utf-8 -*-
"""H407 #2 时代 edge 归因：分币种 × 出口路径的逐腿净 bp 分解。

用途：01:00 判决（预计 PASS 摘 4 币）后，6 币幸存宇宙的 edge 来源可读化——
哪些币/哪些出口路径贡献正期望、哪些在漏血。口径与 h369（#1 时代归因）同构。
只读，不写任何状态。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h407_trial2_attribution.json"
WINDOW_START = "2026-09-27 13:00:00+08"


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
                SELECT symbol,
                       COALESCE(NULLIF(meta_json->>'exit_path',''), 'maker') AS ep,
                       count(*), sum(net_bp)::float8, avg(net_bp)::float8
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND ts > %s::timestamptz
                GROUP BY symbol, ep
                ORDER BY symbol, sum(net_bp) DESC
            """, (WINDOW_START,))
            rows = cur.fetchall()

    by_sym = {}
    for sym, ep, n, tot, avg in rows:
        by_sym.setdefault(sym, []).append(
            {"ep": ep, "n": int(n), "net_bp": round(float(tot or 0.0), 1),
             "mean_bp": round(float(avg or 0.0), 3)})

    print(f"{'币':<6} {'出口':<20} {'腿数':>6} {'净bp':>10} {'均值bp':>8}")
    print("-" * 56)
    tot_all = 0.0
    for sym, eps in sorted(by_sym.items()):
        for e in eps:
            print(f"{sym:<6} {e['ep']:<20} {e['n']:>6} {e['net_bp']:>+10.1f} {e['mean_bp']:>+8.3f}")
            tot_all += e["net_bp"]
        s = sum(e["net_bp"] for e in eps)
        n = sum(e["n"] for e in eps)
        print(f"  {'∑':<4} {sym:<20} {n:>6} {s:>+10.1f} {s/max(n,1):>+8.3f}")
    print(f"\n窗口总净 bp = {tot_all:+.1f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "window_start": WINDOW_START,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "by_symbol": by_sym, "total_net_bp": round(tot_all, 1),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
