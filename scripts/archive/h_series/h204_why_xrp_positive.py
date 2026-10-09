# -*- coding: utf-8 -*-
"""H204 为什么 XRP 的 maker 腿是正的，其余三个是负的。

# 已确立的事实（当前时代，重置前）

    币      腿数    加权 spread_bp   加权 price_bp   加权 net_bp
    XRP    1152      +0.2319         **+0.2092**    **+0.4412**
    ASTER  1428      +0.2606          −0.2485        +0.0121
    HYPE   1788      +0.1898          −0.2554        −0.0656
    SOL    1360      +0.1721         **−0.4065**     −0.2343

**四个币的 spread 捕获几乎一样**（+0.17~+0.26bp）⇒ 差别**全在 `price_bp`**
（成交之后价格朝哪边走）。XRP 有利、SOL 不利。

# 本脚本要回答

`price_bp` 是**逆向选择**的直接度量：成交后价格继续朝不利方向走多少。
它是符号级的，所以要么与**该币的行情特征**有关，要么与**引擎在该币上的行为**有关。

测三组量（都取与账本同一时间窗）：

  ① **行情特征**：实现波动、盘口更新率、点差宽度、买卖压不均衡
  ② **引擎行为**：挂单宽度、成交笔均名义、成交频率
  ③ **净额与波动的关系**：是否"越波动越亏"（若成立，则 XRP 只是恰好更安静）

若 ③ 成立 ⇒ **不是 XRP 特别，是 SOL/HYPE/ASTER 在这段时间波动更大**。
那就是 regime 问题，不是币的问题 —— 修法完全不同。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
# ⚠️ 两张表的符号体系**不同**：
#   `lane_ledger.symbol`            → `XRPUSDT`（带 USDT）
#   `market_orderbook_snapshots`    → `XRP`    （**裸符号**，与 exchange 列配合）
# 首版我没注意，导致行情侧全部 `mkt=False`（查了个不存在的符号）。
MKT_SYMS = ["XRP", "ASTER", "HYPE", "SOL"]
T0 = "2026-09-21 18:00:34+08"
T1 = "2026-09-22 10:22:37+08"


def dsn(which: str = "alpha_arena") -> str:
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
    base, _, _ = url.rpartition("/")
    return f"{base}/{which}"


def main() -> int:
    import psycopg

    print("=" * 100)
    print("H204  XRP vs 其余三个：差别在行情还是引擎？")
    print("=" * 100)
    print(f"  窗口 {T0} → {T1}（与账本同窗，避免拿不同时段对比）")

    # ── ① 与 ③：行情特征（用引擎真正读的那张表）──
    with psycopg.connect(dsn("alpha_market")) as mc:
        with mc.cursor() as cur:
            cur.execute("SET statement_timeout = 180000")
            cur.execute("""
                WITH s AS (
                  SELECT symbol, timestamp/1000.0 AS t,
                         (best_bid+best_ask)/2.0 AS mid,
                         (best_ask-best_bid)/NULLIF((best_bid+best_ask)/2,0)*1e4 AS spr_bp,
                         best_bid, best_ask
                  FROM market_orderbook_snapshots
                  WHERE exchange='asterdex' AND symbol = ANY(%s)
                    AND timestamp BETWEEN (extract(epoch FROM %s::timestamptz)*1000)
                                     AND (extract(epoch FROM %s::timestamptz)*1000)
                    AND best_bid > 0 AND best_ask > best_bid
                ), d AS (
                  SELECT symbol, t, spr_bp,
                         abs(mid - lag(mid) OVER (PARTITION BY symbol ORDER BY t))
                           / NULLIF(lag(mid) OVER (PARTITION BY symbol ORDER BY t),0) * 1e4 AS mv
                  FROM s
                )
                SELECT symbol, count(*) n,
                       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY spr_bp)::numeric,4) spr_med,
                       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY mv)::numeric,3) mv_p90,
                       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY mv)::numeric,3) mv_p50,
                       round(avg(mv)::numeric,3) mv_mean
                FROM d WHERE mv IS NOT NULL GROUP BY symbol ORDER BY symbol
            """, (MKT_SYMS, T0, T1))
            mkt = {r[0]: r[1:] for r in cur.fetchall()}

    # ── ② 与 ③：账本侧（净额、成交频率）──
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, count(*) n, sum(notional) notl,
                       round((sum(net_bp*notional)/NULLIF(sum(notional),0))::numeric,4) wnet,
                       round((sum(price_bp*notional)/NULLIF(sum(notional),0))::numeric,4) wpx,
                       round((sum(spread_bp*notional)/NULLIF(sum(notional),0))::numeric,4) wsp,
                       round(avg(notional)::numeric,2) avg_notl
                FROM lane_ledger WHERE lane_id=%s
                  AND ts BETWEEN %s::timestamptz AND %s::timestamptz
                  AND meta_json->>'flatten' NOT IN ('true','True')
                GROUP BY symbol ORDER BY symbol
            """, (LANE, T0, T1))
            led = {str(r[0]).replace("USDT", ""): r[1:] for r in cur.fetchall()}

    print(f"\n  {'币':<8}{'腿数':>7}{'点差中位':>10}{'mv_p90':>9}{'mv_p50':>9}"
          f"{'成交/时':>9}{'笔均名义$':>11}{'加权price':>11}{'加权net':>10}")
    print("  " + "-" * 96)
    for s in MKT_SYMS:
        m = mkt.get(s)
        l = led.get(s)
        if not m or not l:
            print(f"  {s:<8}  缺数据  mkt={bool(m)} led={bool(l)}")
            continue
        n, spr_med, mv_p90, mv_p50, mv_mean = m
        ln, notl, wnet, wpx, wsp, avg_notl = l
        hours = 16.37
        print(f"  {s.replace('USDT',''):<8}{int(ln):>7}{float(spr_med):>10.4f}"
              f"{float(mv_p90):>9.3f}{float(mv_p50):>9.3f}"
              f"{int(ln)/hours:>9.1f}{float(avg_notl):>11.2f}"
              f"{float(wpx):>11.4f}{float(wnet):>10.4f}")

    # ── 相关性检验：price_bp 是否随波动变差 ──
    print("\n  ── 判据：加权 price_bp 与 mv_p90（成交后价格漂移的幅度）的关系 ──")
    pts = []
    for s in MKT_SYMS:
        m, l = mkt.get(s), led.get(s)
        if not m or not l:
            continue
        pts.append((s.replace("USDT", ""), float(m[2]), float(l[3])))
    pts.sort(key=lambda x: x[1])
    print(f"    {'币':<8}{'mv_p90':>9}{'加权price_bp':>14}")
    for name, v, p in pts:
        print(f"    {name:<8}{v:>9.3f}{p:>+14.4f}")
    if len(pts) >= 3:
        xs = [p[1] for p in pts]
        ys = [p[2] for p in pts]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        dx = sum((x - mx) ** 2 for x in xs) ** 0.5
        dy = sum((y - my) ** 2 for y in ys) ** 0.5
        r = num / (dx * dy) if dx > 0 and dy > 0 else 0.0
        print(f"\n    corr(mv_p90, 加权price_bp) = {r:+.3f}   （仅 {len(pts)} 个点，仅供方向参考）")
        if r < -0.5:
            print("    ⇒ 波动越大 price_bp 越负 ⇒ **regime 效应**，不是 XRP 特别")
            print("       修法应针对'高波动时段是否该少做'，而不是针对某个币")
        elif r > 0.5:
            print("    ⇒ 波动越大 price_bp 越好（反直觉）⇒ 需查其它解释")
        else:
            print("    ⇒ 关系不明显 ⇒ 更可能是**币的微观结构差异**（流动性/参与者）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
