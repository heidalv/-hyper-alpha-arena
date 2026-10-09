"""手续费归因（只读）：钱到底被哪条出场路径的 taker 费吃掉了。

事实基础：
  · 场馆 `edge_source = "spread+mkr(asterdex maker 0%)"` ⇒ **maker 费率 0**，
    任何 taker 成交都是净支出；
  · h465（49h）：净/腿 −0.28bp = 手续费 −0.28bp + 价格 −0.04bp + 价差 +0.04bp
    ⇒ 亏损的 ~82% 就是手续费；近 6h 更极端（每腿 −1.4bp 费）。
  · h470：ofi_flatten 平仓腿**已实现 +0.17bp**（含费），出场后有利漂移 +9.9bp(t=2.15)
    ⇒ 它的价格判断是对的，但每笔付 3.53bp taker 费。

本脚本按 `exit_path` 给出近 N 小时：腿数、净 bp/腿、**费 bp/腿**、名义额、净额$、费$占比。

用法：python scripts/h471_fee_attribution.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h471_fee_attribution.json"


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
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(入场腿)') AS p, "
                "count(*), avg(net_bp)::float8, avg(fee_bp)::float8, "
                "avg(spread_bp)::float8, avg(price_bp)::float8, "
                "sum(notional)::float8, sum(net_bp*notional/1e4)::float8, "
                "sum(fee_bp*notional/1e4)::float8, sum(price_bp*notional/1e4)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) "
                "GROUP BY 1 ORDER BY 8 ASC", (LANE, a.hours))
            rows = cur.fetchall()
    tot_n = sum(int(r[1]) for r in rows)
    tot_usd = sum(float(r[7] or 0) for r in rows)
    tot_fee = sum(float(r[8] or 0) for r in rows)
    tot_not = sum(float(r[6] or 0) for r in rows)
    print(f"近 {a.hours:.0f}h 手续费归因  lane={LANE}")
    print("=" * 118)
    print(f"{'exit_path':>26s} {'腿数':>6s} {'净bp/腿':>8s} {'费bp/腿':>8s} "
          f"{'价差bp':>7s} {'价格bp':>8s} {'名义$':>10s} {'净额$':>9s} {'费$':>9s} "
          f"{'费占比':>7s}")
    for (p, n, net, fee, sp, px, noti, usd, feeusd, pxusd) in rows:
        n = int(n)
        share = (100.0 * float(feeusd or 0) / tot_fee) if tot_fee else 0.0
        print(f"{p:>26s} {n:6d} {float(net or 0):8.2f} {float(fee or 0):8.2f} "
              f"{float(sp or 0):7.2f} {float(px or 0):8.2f} {float(noti or 0):10.0f} "
              f"{float(usd or 0):9.2f} {float(feeusd or 0):9.2f} {share:6.1f}%")
    print("=" * 118)
    print(f"合计 {tot_n} 腿  名义={tot_not:.0f}$  净额={tot_usd:+.2f}$  "
          f"手续费={tot_fee:+.2f}$  净/腿={tot_usd/max(tot_n,1)*1e4/max(tot_not/max(tot_n,1),1e-9):+.2f}bp"
          f"  （{tot_usd/max(tot_n,1):+.4f}$/腿）")
    print(f"⇒ 费/净额 = {100.0*tot_fee/tot_usd if tot_usd else float('nan'):.0f}%  "
          f"（>100% 表示净亏全部来自手续费）")
    OUT.write_text(json.dumps(
        [{"path": r[0], "legs": int(r[1]), "net_bp_per_leg": round(float(r[2] or 0), 3),
          "fee_bp_per_leg": round(float(r[3] or 0), 3),
          "spread_bp_per_leg": round(float(r[4] or 0), 3),
          "price_bp_per_leg": round(float(r[5] or 0), 3),
          "notional_usd": round(float(r[6] or 0), 1),
          "net_usd": round(float(r[7] or 0), 3),
          "fee_usd": round(float(r[8] or 0), 3)} for r in rows],
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
