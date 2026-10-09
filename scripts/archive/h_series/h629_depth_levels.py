"""h629 — **20 档盘口核验**（只读、快；R232）。

用户直接问的就是这个："20 档盘口呢？" ⇒ 本脚本回答两件事，且**不做全表扫**（深度表仅 ~150 万行 ✓）：
  1. 近 2 分钟内有深度的**币数**（应为 28 ✓，与深度订阅清单一致 ✓）；
  2. 抽 8 个币的**最新一行**，数 `bids`/`asks` 层数（深度流是 `depth20@100ms` ⇒ 应各 **20 档** ✓），
     并打印买一/卖一，供肉眼核对 ✓。

用法：python scripts/h629_depth_levels.py
"""
from __future__ import annotations

import json
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
import psycopg  # noqa: E402


def _dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("MARKET_DATABASE_URL") or env.get("DATABASE_URL") or ""
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    print("=" * 88)
    print("h629 — 20 档盘口核验（只读）")
    print("=" * 88)
    with psycopg.connect(_dsn(), autocommit=True, connect_timeout=10) as c, c.cursor() as cur:
        cur.execute("SET statement_timeout = '15000'")
        cur.execute("SELECT count(DISTINCT symbol) FROM asterdex_depth_snapshots"
                    " WHERE event_ts_ms > (EXTRACT(EPOCH FROM now()) - 120) * 1000")
        n = int(cur.fetchone()[0] or 0)
        print(f"\n  近 2 分钟有深度的币数 = {n} / 28  {'✓' if n >= 26 else '✗'}")
        cur.execute("""
            SELECT symbol,
                   EXTRACT(EPOCH FROM (now() - to_timestamp(event_ts_ms/1000.0)))::float8,
                   jsonb_array_length(bids::jsonb), jsonb_array_length(asks::jsonb),
                   (bids::jsonb -> 0), (asks::jsonb -> 0)
            FROM asterdex_depth_snapshots
            WHERE event_ts_ms > (EXTRACT(EPOCH FROM now()) - 120) * 1000
            ORDER BY event_ts_ms DESC LIMIT 8""")
        rows = cur.fetchall()
        if not rows:
            print("  ✗ 近 2 分钟**没有任何深度行** ⇒ 20 档盘口为空 ✗✗")
            return 1
        print(f"\n  {'币':<14}{'滞后':>8}{'bids 档':>9}{'asks 档':>9}   买一 / 卖一")
        for sym, lag, nb, na, b0, a0 in rows:
            bp = b0[0] if isinstance(b0, list) else (b0 or {}).get("0") if isinstance(b0, dict) else "?"
            ap = a0[0] if isinstance(a0, list) else (a0 or {}).get("0") if isinstance(a0, dict) else "?"
            print(f"  {sym:<14}{float(lag):7.1f}s{int(nb or 0):>9}{int(na or 0):>9}   {bp} / {ap}")
        ok = all(int(r[2] or 0) >= 20 and int(r[3] or 0) >= 20 for r in rows)
        print(f"\n  ⇒ 抽样 {len(rows)} 个币的档数 {'全部 ≥20 档 ✓' if ok else '**有不足 20 档的币 ✗**'}")
    print("\n  判读：若这里 ✓ 而看板仍显示「该币无深度采集」⇒ 看板走的是**另一条读路径**")
    print("        （`board.py` 的 `_KNOWN_DEPTH_SYMS` / 深度缓存），不是采集没数据 ✓")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
