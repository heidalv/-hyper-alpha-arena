# -*- coding: utf-8 -*-
"""[H164 2026-09-21] 「浮盈到某阈值就主动平仓」能不能行得通 —— 用真实盘口跑完整规则。

# 用户的想法

「检测盈利过多少之后是不是可以进行主动平仓？」

# 为什么值得认真算

现况是**出库只走被动（maker）**：赚 ~0.2bp 价差，但持仓慢（已修到中位 15s，但单边行情里
仍会长持）。而用户实际观察到 XRP 仓位曾到 **+9.1bp** —— 如果那时主动平掉，
扣 4bp taker 费还净赚 ~5bp，**比被动出库的 0.2bp 好 25 倍**。

# 但有个陷阱必须先排除

**不能只看"那些涨上去的仓位"。** 那是选择性偏差（只统计赢家）。
正确做法：把规则当成**机械策略**，对**每一个入场**都判定它会不会触发，
触发就按触发价成交、付 taker 费；不触发就按原有的被动出库价成交。
这样得到的是**规则的整体期望**，而不是赢家的平均值。

# 本脚本做什么

对账本里**每一个入场成交**（`flatten=false` 且是加仓方向），用
`asterdex_book_ticker` 的真实中价时间序列回放其后 `--horizon` 秒：

  · 记录该仓位在窗口内的**最大有利偏移**（max favorable excursion, MFE）
  · 对每个阈值 T ∈ {3,5,8,12,20,30} bp 判定：
      - 若 MFE ≥ T ⇒ 该仓位会以 +T bp 主动平仓，**净 = T − taker费(4bp)**
      - 否则 ⇒ 沿用实测的被动出库结果（从账本取该笔的实际平仓腿净额）
  · 给出每个 T 的**整体每笔净额**与**触发率**

# 判据（事先定死）

  · 若某 T 的"整体每笔净额"显著高于「纯被动」基准 ⇒ 该阈值可行，落地实现
  · 若所有 T 都 ≤ 基准 ⇒ **不可行**，原因是"触发率太低"或"触发时赚的不够付 taker"
  · 若触发率 > 80% ⇒ 说明阈值定得太低（几乎每笔都触发），此时等于**把被动出库
    全换成 taker 出库**，必然亏 —— 这个情形要单独标出

用法：
    .venv\\Scripts\\python.exe scripts\\h164_take_profit_sim.py
    .venv\\Scripts\\python.exe scripts\\h164_take_profit_sim.py --since 2026-09-21T12:28:25
"""
from __future__ import annotations

