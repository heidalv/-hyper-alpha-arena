# -*- coding: utf-8 -*-
"""[H141 2026-09-21] 监看「超时只挂单不 taker」的实测效果与代价。

# 这条改动的判据（事先定死，避免事后挑数）

**收益侧**
  G1 `flatten` 笔数占比下降 —— 出库从 taker 转被动
  G2 `skip_counts` 出现 `timeout_maker_only` —— 证明超时路径被挡下（改动真的在跑）
  G3 强平腿累计 taker 费增速下降（对比窗口：$/小时）

**代价侧（必须盯，否则是拿风险换成本）**
  C1 `skip_counts.symbol_exposure` / `net_exposure` 是否上行 —— 敞口被占满、新仓被堵
  C2 是否有币的 `qty` **长期不归零**（卡住不回摆）
  C3 单笔 `price_bp` 的尾部是否变差 —— 持仓变长意味着吃更多行情风险

# 口径

用 `lane_ledger`（顶层列 `spread_bp/price_bp/fee_bp/notional`）+ 心跳。
窗口从 worker 重启（改动生效）时刻起算。

用法：
    .venv\\Scripts\\python.exe scripts\\h141_watch_maker_exit.py
    .venv\\Scripts\\python.exe scripts\\h141_watch_maker_exit.py --since 2026-09-21T10:46:00
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-21T10:46:00")
    a = ap.parse_args()

    j = json.loads(Path("logs/mm_lane_status.json").read_text(encoding="utf-8"))
    l = j.get("limits") or {}

    print("=" * 96)
    print("H141  监看「超时只挂单不 taker」")
    print("=" * 96)
    print(f"  现在 {datetime.now().strftime('%H:%M:%S')}   切换点 {a.since}")
    print(f"\n  【开关核对】timeout_exit_maker_only = **{l.get('timeout_exit_maker_only')}**"
          f"   stop_loss_bp = {l.get('stop_loss_bp')} (vol_min={l.get('stop_loss_vol_min')})")
    print(f"  宇宙 {j.get('symbols')}")
    print(f"  ticks {j.get('ticks')}  fills {j.get('fills')}  flattens {j.get('flattens')}  "
          f"equity {j.get('equity'):.4f}")

    sk = j.get("skip_counts") or {}
    print(f"\n  【G2/C1】skip_counts：{sk}")
    print(f"  【C2】timeout_exit_blocked（超时被挡下的 taker 次数）："
          f"{j.get('timeout_exit_blocked')}")
    states = j.get("states") or {}
    pos = {k: round(float(v.get("qty") or 0.0), 4) for k, v in states.items()
           if abs(float(v.get("qty") or 0.0)) > 1e-9}
    print(f"  【C2】当前持仓：{pos or '(全平)'}")

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT coalesce(meta_json->>'flatten','false') AS flat,
                       count(*), coalesce(sum(notional),0),
                       coalesce(sum(fee_bp*notional/1e4),0),
                       coalesce(sum(price_bp*notional/1e4),0),
                       coalesce(sum(spread_bp*notional/1e4),0)
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= %s
                GROUP BY 1
            """, (LANE, a.since))
            rows = cur.fetchall()

            cur.execute("""SELECT count(*) FROM lane_ledger
                           WHERE lane_id=%s AND event='fill' AND ts >= %s""",
                        (LANE, a.since))
            tot = cur.fetchone()[0] or 0

            cur.execute("""SELECT price_bp FROM lane_ledger
                           WHERE lane_id=%s AND event='fill' AND ts >= %s
                             AND coalesce(meta_json->>'flatten','false')='true'
                             AND price_bp IS NOT NULL""", (LANE, a.since))
            pb = sorted(float(r[0]) for r in cur.fetchall())

    print(f"\n  【G1/G3】切换点后成交明细（共 {tot} 笔）")
    print(f"  {'腿':<14} {'笔数':>6} {'占比':>7} {'名义$':>12} {'费$':>10} "
          f"{'行情$':>10} {'价差$':>10}")
    print("  " + "-" * 74)
    for (flat, n, notl, fee, price, spread) in rows:
        lab = "强平腿(taker)" if flat == "true" else "入场腿(maker)"
        print(f"  {lab:<14} {n:>6} {n/max(tot,1)*100:>6.1f}% {float(notl):>12,.0f} "
              f"{float(fee):>10.3f} {float(price):>10.3f} {float(spread):>10.3f}")

    n_flat = next((int(r[1]) for r in rows if r[0] == "true"), 0)
    print(f"\n  强平率（按笔数）**{n_flat/max(tot,1)*100:.2f}%**"
          f"   （改动前全夜 15.5%、ASTER 6.6%）")
    if pb:
        print(f"  【C3】强平腿 price_bp：n={len(pb)}  中位 {st.median(pb):+.2f}bp  "
              f"最小 {min(pb):+.2f}bp")
        for thr in (-40, -60, -100):
            print(f"        ≤ {thr}bp 占 {sum(1 for x in pb if x <= thr)/len(pb)*100:.1f}%")
    if tot < 40:
        print(f"\n  ⚠️ 样本 {tot} 笔偏少 —— 建议等 ≥100 笔再判。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
