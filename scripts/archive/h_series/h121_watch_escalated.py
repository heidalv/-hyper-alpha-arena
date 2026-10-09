# -*- coding: utf-8 -*-
"""[H121 2026-09-21] 监看：新档位（H120 升级）之后的实测盈亏与强平率。

# 为什么要单独算"切换点之后"

整夜账（−$52.20）是旧档位（单腿 $125、持有上限 120s、最少持有 30s）的结果，
用它评价新档位（单腿 $754、持有上限 300s、最少持有 0s）是**张冠李戴**。
所以按切换点切片，只看之后的账。

# 口径（与 H105 一致，便于对照）

  · **入场腿** = `flatten=false` 的行：maker 价差收益（`spread_bp` × notional）
  · **强平腿** = `flatten=true` 的行：taker 成本
  · 两本账都要算：`lane_ledger`（六维归因）+ `arbitrary_paper_ledger` 总账
  · 强平率用 **H84 周期口径**（从持仓曲线推导周期，不是用 fill 行数）

# 判据（事先定死）

  · 单腿放大约 6× ⇒ **每周期绝对金额也应放大约 6×**（百分点不变）
  · 若每周期 bp 口径**同时改善** ⇒ 说明 300s 持有窗口真的让更多周期走被动出库
  · 若每周期 bp 口径**变差** ⇒ 加杠杆只是放大了同一个负边际（并加速亏损）

用法：
    .venv\\Scripts\\python.exe scripts\\h121_watch_escalated.py
    .venv\\Scripts\\python.exe scripts\\h121_watch_escalated.py --since 2026-09-21T09:42:00
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from h84_derive_episodes import derive, load  # noqa: E402

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


def ledger_since(conn, since_iso: str):
    """取切换点之后的账本聚合（六维口径）。"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT (meta_json->>'flatten')::boolean AS flat,
                   count(*)                          AS n,
                   sum(notional)                     AS notional,
                   sum(spread_bp  * notional / 1e4)  AS spread_usd,
                   sum(price_bp   * notional / 1e4)  AS price_usd,
                   sum(fee_bp     * notional / 1e4)  AS fee_usd,
                   sum(net_bp     * notional / 1e4)  AS net_usd
            FROM lane_ledger
            WHERE lane_id=%s AND event='fill' AND ts >= %s
            GROUP BY 1 ORDER BY 1
        """, (LANE, since_iso))
        return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-21T09:42:00",
                    help="新档位生效时刻（本机时区）")
    a = ap.parse_args()

    print("=" * 100)
    print("H121  新档位实测监看")
    print("=" * 100)
    print(f"  切换点（本机）: {a.since}")
    print(f"  现在        : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── 账本聚合 ─────────────────────────────────────────────
    with psycopg.connect(dsn()) as conn:
        rows = ledger_since(conn, a.since)
    if not rows:
        print("\n  ⚠️ 切换点之后还没有账本成交。等几分钟再跑。")
        return 0

    print(f"\n{'─'*100}\n账本聚合（六维口径，USD）\n{'─'*100}")
    print(f"  {'腿':<12} {'笔数':>6} {'名义USD':>12} {'价差':>10} {'行情':>10} "
          f"{'费用':>10} {'净额':>10}")
    print("  " + "-" * 76)
    tot_net = 0.0
    entry = flat = None
    for (is_flat, n, notl, spread, price, fee, net) in rows:
        lab = "强平腿(taker)" if is_flat else "入场腿(maker)"
        vals = [float(x or 0.0) for x in (notl, spread, price, fee, net)]
        print(f"  {lab:<12} {n:>6} {vals[0]:>12,.0f} {vals[1]:>10.2f} {vals[2]:>10.2f} "
              f"{vals[3]:>10.2f} {vals[4]:>10.2f}")
        tot_net += vals[4]
        if is_flat:
            flat = (n, vals)
        else:
            entry = (n, vals)
    print(f"\n  ⇒ 切换点后累计净额 **{tot_net:+.4f} USD**")

    if entry:
        n, v = entry
        print(f"\n  入场腿：{n} 笔，净 {v[4]:+.4f} USD  "
              f"= **{v[4]/max(v[0],1e-9)*1e4:+.4f} bp/笔**（旧档位 −0.002 bp/行）")
    if flat:
        n, v = flat
        print(f"  强平腿：{n} 笔，净 {v[4]:+.4f} USD  "
              f"= **{v[4]/max(v[0],1e-9):+.5f} USD/笔**（旧档位 −0.134 USD/次）")

    # ── 周期口径 ────────────────────────────────────────────
    print(f"\n{'─'*100}\n周期口径（H84：从持仓曲线推导）\n{'─'*100}")
    rows_fb = load()
    fb = [r for r in rows_fb if (r.get("iso") or "") >= a.since]
    print(f"  切换点后 fill_basis 行数 {len(fb)}")
    if len(fb) >= 5:
        eps = [e for e in derive(rows_fb) if (e.get("t0") or "") >= a.since]
        nf = sum(1 for e in eps if e["flat"])
        if eps:
            print(f"  周期数 **{len(eps)}**   含强平腿 **{nf}**   "
                  f"强平率 **{nf/len(eps)*100:.1f}%**（旧档位全夜 16.0%）")
            import statistics as st
            d = [e["dur_s"] for e in eps]
            nt = [e["notional"] for e in eps]
            print(f"  周期时长中位 {st.median(d):.0f}s   峰值名义中位 "
                  f"${st.median(nt):,.2f}（旧 $102）")
    else:
        print("  ⚠️ 周期样本 <5，不下结论。")

    # ── 分币 ───────────────────────────────────────────────
    if len(fb) >= 5:
        print(f"\n{'─'*100}\n分币（切换点后）\n{'─'*100}")
        per = defaultdict(lambda: {"n": 0, "flat": 0, "notl": 0.0})
        for r in fb:
            s = r.get("symbol")
            q = float(r.get("qty") or 0.0)
            px = float(r.get("fill_px") or 0.0)
            per[s]["n"] += 1
            per[s]["notl"] += q * px
            if r.get("flatten"):
                per[s]["flat"] += 1
        print(f"  {'币':<10} {'笔':>5} {'强平笔':>7} {'名义USD':>12}")
        print("  " + "-" * 38)
        for s in sorted(per):
            v = per[s]
            print(f"  {s:<10} {v['n']:>5} {v['flat']:>7} {v['notl']:>12,.0f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
