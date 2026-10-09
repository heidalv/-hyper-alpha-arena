"""小时级 入场/出场 结构（只读）：定位 09-28 13:00Z 腿速断崖的机制。

背景（h465）：09-28 12:00Z→13:00Z，腿速 225→65/h、每腿名义 232$→106$、
每腿手续费 −0.28bp→−0.74bp，此后净/腿长期 ≈ −3bp。
候选变更（h467 时间线）：h441 `ofi_flatten_threshold` 0→0.5 @13:13Z、
h442 `stop_ref_last_leg` 0→1 @13:17Z、h443 `compound_ratio` 1.0→0.5 @14:47Z。

判别口径：
  · 入场行（`exit_path` 为空）逐小时计数 ⇒ **入场闸门**是否收紧；
  · 出场行逐小时计数 + 出场路径构成 ⇒ 出场是否被改造成"等更久/taker 更多"；
  · 每腿名义 ⇒ 是否只是仓位减半（h443）而不是机会减少。

用法：python scripts/h468_entry_exit_hourly.py --hours 36
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h468_entry_exit_hourly.json"


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
    ap.add_argument("--hours", type=float, default=36.0)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT date_trunc('hour', ts) AS h, "
                "count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')='') AS ent, "
                "count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')<>'') AS exi, "
                "avg(notional) FILTER (WHERE COALESCE(meta_json->>'exit_path','')='')::float8, "
                "avg(net_bp)::float8 AS net, "
                "count(*) FILTER (WHERE meta_json->>'exit_path'='timeout_hard_taker') AS hard, "
                "count(*) FILTER (WHERE meta_json->>'exit_path' LIKE 'stop_loss%%') AS stopx, "
                "count(*) FILTER (WHERE meta_json->>'exit_path' LIKE 'ofi_flatten%%') AS ofif, "
                "count(*) FILTER (WHERE meta_json->>'exit_path' LIKE 'jump_exit%%') AS jump, "
                "count(*) FILTER (WHERE meta_json->>'exit_path' LIKE '%%taker') AS takerish, "
                "sum(notional)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) "
                "GROUP BY 1 ORDER BY 1", (LANE, a.hours))
            rows = cur.fetchall()
    print(f"近 {a.hours:.0f}h 入场/出场/路径构成（UTC）  lane={LANE}")
    print("=" * 118)
    print(f"{'小时(UTC)':>13s} {'入场':>5s} {'出场':>5s} {'入场$':>7s} {'净bp':>7s} "
          f"{'硬顶':>4s} {'止损':>4s} {'ofi平':>5s} {'跳空':>4s} {'taker':>5s} {'名义$':>9s}")
    detail = []
    for (h, ent, exi, aent, net, hard, stopx, ofif, jump, takerish, notional) in rows:
        # [h493] DB 会话时区是 Asia/Shanghai（`timestamptz` 列）⇒ psycopg 返回的是
        # **+08:00 的 aware 值**。此前直接 `{h:%H:%M}` 打印会把本地钟点标成 "UTC"，
        # 与 h465（已转 UTC）**跨表错位 8 小时**。统一到这里转 UTC 再打印。
        hh = (h.astimezone(dt.timezone.utc) if h.tzinfo
              else h.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
              .astimezone(dt.timezone.utc))
        print(f"{hh:%m-%d %H:%M} {int(ent):5d} {int(exi):5d} {float(aent or 0):7.0f} "
              f"{float(net or 0):7.2f} {int(hard):4d} {int(stopx):4d} {int(ofif):5d} "
              f"{int(jump):4d} {int(takerish):5d} {float(notional or 0):9.0f}")
        detail.append({"hour": hh.isoformat(), "entries": int(ent), "exits": int(exi),
                       "entry_notional_avg": round(float(aent or 0), 1),
                       "net_bp": round(float(net or 0), 3),
                       "hard_cap": int(hard), "stop_loss": int(stopx),
                       "ofi_flatten": int(ofif), "jump_exit": int(jump),
                       "takerish": int(takerish),
                       "notional": round(float(notional or 0), 1)})
    OUT.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 118)
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
