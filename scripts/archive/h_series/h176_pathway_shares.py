# -*- coding: utf-8 -*-
"""[H176 2026-09-21] 撤掉出库挂单前的必要测量：三条路径各占多少。

# 用户选定方案 ①

「撤掉减仓侧出库单，让止盈（+12bp 付 4bp）成为主要出库路径」。
依据：止盈腿实测 **+7.44bp/笔** 对 出库腿 **+0.3bp/笔**（24 倍）。

# 但必须先回答：**那 65% 到不了 +12bp 的仓位怎么出？**

现状 `timeout_exit_maker_only=True` ⇒ **超时也不 taker** ⇒ 撤掉出库挂单后
这些仓位会**无限挂着**。所以必须先量清楚，在 300s 窗口内：

  A 止盈（有利 ≥ 12bp）      → 净 +8bp（付 taker）
  B 止损（不利 ≥ 40bp）      → 净 −44bp（付 taker）
  C 被动出库（触到出库单）    → 净 +挂宽（免费）   ← 撤掉后这条消失
  D 都没发生                 → 该仓位到 300s 时还在，**必须有个出口**

# 判据（事先定死）

  · 若 D 占比很高（> 25%）⇒ **不能完全撤掉出库单**，必须保留窄幅出库单作为"逃生口"
    （因为超时若不 taker，这些仓位会永远占着敞口；若超时 taker，则付 4bp）
  · 若 A 占比高且 D 占比低 ⇒ 可以放心撤掉出库单
  · 同时给出 D 类仓位在 300s 时刻的**浮动盈亏分布** ——
    若中位数接近 0，则"超时按浮动盈亏 taker 平掉"代价很小（只付 4bp）

用法：
    .venv\\Scripts\\python.exe scripts\\h176_pathway_shares.py
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn(db: str = "alpha_arena") -> str:
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
    return f"{url.rsplit('/', 1)[0]}/{db}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--horizon", type=float, default=300.0)
    ap.add_argument("--tp", type=float, default=12.0)
    ap.add_argument("--sl", type=float, default=40.0)
    a = ap.parse_args()

    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    lim = j.get("limits") or {}
    print("=" * 96)
    print("H176  三条路径的占比（撤出库单前的必要测量）")
    print("=" * 96)
    print(f"  止盈 tp={a.tp}bp（净 +{a.tp-4:.0f}）  止损 sl={a.sl}bp（净 −{a.sl+4:.0f}）")
    print(f"  当前：timeout_maker_only={lim.get('timeout_exit_maker_only')}  "
          f"spread_mult_reduce={(j.get('params') or {}).get('spread_mult_reduce')}")

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            since = (cur.fetchone() or [None])[0]
            cur.execute("""
                SELECT id, ts, symbol, meta_json->>'side'
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s
                  AND coalesce(meta_json->>'flatten','false')='false'
                ORDER BY id
            """, (LANE, since))
            entries = cur.fetchall()[-a.limit:]

    print(f"  入场样本 {len(entries)} 笔，取盘口 …\n")
    cA = cB = cD = 0
    d_fav = []          # D 类（都没发生）在 300s 时刻的浮动盈亏 bp
    t_tp = []           # 触及止盈的耗时
    t_sl = []           # 触及止损的耗时
    checked = 0
    for i, (rid, ts, sym, side) in enumerate(entries, 1):
        t0 = ts.timestamp()
        with psycopg.connect(dsn("alpha_market")) as mc:
            with mc.cursor() as cur:
                cur.execute("""
                    SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND ask_px > bid_px AND bid_px > 0
                    ORDER BY event_ts_ms
                """, (sym + "USDT", int(t0 * 1000), int((t0 + a.horizon) * 1000)))
                seq = [(int(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
        if len(seq) < 5:
            continue
        mid0 = (seq[0][1] + seq[0][2]) / 2.0
        if mid0 <= 0:
            continue
        checked += 1
        sgn = 1.0 if side == "buy" else -1.0
        last_fav = 0.0
        hit = None
        for (ms, b, aa) in seq:
            mid = (b + aa) / 2.0
            fav = sgn * (mid - mid0) / mid0 * 1e4
            last_fav = fav
            if fav <= -a.sl:
                hit = ("B", (ms - seq[0][0]) / 1000.0); break
            if fav >= a.tp:
                hit = ("A", (ms - seq[0][0]) / 1000.0); break
        if hit is None:
            cD += 1
            d_fav.append(last_fav)
        elif hit[0] == "A":
            cA += 1; t_tp.append(hit[1])
        else:
            cB += 1; t_sl.append(hit[1])
        if i % 50 == 0:
            print(f"    回放进度 {i}/{len(entries)} …")

    n = max(checked, 1)
    print(f"\n  ── 结果（n={checked}，窗口 {a.horizon:.0f}s）──")
    print(f"  {'路径':<34} {'笔数':>6} {'占比':>8} {'净bp/笔':>10} {'加权贡献':>10}")
    print("  " + "-" * 72)
    rows = [
        (f"A 止盈（有利 ≥ {a.tp:.0f}bp）", cA, a.tp - 4.0),
        (f"B 止损（不利 ≥ {a.sl:.0f}bp）", cB, -(a.sl + 4.0)),
        ("D 都没发生（300s 仍在）", cD, None),
    ]
    tot = 0.0
    for lab, c, bp in rows:
        share = c / n * 100
        if bp is None:
            print(f"  {lab:<34} {c:>6} {share:>7.1f}% {'—':>10} {'—':>10}")
        else:
            contrib = c / n * bp
            tot += contrib
            print(f"  {lab:<34} {c:>6} {share:>7.1f}% {bp:>+10.1f} {contrib:>+10.4f}")
    print(f"\n  A+B 的加权贡献合计 **{tot:+.4f} bp/笔**（D 未计，取决于超时怎么处理）")

    if t_tp:
        print(f"\n  止盈触及耗时：中位 {st.median(t_tp):.0f}s  最大 {max(t_tp):.0f}s")
    if t_sl:
        print(f"  止损触及耗时：中位 {st.median(t_sl):.0f}s")
    if d_fav:
        print(f"\n  ── D 类（{len(d_fav)} 笔）在窗口末的浮动盈亏 ──")
        print(f"    中位 {st.median(d_fav):+.2f}bp   均值 {st.mean(d_fav):+.2f}bp")
        print(f"    p25 {sorted(d_fav)[len(d_fav)//4]:+.2f}  "
              f"p75 {sorted(d_fav)[len(d_fav)*3//4]:+.2f}")
        print(f"    ⇒ D 类**不是「没动」**，而是「在 tp 和 sl 之间来回」；")
        print(f"      若超时 taker 平掉，代价 = 当时的浮动盈亏 − 4bp")

    print(f"\n  ── 判据核对 ──")
    print(f"    D 占比 **{cD/n*100:.1f}%**")
    if cD / n > 0.25:
        print(f"    ⇒ **不能完全撤掉出库单**：{cD/n*100:.0f}% 的仓位到 300s 仍在，")
        print(f"      若超时也不 taker 会**永远占着敞口**。")
        print(f"      建议：保留**窄幅出库单**作为逃生口（挂宽取小额，赚 0.2bp），")
        print(f"      同时让止盈覆盖大额移动 —— 但两者会竞争（H172 已证）。")
        print(f"      **替代方案：超时改回 taker**（付 4bp），把 D 类清掉。")
    else:
        print(f"    ⇒ D 占比低，撤掉出库单后敞口压力可控。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
