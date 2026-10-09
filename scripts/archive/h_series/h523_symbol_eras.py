"""h523：币种宇宙时代划分——lane_ledger 里每个币的首末腿时刻（默认 72h）。

为什么需要：h509 只统计「当前在役币」，而 h519 的 72h 窗口把**旧宇宙**（ETH/BTC/
DOGE/SUI/ADA）与新宇宙（BNB/NEAR/ARB/XRP/ENA）混在一起，导致「74% 的往返 0 止损」
这类结论不可执行 ⇒ 任何跨币比较都必须先切时代。

用法：python scripts/h523_symbol_eras.py [--hours 72]
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"

Q_ERAS = """
SELECT symbol, count(*) AS n, min(ts) AS first_ts, max(ts) AS last_ts,
       count(*) FILTER (WHERE ts > now() - interval '12 hours') AS n_12h,
       count(*) FILTER (WHERE ts > now() - interval '6 hours') AS n_6h
FROM lane_ledger
WHERE lane_id = %s AND ts > now() - make_interval(hours => %s::int)
GROUP BY symbol
ORDER BY n DESC
"""

Q_REG = """
SELECT meta_json->'params'->>'symbols' AS syms,
       meta_json->'params'->>'universe' AS uni,
       updated_at
FROM lane_registry WHERE lane_id = %s
"""


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(Q_ERAS, (LANE, int(a.hours)))
            rows = cur.fetchall()
            print(f"近 {a.hours:g}h 逐币腿数与时程（lane={LANE}）")
            print("=" * 96)
            print(f"{'币':>6s} {'腿数':>7s} {'近12h':>7s} {'近6h':>7s}  "
                  f"{'首腿':<17s} {'末腿':<17s} 状态")
            now = dt.datetime.now(dt.timezone.utc)
            for s, n, f, l, n12, n6 in rows:
                age_h = (now - l.astimezone(dt.timezone.utc)).total_seconds() / 3600.0
                if n6 > 0:
                    state = "在役"
                elif n12 > 0:
                    state = "半退（12h 内有腿）"
                else:
                    state = f"已停 {age_h:.1f}h"
                print(f"{s:>6s} {n:7d} {n12:7d} {n6:7d}  "
                      f"{f:%m-%d %H:%M:%S}     {l:%m-%d %H:%M:%S}     {state}")
            cur.execute(Q_REG, (LANE,))
            r = cur.fetchone()
            if r:
                print(f"\n登记表 symbols = {r[0]!r}\n登记表 universe = {r[1]!r}\n"
                      f"登记表 updated_at = {r[2]}")
            # 时代切点：最近一次"连续 6h 无腿"的币的最后腿时刻的中位
            dead = [l for s, n, f, l, n12, n6 in rows if n6 == 0 and n12 == 0]
            if dead:
                dead.sort()
                print(f"\n旧宇宙最后腿时刻中位 ≈ {dead[len(dead)//2]:%m-%d %H:%M:%S} UTC")
                print("⇒ 跨币比较请只取『该时刻之后』的样本（h519 需要加 --since）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
