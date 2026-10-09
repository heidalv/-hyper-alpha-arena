"""h528：打印线上登记表 `params` 全量 + 每个币的**实际单笔名义**分布。

用途：h527 逐币旋钮要落地，必须知道**当前生效值**（尤其 `max_net_directional_ratio`
与 `spread_mult`），否则取值就是拍数字；并且要知道每个币实际成交的单笔名义
（单币敞口闸的触顶值 = equity × ratio），才能把"缩小到多少"写成可验证的预期。

用法：python scripts/h528_live_params.py [--hours 6]
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
OUT = ROOT / "research_l1" / "out" / "h528_live_params.json"

# h527 关心的键（其余只在末尾汇总）
KEY = ["spread_mult", "spread_mult_reduce", "max_net_directional_ratio",
       "max_net_exposure_ratio", "max_gross_notional_ratio", "compound_ratio",
       "per_symbol_spread_mult", "per_symbol_max_notional_ratio",
       "max_leg_notional_mult", "w_base_bp", "min_width_bp", "k_inv",
       "stop_loss_bp", "stop_maker_grace_sec", "ofi_flatten_maker_only",
       "max_quote_age_sec", "ofi_confirm_threshold", "vol_pause_sigma"]


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
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            meta = row[0] if row else {}
            params = (meta or {}).get("params") or {}
            print(f"登记表 params（lane={LANE}，共 {len(params)} 键）")
            print("=" * 78)
            for k in KEY:
                print(f"  {('★ ' if k in ('per_symbol_spread_mult', 'per_symbol_max_notional_ratio') else '  ')}"
                      f"{k:<32s} = {params.get(k, '（未设置）')}")
            extra = sorted(set(params) - set(KEY))
            print(f"\n  其余 {len(extra)} 键：" + "、".join(extra[:20]))
            print(f"\n  symbols = {meta.get('symbols')}")
            print(f"  status  = {meta.get('status')} / mode = {meta.get('mode')}")
            # equity：从账户重置点后的净额推（这里只取登记表里的余量字段）
            for k in ("equity", "equity_usd"):
                if k in meta:
                    print(f"  {k} = {meta[k]}")
            cur.execute("""
                SELECT symbol,
                       count(*) AS legs,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY notional)::float8 AS p50,
                       percentile_cont(0.9) WITHIN GROUP (ORDER BY notional)::float8 AS p90,
                       max(notional)::float8 AS mx,
                       avg(notional)::float8 AS mean
                FROM lane_ledger
                WHERE lane_id=%s AND ts > now() - make_interval(hours => %s::int)
                GROUP BY symbol ORDER BY legs DESC""", (LANE, int(a.hours)))
            rows = cur.fetchall()
    print(f"\n近 {a.hours:g}h 实际单笔名义（$）—— 单币敞口闸触顶值 = equity × ratio")
    print("=" * 78)
    print(f"{'币':>6s} {'腿数':>6s} {'P50':>8s} {'P90':>8s} {'均值':>8s} {'最大':>9s}")
    coin = []
    for s, n, p50, p90, mx, mean in rows:
        print(f"{s:>6s} {n:6d} {p50:8.1f} {p90:8.1f} {mean:8.1f} {mx:9.1f}")
        coin.append({"sym": s, "legs": n, "p50": round(p50, 1), "p90": round(p90, 1),
                     "mean": round(mean, 1), "max": round(mx, 1)})
    OUT.write_text(json.dumps({"params": params, "key_params": {k: params.get(k)
                                                               for k in KEY},
                               "coins": coin, "hours": a.hours},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
