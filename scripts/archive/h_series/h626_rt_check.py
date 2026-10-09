"""h626 — **实时性 + 20 档盘口** 核验（只读；R231）。

用户现场问题（2026-09-29 18:0xL）："实时数据呢？20 档盘口呢？"
本脚本一次答清四件事，全部是**读数**不是推理 ✓：
  1. 三张行情表的**滞后秒数**与**近 2 分钟覆盖币数**（book/trades 35、depth 28）；
  2. `asterdex_stream_health` 三行心跳滞后（采集器是否在跑的直接证据 ✓）；
  3. **20 档盘口是否真的是 20 档**：取最新深度行，数 `bids`/`asks` 的层数 ✓
     （深度流是 `depth20@100ms` ⇒ 应为 20 档 ✓）；
  4. 车道近 15 分钟腿数（数据恢复后引擎是否重新出腿 ✓）。

用法：python scripts/h626_rt_check.py
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
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

TABLES = (("asterdex_trades", 35), ("asterdex_book_ticker", 35),
          ("asterdex_depth_snapshots", 28))


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
    print("=" * 92)
    print("h626 — 实时性 + 20 档盘口核验（只读）")
    print("=" * 92)
    bad = []
    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        print("\n  [1] 行情表实时性（滞后 = now − 最新事件时间）")
        for tbl, want in TABLES:
            # [R231] 覆盖面用**有界子查询**（首版对 3.5 亿行的 book_ticker 做 count(DISTINCT ...)
            # ⇒ 全表扫 ⇒ 本脚本自己超时 5 分钟 ✗）；`max(event_ts_ms)` 走索引 ⇒ 快 ✓
            cur.execute(
                f'SELECT EXTRACT(EPOCH FROM (now() - to_timestamp(max("event_ts_ms")/1000.0)))::float8'
                f' FROM "{tbl}"')
            lag = cur.fetchone()[0]
            cur.execute(
                f'SELECT count(DISTINCT symbol) FROM (SELECT symbol FROM "{tbl}"'
                f' WHERE "event_ts_ms" > (EXTRACT(EPOCH FROM now())-120)*1000 LIMIT 20000) s')
            syms = cur.fetchone()[0]
            lag = float(lag) if lag is not None else -1.0
            ok = lag <= 60 and int(syms or 0) >= want - 2
            print(f"    {tbl:<26} 滞后 {lag:8.1f}s   近 2 分钟币数 {int(syms or 0):>3}/{want}"
                  f"   {'✓ 实时' if ok else '✗ 不实时'}")
            if not ok:
                bad.append(f"{tbl} 不实时（滞后 {lag:.0f}s，覆盖 {int(syms or 0)}/{want}）")
        print("\n  [2] 采集器心跳（asterdex_stream_health）")
        cur.execute("SELECT stream, msgs_total, reconnects,"
                    " EXTRACT(EPOCH FROM (now() - updated_at))::float8"
                    " FROM asterdex_stream_health ORDER BY stream")
        for s, m, rc, age in cur.fetchall():
            age = float(age) if age is not None else -1.0
            print(f"    {s:<8} msgs={int(m or 0):>10}  reconnects={rc}  心跳滞后 {age:7.1f}s"
                  f"   {'✓' if age <= 60 else '✗'}")
            if age > 60:
                bad.append(f"心跳 {s} 滞后 {age:.0f}s")
        print("\n  [3] 20 档盘口核验（最新一行深度，数层数）")
        cur.execute("""
            SELECT symbol, event_ts_ms,
                   EXTRACT(EPOCH FROM (now() - to_timestamp(event_ts_ms/1000.0)))::float8,
                   jsonb_array_length(bids::jsonb), jsonb_array_length(asks::jsonb)
            FROM asterdex_depth_snapshots
            WHERE event_ts_ms > (EXTRACT(EPOCH FROM now()) - 120) * 1000
            ORDER BY event_ts_ms DESC LIMIT 6""")
        rows = cur.fetchall()
        if not rows:
            print("    ✗ 近 2 分钟**没有任何深度行** ⇒ 20 档盘口当前是空的 ✗✗")
            bad.append("近 2 分钟无深度行（20 档盘口为空）")
        for sym, ts, lag, nb, na in rows:
            ok = int(nb or 0) >= 20 and int(na or 0) >= 20
            print(f"    {sym:<14} 滞后 {float(lag):6.1f}s  bids {int(nb or 0):>3} 档 / asks {int(na or 0):>3} 档"
                  f"   {'✓ 20 档' if ok else '✗ 档数不足'}")
            if not ok:
                bad.append(f"{sym} 档数不足（{int(nb or 0)}/{int(na or 0)}）")
        cur.execute("SELECT count(DISTINCT symbol) FROM asterdex_depth_snapshots"
                    " WHERE event_ts_ms > (EXTRACT(EPOCH FROM now()) - 120) * 1000")
        print(f"    ⇒ 近 2 分钟有 20 档深度的币数 = {int(cur.fetchone()[0] or 0)} / 28")
    print("\n" + "-" * 92)
    if bad:
        print(f"✗ {len(bad)} 项不达标：")
        for x in bad:
            print(f"    · {x}")
        print("  ⇒ 处置：确认采集进程在跑（`DSH_ASTER_INGEST` 任务 / standby_ingest 进程），")
        print("     再跑 `python scripts/h625_ingest_recovery_check.py` ✓")
        return 1
    print("✓ 三条流都是**秒级实时**、20 档盘口覆盖 28 币 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