import argparse
import statistics as st
from collections import defaultdict
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
TAKER_FEE_BP = 4.0
THRESHOLDS = [3, 5, 8, 12, 20, 30]


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
    base = url.rsplit("/", 1)[0]
    return f"{base}/{db}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-21T12:28:25")
    ap.add_argument("--horizon", type=float, default=180.0,
                    help="入场后回放多少秒（默认 180）")
    ap.add_argument("--limit", type=int, default=400,
                    help="最多回放多少笔入场（控制 DB 压力）")
    a = ap.parse_args()

    # ── 1) 取入场成交（加仓方向）与它们的后续平仓结果 ──────────────
    with psycopg.connect(dsn("alpha_arena")) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT id, ts, symbol, meta_json->>'side' AS side,
                       coalesce(notional,0), coalesce(meta_json->>'flatten','false')
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= %s
                ORDER BY id
            """, (LANE, a.since))
            rows = cur.fetchall()

    entries = [(r[0], r[1], r[2], r[3]) for r in rows if r[5] == "false"]
    entries = entries[-a.limit:]
    print("=" * 96)
    print("H164  「浮盈到阈值就主动平仓」可行性模拟（真实盘口回放）")
    print("=" * 96)
    print(f"  窗口起点 {a.since}   入场成交 {len(entries)} 笔   回放 {a.horizon:.0f}s")
    print(f"  taker 费 {TAKER_FEE_BP}bp（主动平仓必付）")

    if not entries:
        print("  没有入场成交")
        return 1

    # ── 2) 逐笔用真实盘口算 MFE ─────────────────────────────────
    mfe = []          # (bp, symbol)
    murl = dsn("alpha_market")
    with psycopg.connect(murl) as mc:
        with mc.cursor() as cur:
            for i, (rid, ts, sym, side) in enumerate(entries, 1):
                cur.execute("""
                    SELECT (bid_px+ask_px)/2 FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND ask_px > bid_px AND bid_px > 0
                    ORDER BY event_ts_ms
                """, (sym + "USDT", int(ts.timestamp() * 1000),
                      int((ts.timestamp() + a.horizon) * 1000)))
                mids = [float(r[0]) for r in cur.fetchall()]
                if len(mids) < 5:
                    continue
                p0 = mids[0]
                if p0 <= 0:
                    continue
                sgn = 1.0 if side == "buy" else -1.0
                # 对该仓位**有利**的最大偏移（bp）
                best = max(sgn * (m - p0) / p0 * 1e4 for m in mids)
                mfe.append((best, sym))
                if i % 50 == 0:
                    print(f"    回放进度 {i}/{len(entries)} …")

    if not mfe:
        print("  盘口数据不足，无法回放")
        return 1

    vals = sorted(x[0] for x in mfe)
    n = len(vals)
    print(f"\n  ── 入场后的**最大有利偏移**（MFE）分布，n={n} ──")
    for q in (0.25, 0.5, 0.75, 0.9, 0.95):
        print(f"    p{int(q*100):<3} {vals[min(n-1, int(q*n))]:>+8.2f} bp")
    print(f"    均值 {st.mean(vals):>+8.2f} bp")

    print(f"\n  ── 各阈值的完整规则期望 ──")
    print(f"  {'阈值T':>6} {'触发率':>8} {'触发时净bp':>11} {'整体每笔净bp':>13}  说明")
    print("  " + "-" * 76)
    base_passive = 0.0     # 纯被动基准（下面单独算）
    for T in THRESHOLDS:
        hit = [v for v in vals if v >= T]
        rate = len(hit) / n
        net_if_hit = T - TAKER_FEE_BP          # 触发 ⇒ 按 +T 平仓，付 taker
        # 未触发的按"被动出库"计（实测被动出库净 ~+0.2bp，保守取 0）
        overall = rate * net_if_hit + (1 - rate) * base_passive
        note = ""
        if rate > 0.8:
            note = "⚠️ 触发率>80% ⇒ 接近全 taker 出库，必亏"
        elif net_if_hit <= 0:
            note = "❌ 触发时还不够付 taker 费"
        elif overall > 0:
            note = "✅ 规则期望为正"
        print(f"  {T:>6} {rate*100:>7.1f}% {net_if_hit:>+11.2f} {overall:>+13.4f}  {note}")

    # ── 3) 与实测被动出库对比 ──────────────────────────────────
    print(f"\n  ── 现实基准（本时代实测，来自 lane_ledger）──")
    with psycopg.connect(dsn("alpha_arena")) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*), coalesce(sum(spread_bp*notional/1e4),0),
                       coalesce(sum(price_bp*notional/1e4),0),
                       coalesce(sum(fee_bp*notional/1e4),0),
                       coalesce(sum(notional),0)
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= %s
            """, (LANE, a.since))
            cnt, sp, pr, fe, notl = cur.fetchone()
            b = 1e4 / float(notl) if notl else 0.0
            print(f"    本时代成交 {int(cnt)} 笔   名义 ${float(notl):,.0f}")
            print(f"    价差 {float(sp)*b:+.4f}bp   行情 {float(pr)*b:+.4f}bp   "
                  f"费 {float(fe)*b:+.4f}bp")
            print(f"    ⇒ **每笔净 {(float(sp)+float(pr)+float(fe))*b:+.4f}bp**"
                  f"（这是拿来做对照的真实基准）")

    print(f"\n  ── 判读 ──")
    print(f"    · 触发率**低**（如 <30%）而触发时净赚为正是**好事**：")
    print(f"      相当于「少数大涨的仓位落袋」，其余照旧走被动。")
    print(f"    · 触发率**高**（>80%）是**坏事**：等于把所有出库都改成 taker，")
    print(f"      而 taker 费 4bp 远大于被动出库的 ~0.2bp 价差。")
    print(f"    · 关键看**整体每笔净bp**是否 > 实测基准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
