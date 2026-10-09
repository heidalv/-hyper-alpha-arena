"""小时级净额分解（只读）：腿速合规 + 净/腿的来源结构。

用户的硬约束是 **≥60 腿/h**（绝对），并且要"能赚钱"。
本脚本按 UTC 小时给出：腿数 / 腿速 / 净 bp / 价差 / 价格 / 手续费 分量 / 名义额 /
净额美元 / 标准误，用来回答两件事：
  Q1 哪些小时掉到 60 腿/h 以下（合规风险窗口）？
  Q2 净/腿为负的小时，亏在**价差**、**价格**还是**手续费**？

⚠️ 最后一个小时桶通常**不完整**（例如 03:27 跑，03:00 桶只含 27 分钟）
⇒ 直接按整点算腿速会报出假的"低频"。本脚本按时长外推并标注 `(仅Nmin)`，
合规计数也把未走完的小时单列（[h485] 修复；此前会把半截小时误报成低频）。

用法：python scripts/h465_hourly.py --hours 48
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
OUT = ROOT / "research_l1" / "out" / "h465_hourly.json"


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
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT date_trunc('hour', ts) AS h, count(*) AS n, "
                "avg(net_bp)::float8, avg(spread_bp)::float8, avg(price_bp)::float8, "
                "avg(fee_bp)::float8, avg(slippage_bp)::float8, "
                "sum(net_bp * notional / 1e4)::float8, sum(notional)::float8, "
                "stddev_samp(net_bp)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) "
                "GROUP BY 1 ORDER BY 1", (LANE, a.hours))
            rows = cur.fetchall()
    now = dt.datetime.now(dt.timezone.utc)
    print(f"近 {a.hours:.0f}h 小时级（UTC）  lane={LANE}")
    print("=" * 104)
    print(f"{'小时(UTC)':>13s} {'腿数':>5s} {'腿/h':>6s} {'净bp':>7s} {'±SE':>6s} "
          f"{'价差':>7s} {'价格':>7s} {'费':>7s} {'滑点':>6s} {'净额$':>9s} {'名义$':>10s}")
    ok_h = bad_h = partial = 0
    tot = {"n": 0, "net": 0.0, "sp": 0.0, "px": 0.0, "fee": 0.0, "usd": 0.0, "not": 0.0}
    detail = []
    for h, n, net, sp, px, fee, slip, usd, notional, sd in rows:
        n = int(n)
        hh = h if h.tzinfo else h.replace(tzinfo=dt.timezone.utc)
        hh = hh.astimezone(dt.timezone.utc)
        se = (float(sd) / (n ** 0.5)) if (sd and n > 1) else float("nan")
        # [h485] 未走完的小时：按时长外推，单列计数，避免误报"低频"
        elapsed_min = 60.0
        if hh.date() == now.date() and hh.hour == now.hour:
            elapsed_min = max(1.0, (now - hh).total_seconds() / 60.0)
        is_partial = elapsed_min < 59.0
        rate_adj = n * 60.0 / elapsed_min
        flag = "OK " if rate_adj >= 60 else "低频"
        if is_partial:
            flag += f"(仅{elapsed_min:.0f}min，外推{rate_adj:.0f}/h)"
            partial += 1
        if rate_adj >= 60:
            ok_h += 1
        else:
            bad_h += 1
        print(f"{hh:%m-%d %H:%M} {n:5d} {n:6.0f} {float(net):7.2f} {se:6.2f} "
              f"{float(sp or 0):7.2f} {float(px or 0):7.2f} {float(fee or 0):7.2f} "
              f"{float(slip or 0):6.2f} {float(usd or 0):9.2f} "
              f"{float(notional or 0):10.0f}  {flag}")
        tot["n"] += n
        tot["net"] += float(net or 0) * n
        tot["sp"] += float(sp or 0) * n
        tot["px"] += float(px or 0) * n
        tot["fee"] += float(fee or 0) * n
        tot["usd"] += float(usd or 0)
        tot["not"] += float(notional or 0)
        detail.append({"hour": hh.isoformat(), "legs": n, "legs_per_h": n,
                       "partial_hour": is_partial,
                       "legs_per_h_extrapolated": round(rate_adj, 1),
                       "net_bp": round(float(net or 0), 3), "se": round(se, 3),
                       "spread_bp": round(float(sp or 0), 3),
                       "price_bp": round(float(px or 0), 3),
                       "fee_bp": round(float(fee or 0), 3),
                       "net_usd": round(float(usd or 0), 3)})
    print("=" * 104)
    n_tot = max(tot["n"], 1)
    print(f"合计 {tot['n']} 腿（{len(rows)} 小时，其中 {partial} 个未走完）  "
          f"净/腿={tot['net']/n_tot:+.2f}bp  价差={tot['sp']/n_tot:+.2f}  "
          f"价格={tot['px']/n_tot:+.2f}  费={tot['fee']/n_tot:+.2f}")
    print(f"净额={tot['usd']:+.2f}$  名义={tot['not']:.0f}$  "
          f"合规：≥60腿/h 的小时 {ok_h}/{len(rows)}（低频 {bad_h}，其中未走完 {partial}）")
    OUT.write_text(json.dumps({"hours": detail, "totals": {
        "legs": tot["n"], "net_bp_per_leg": round(tot["net"] / n_tot, 3),
        "spread_bp_per_leg": round(tot["sp"] / n_tot, 3),
        "price_bp_per_leg": round(tot["px"] / n_tot, 3),
        "fee_bp_per_leg": round(tot["fee"] / n_tot, 3),
        "net_usd": round(tot["usd"], 3), "notional_usd": round(tot["not"], 1),
        "hours_ge_60": ok_h, "hours_lt_60": bad_h, "partial_hours": partial}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
