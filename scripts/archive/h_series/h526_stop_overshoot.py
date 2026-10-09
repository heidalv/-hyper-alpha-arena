"""h526：止损路径的 **20bp 溢出**到底是什么——系统性滑点还是少数跳空？

事实（h525，当前时代）：
  · `stop_loss_taker` 42 腿、净 −18.731$ = **总亏损的 92%**，加权 −59.637bp/腿；
  · 止损触发阈值 `stop_loss_bp = 40`，费用只 −1.26$（7%）。
  ⇒ 说明"触发 40bp、实际付 −59.6bp"，中间约 **20bp 无解释**，值约 0.7$/h。
本脚本把这 20bp 拆开：
  (1) `price_bp` 分布（p10/p25/p50/p75/p90）——少数跳空 vs 整体平移；
  (2) `spread_bp`（出场穿价差）与 `fee_bp`（−4 taker）各占多少；
  (3) 按"是否超过 −60bp"分档，看超额部分是集中在少数腿（⇒ 对策=限制单笔敞口/
      跳空保护）还是普遍存在（⇒ 对策=触发/执行机制本身）；
  (4) 与 `stop_maker_grace_sec` 的关系：宽限期本应先用 maker 出场，但 36/42 腿仍 taker。

用法：python scripts/h526_stop_overshoot.py [--hours 0]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h526_stop_overshoot.json"
ERA_DEFAULT = "2026-09-28 13:00"
STOP_BP = 40.0


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


def pct(v, q):
    if not v:
        return float("nan")
    v = sorted(v)
    i = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return v[i]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--since", default=ERA_DEFAULT)
    a = ap.parse_args()
    dsn = read_env_dsn()
    now = dt.datetime.now()
    if a.hours > 0:
        since, hours = now - dt.timedelta(hours=a.hours), a.hours
        label = f"近 {a.hours:g}h"
    else:
        since = dt.datetime.strptime(a.since, "%Y-%m-%d %H:%M")
        hours = (now - since).total_seconds() / 3600.0
        label = f"{a.since} 起（当前时代）"
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, net_bp, price_bp, fee_bp, spread_bp, notional,
                       COALESCE(meta_json->>'exit_action',''),
                       COALESCE(meta_json->>'exit_reason',''),
                       COALESCE(meta_json->>'exit_path',''),
                       ts
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= %s
                  AND COALESCE(meta_json->>'exit_path','') LIKE 'stop_loss%%'
                ORDER BY ts""", (LANE, since))
            rows = cur.fetchall()
            cur.execute("""
                SELECT symbol, count(*), min(net_bp), max(net_bp),
                       (sum(net_bp*notional)/NULLIF(sum(notional),0))::float8
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s
                  AND COALESCE(meta_json->>'exit_path','') LIKE 'stop_loss%%'
                GROUP BY symbol ORDER BY 2 DESC""", (LANE, since))
            bysym = cur.fetchall()
    if not rows:
        print("窗口内无止损腿")
        return 0
    n = len(rows)
    tot_usd = sum(r[1] * r[5] / 1e4 for r in rows)
    w = lambda idx: (sum(r[idx] * r[5] for r in rows) / sum(r[5] for r in rows))
    print(f"止损路径分解 · {label}（{hours:.2f}h）· {n} 腿 · 净 {tot_usd:+.3f}$ "
          f"（{tot_usd/hours:+.3f}$/h）")
    print("=" * 100)
    print(f"加权净 {w(1):+.3f}bp/腿 | 价格项 {w(2):+.3f} | 费用项 {w(3):+.3f} | "
          f"价差项 {w(4):+.3f}")
    pb = [r[2] for r in rows]
    print(f"\n价格项 price_bp 分布：p10 {pct(pb,0.10):+.1f} | p25 {pct(pb,0.25):+.1f} | "
          f"p50 {pct(pb,0.50):+.1f} | p75 {pct(pb,0.75):+.1f} | p90 {pct(pb,0.90):+.1f} | "
          f"均值 {st.mean(pb):+.1f}")
    print(f"触发阈值 stop_loss_bp = {STOP_BP:.0f}bp ⇒ 溢出 = |price_bp| − {STOP_BP:.0f}")
    print(f"  中位溢出 {abs(pct(pb,0.50))-STOP_BP:+.1f}bp | 均值溢出 "
          f"{abs(st.mean(pb))-STOP_BP:+.1f}bp")
    # 分档
    bins = [("−40 ~ −50", 40, 50), ("−50 ~ −60", 50, 60), ("−60 ~ −80", 60, 80),
            ("−80 ~ −120", 80, 120), ("≤ −120", 120, 1e9)]
    print(f"\n{'档位(bp)':<12s} {'腿数':>6s} {'占比':>7s} {'净$':>10s} {'净$占比':>8s} "
          f"{'名义$':>10s} {'该档中位净bp':>13s}")
    out_bins = []
    for nm, lo, hi in bins:
        g = [r for r in rows if lo <= abs(r[2]) < hi]
        if not g:
            print(f"{nm:<12s} {0:6d}")
            continue
        usd = sum(r[1] * r[5] / 1e4 for r in g)
        med = pct([r[1] for r in g], 0.5)
        print(f"{nm:<12s} {len(g):6d} {100.0*len(g)/n:6.1f}% {usd:+10.3f} "
              f"{100.0*usd/tot_usd:7.1f}% {sum(r[5] for r in g):10.1f} {med:+13.1f}")
        out_bins.append({"band": nm, "legs": len(g), "usd": round(usd, 3),
                         "median_net_bp": round(med, 2)})
    # 若把溢出压到 0（即每腿只亏 40bp+费用），能省多少
    ideal = sum((-STOP_BP + r[3]) * r[5] / 1e4 for r in rows)
    print(f"\n反事实：若每腿的**价格项恰好等于 −{STOP_BP:.0f}bp**（无溢出）、费用项不变，"
          f"则这 {n} 腿净额 = {ideal:+.3f}$（现状 {tot_usd:+.3f}$）⇒ "
          f"**最多可省 {ideal - tot_usd:+.3f}$ = {(ideal-tot_usd)/hours:+.3f}$/h**")
    print(f"反事实 B：若每腿只按 maker 计费（费用 0、价格项不变）⇒ "
          f"省 {sum(-r[3]*r[5]/1e4 for r in rows):+.3f}$ = "
          f"{sum(-r[3]*r[5]/1e4 for r in rows)/hours:+.3f}$/h")
    print("\n逐币止损")
    for s, cnt, mn, mx, wn in bysym:
        print(f"  {s:>6s} {cnt:4d} 腿 加权净 {wn:+9.2f}bp 范围 [{mn:+.1f}, {mx:+.1f}]")
    reasons = {}
    for r in rows:
        reasons[(r[6], r[7])] = reasons.get((r[6], r[7]), 0) + 1
    print("\nexit_action/exit_reason 分布（前 8）")
    for (act, rsn), cnt in sorted(reasons.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {act:<18s} {rsn:<24s} ×{cnt}")
    OUT.write_text(json.dumps({
        "label": label, "hours": round(hours, 3), "legs": n,
        "net_usd": round(tot_usd, 3), "weighted_net_bp": round(w(1), 3),
        "weighted_price_bp": round(w(2), 3), "weighted_fee_bp": round(w(3), 3),
        "price_pct": {"p10": round(pct(pb, 0.10), 2), "p50": round(pct(pb, 0.50), 2),
                      "p90": round(pct(pb, 0.90), 2)},
        "median_overshoot_bp": round(abs(pct(pb, 0.50)) - STOP_BP, 2),
        "bands": out_bins,
        "counterfactual_no_overshoot_usd": round(ideal, 3),
        "counterfactual_maker_fee_usd": round(sum(-r[3] * r[5] / 1e4 for r in rows), 3),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
