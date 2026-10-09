# -*- coding: utf-8 -*-
"""[H172 2026-09-21] 出库挂宽 vs 止盈：找到两者协同的正确值。

# 发现的矛盾（H171）

    理论命中（MFE ≥ 12bp）      87 笔（34.8%）
    实际付 taker 的出库腿        11 笔
    ⇒ 漏抓 76 笔，而 30s 宽限期**没有**吃掉利润（触及后中位还涨 +4.92bp）

**真正原因**：减仓侧挂宽 `spread_mult_reduce = 0.4` ⇒ 出库单挂在 `mid + 0.2×价差`，
**持仓一有微利就被自己的挂单卖掉** ⇒ 仓位在 +0.2bp 就结束，永远到不了 +12bp 止盈线。

  · 出库**太紧** ⇒ 赢家被砍在 +0.2bp，止盈形同虚设
  · 出库**太松**（原 0.95）⇒ 排在盘口之外，根本不被吃（H163 已证）

⇒ 正确值应该是"**只在有意义的有利移动之后才成交**"：既不能贴中价，也不能出盘口。

# 本脚本量什么

用真实盘口回放每个入场，对每个候选 `spread_mult_reduce`（即出库单距中价的位置 r×半价差）
计算两种出库路径谁先发生、各自赚多少：

  路径 A（被动出库）：价格触到我们的出库单 ⇒ 以该价成交，赚 `r × 半价差`
     判定：与引擎同语义 —— 多头需 `bid ≥ 出单价`，空头需 `ask ≤ 出单价`
  路径 B（止盈）：有利偏移 ≥ 12bp ⇒ 以 +12bp 成交，付 4bp taker ⇒ 净 +8bp

  取**先发生**的那条；都没发生则记为"未出库"（留给止损/超时，不计收益）

# 判据（事先定死）

  · 对每个 r 算**整体每笔净 bp**（= 两条路径的期望），取最大者
  · 同时看"止盈占比"——它是这个协同的关键指标：
    r 太大 ⇒ 几乎全靠止盈（= 出库全变 taker，付 4bp）
    r 太小 ⇒ 几乎全靠被动（= 赚 0.2bp，止盈没机会）

用法：
    .venv\\Scripts\\python.exe scripts\\h172_exit_width_vs_tp.py
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
TAKER_BP = 4.0


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
    ap.add_argument("--horizon", type=float, default=180.0)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--ladder", default="0.3,0.4,0.6,0.8,1.0,1.5,2.0")
    a = ap.parse_args()

    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    lim = j.get("limits") or {}
    tp = float(lim.get("take_profit_bp") or 12.0)
    ladder = [float(x) for x in a.ladder.split(",")]

    print("=" * 96)
    print("H172  出库挂宽 × 止盈：协同最优值")
    print("=" * 96)
    print(f"  止盈 tp={tp}bp（净 +{tp-TAKER_BP}bp）   taker {TAKER_BP}bp")
    print(f"  候选出库挂宽 r ∈ {ladder}")

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

    print(f"  回放入场 {len(entries)} 笔\n")

    # 逐笔：算"出库单被触达的最早偏离"与"首次触及 tp 的偏离"
    per = []      # (symbol, 出库单被触达时的有利偏离bp 或 None, 触及tp的偏离 或 None)
    for i, (rid, ts, sym, side) in enumerate(entries, 1):
        t0 = ts.timestamp()
        with psycopg.connect(dsn("alpha_market")) as mc:
            with mc.cursor() as cur:
                cur.execute("""
                    SELECT bid_px, ask_px FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND ask_px > bid_px AND bid_px > 0
                    ORDER BY event_ts_ms
                """, (sym + "USDT", int(t0 * 1000), int((t0 + a.horizon) * 1000)))
                seq = [(float(r[0]), float(r[1])) for r in cur.fetchall()]
        if len(seq) < 5:
            continue
        mid0 = (seq[0][0] + seq[0][1]) / 2.0
        if mid0 <= 0:
            continue
        hs = (seq[0][1] - seq[0][0]) / 2.0      # 半价差（绝对值）
        sgn = 1.0 if side == "buy" else -1.0
        # 每个快照的"有利偏移（bp）"
        fav = [sgn * (((b + aa) / 2.0) - mid0) / mid0 * 1e4 for (b, aa) in seq]
        # 触及 tp 的最早有利偏移
        tp_at = None
        for v in fav:
            if v >= tp:
                tp_at = v
                break
        per.append((sym, mid0, hs, fav, tp_at))
        if i % 50 == 0:
            print(f"    回放进度 {i}/{len(entries)} …")

    print(f"\n  ── 各 r 的规则期望（n={len(per)}）──")
    print(f"  {'r':>5} {'出库赚bp':>9} {'被动占比':>9} {'止盈占比':>9} {'未出库':>8} "
          f"{'整体每笔净bp':>13}")
    print("  " + "-" * 62)
    best = (None, -1e9)
    for r in ladder:
        passive_gain = r * 0.5      # r × 半价差 = r×0.5 × 全价差；以"价差倍数"计
        n_pas = n_tp = n_none = 0
        tot = 0.0
        for (sym, mid0, hs, fav, tp_at) in per:
            # 出库单距中价的有利偏离（bp）= r × 半价差 / mid × 1e4
            exit_off = (r * hs) / mid0 * 1e4
            # 路径 A：价格先触到出库单
            hit_a = next((v for v in fav if v >= exit_off), None)
            # 路径 B：止盈
            hit_b = tp_at
            if hit_a is not None and (hit_b is None or hit_a <= hit_b):
                n_pas += 1
                tot += exit_off                 # 被动出库：赚到出库单的偏移（maker 免费）
            elif hit_b is not None:
                n_tp += 1
                tot += tp - TAKER_BP            # 止盈：付 taker
            else:
                n_none += 1
        n = max(len(per), 1)
        overall = tot / n
        if overall > best[1]:
            best = (r, overall)
        print(f"  {r:>5} {r*0.5:>9.3f} {n_pas/n*100:>8.1f}% {n_tp/n*100:>8.1f}% "
              f"{n_none/n*100:>7.1f}% {overall:>+13.4f}")

    print(f"\n  现实基准（本时代实测每笔净额）= +0.1374 bp")
    print(f"\n  ⇒ 最优 **r = {best[0]}**，规则期望 **{best[1]:+.4f} bp/笔**"
          f"（基准的 {best[1]/0.1374:.1f}×）")
    print(f"\n  ── 读法 ──")
    print(f"    · r 小 ⇒ 被动占比高但**每笔只赚 r×0.5bp**，止盈没机会")
    print(f"    · r 大 ⇒ 止盈占比高但**每笔付 4bp taker**，被动路失效")
    print(f"    · 最优在中间：让**小额有利移动走被动（免费）**、")
    print(f"      **大额有利移动走止盈（付 4bp 但吃 +8bp）**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
