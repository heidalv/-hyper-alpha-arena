"""h524：**当前时代**逐币决策表——剔币/加权的唯一依据。

背景（h523）：09-28 13:00（本地）旧宇宙（ETH/BTC/DOGE/SUI/ADA）整体停摆，之后只剩
BNB/XRP/NEAR/ARB/ENA。此前 h519 的 72h 窗口混了两个时代，其"74% 的往返 0 止损"
属旧时代、不可执行 ⇒ 所有跨币结论必须在当前时代内重算。

本脚本给出**单一决策表**：每个币的腿数、净 bp/腿（含 t）、净额 $、$/h、止损腿数与
止损金额占比；并直接回答"若剔掉某些币，腿速与盈亏会变成什么样"（腿速硬约束 ≥60/h）。

用法：
  python scripts/h524_era_coin_table.py                 # 默认当前时代（09-28 13:00 本地起）
  python scripts/h524_era_coin_table.py --hours 72      # 换成滚动窗口对照
  python scripts/h524_era_coin_table.py --since "2026-09-28 13:00"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h524_era_coin_table.json"
ERA_DEFAULT = "2026-09-28 13:00"   # 本地时间，h523 测得的宇宙切点
MANDATE_LEGS_PER_H = 60.0

Q = """
SELECT symbol,
       count(*) AS legs,
       count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','') = '') AS entries,
       COALESCE(avg(net_bp), 0)::float8 AS net_bp,
       COALESCE(stddev_samp(net_bp), 0)::float8 AS sd_bp,
       COALESCE(sum(net_bp * notional / 1e4), 0)::float8 AS net_usd,
       COALESCE(sum(notional) FILTER (
           WHERE COALESCE(meta_json->>'exit_path','') = ''), 0)::float8 AS entry_notional,
       count(*) FILTER (WHERE meta_json->>'exit_path' LIKE 'stop_loss%%') AS stop_legs,
       COALESCE(sum(net_bp * notional / 1e4) FILTER (
           WHERE meta_json->>'exit_path' LIKE 'stop_loss%%'), 0)::float8 AS stop_usd,
       count(*) FILTER (WHERE meta_json->>'exit_path' LIKE 'take_profit%%') AS tp_legs,
       min(ts) AS first_ts, max(ts) AS last_ts
FROM lane_ledger
WHERE lane_id = %s AND ts >= %s
GROUP BY symbol
ORDER BY legs DESC
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
    ap.add_argument("--hours", type=float, default=0.0,
                    help=">0 时改用滚动窗口（会混时代，仅作对照）")
    ap.add_argument("--since", default=ERA_DEFAULT, help="本地时间，当前时代起点")
    a = ap.parse_args()
    dsn = read_env_dsn()
    now = dt.datetime.now()
    if a.hours > 0:
        since = now - dt.timedelta(hours=a.hours)
        label = f"近 {a.hours:g}h 滚动窗口（可能混时代）"
        hours = a.hours
    else:
        since = dt.datetime.strptime(a.since, "%Y-%m-%d %H:%M")
        hours = max((now - since).total_seconds() / 3600.0, 1e-6)
        label = f"{a.since} 起（当前时代）"
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(Q, (LANE, since))
            rows = cur.fetchall()
    if not rows:
        print("窗口内无腿")
        return 0
    tot_legs = sum(r[1] for r in rows)
    tot_usd = sum(r[5] for r in rows)
    tot_stop = sum(r[8] for r in rows)
    print(f"逐币决策表 · {label}")
    print(f"窗口 {hours:.2f}h，总腿数 {tot_legs}（{tot_legs/hours:.1f} 腿/h，"
          f"硬约束 ≥{MANDATE_LEGS_PER_H:.0f}），总净额 {tot_usd:+.2f}$ = "
          f"{tot_usd/hours:+.3f}$/h")
    print("=" * 118)
    print(f"{'币':>6s} {'腿数':>6s} {'腿/h':>6s} {'净bp/腿':>8s} {'t':>6s} "
          f"{'净额$':>9s} {'$/h':>8s} {'腿占比':>7s} {'亏损占比':>8s} "
          f"{'止损腿':>6s} {'止损$':>9s} {'止损/该币':>9s}")
    out = []
    for (s, n, ents, nb, sd, usd, en, sl, susd, tp, f0, l0) in rows:
        se = (sd / math.sqrt(n)) if n > 1 and sd else 0.0
        t = (nb / se) if se > 0 else 0.0
        leg_share = n / tot_legs
        loss_share = (susd / tot_stop) if tot_stop else 0.0
        print(f"{s:>6s} {n:6d} {n/hours:6.1f} {nb:+8.3f} {t:+6.2f} {usd:+9.3f} "
              f"{usd/hours:+8.3f} {100*leg_share:6.1f}% {100*loss_share:7.1f}% "
              f"{sl:6d} {susd:+9.3f} "
              f"{(100.0*susd/usd if usd else float('nan')):8.1f}%")
        out.append({"sym": s, "legs": n, "entries": ents, "legs_per_h": round(n / hours, 2),
                    "net_bp": round(nb, 3), "t": round(t, 2), "net_usd": round(usd, 3),
                    "usd_per_h": round(usd / hours, 4),
                    "leg_share": round(leg_share, 4), "loss_share": round(loss_share, 4),
                    "stop_legs": sl, "stop_usd": round(susd, 3)})
    unprof = [r for r in out if r["usd_per_h"] < 0]
    prof = [r for r in out if r["usd_per_h"] >= 0]
    print("\n" + "=" * 118)
    print("剔币推演（保守假设：被剔币的腿速按比例消失，其余币的 bp/腿不变）")
    print(f"{'方案':<28s} {'腿/h':>7s} {'达 ≥60?':>9s} {'$/h':>9s} {'改善$/h':>9s} "
          f"{'止损$ 变化':>11s}")
    for r in unprof:
        keep = [x for x in out if x["sym"] != r["sym"]]
        kl = sum(x["legs"] for x in keep) / hours
        ku = sum(x["usd_per_h"] for x in keep)
        print(f"{'剔 ' + r['sym']:<28s} {kl:7.1f} "
              f"{('是' if kl >= MANDATE_LEGS_PER_H else '否 ✗'):>9s} {ku:+9.3f} "
              f"{ku - tot_usd/hours:+9.3f} {-r['stop_usd']:+11.3f}")
    keep_all = [x for x in out if x["usd_per_h"] >= 0]
    if unprof:
        kl = sum(x["legs"] for x in keep_all) / hours
        ku = sum(x["usd_per_h"] for x in keep_all)
        print(f"{'剔全部亏损币':<28s} {kl:7.1f} "
              f"{('是' if kl >= MANDATE_LEGS_PER_H else '否 ✗'):>9s} {ku:+9.3f} "
              f"{ku - tot_usd/hours:+9.3f} {-tot_stop:+11.3f}")
    print(f"\n止损合计 {tot_stop:+.3f}$（占总净额 "
          f"{(100.0*tot_stop/tot_usd if tot_usd else float('nan')):.0f}%）")
    OUT.write_text(json.dumps({"label": label, "hours": round(hours, 3),
                               "total_legs": tot_legs, "total_usd": round(tot_usd, 3),
                               "total_stop_usd": round(tot_stop, 3),
                               "legs_per_h": round(tot_legs / hours, 2),
                               "coins": out}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
