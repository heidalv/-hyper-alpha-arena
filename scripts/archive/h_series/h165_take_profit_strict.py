# -*- coding: utf-8 -*-
"""[H165 2026-09-21] 止盈规则的**成交可行性**验证 —— 排除 H164 的乐观假设。

# H164 的结果与它的缺陷

H164 用"入场后窗口内的**最高中价**"作为止盈成交价，得到：

    阈值 T   触发率    整体每笔净bp
      8     46.3%      +1.853
     12     31.3%      **+2.507**
     20     12.0%      +1.920
    （现实基准 +0.1374 bp/笔）

**缺陷**：`最高中价 ≥ T` 只说明"价格**到过** T"，不说明
  · 我们挂在 T 的**限价单会被成交**（需要对手方主动打到我们的价）
  · 成交价就是 T（可能滑点/只成交一部分）
  · 从触发到成交之间价格没有回撤（回撤了我们就被落下）

⇒ 本脚本改用**更严格、也更接近引擎实际判定**的口径：

    在窗口内按时间顺序扫描盘口，若出现 **ask ≤ T价**（买入平空）
    或 **bid ≥ T价**（卖出平多），则视为该价位被触达 ⇒ 以 T价成交。
    这正是引擎判定的"对手方打到我们的挂单价"语义
    （`plan_symbol` 的 `hit_buy`/`hit_sell` 用 `seg_low < quote*(1-pen)`）。

# 判据（事先定死）

  · 若严格口径下某 T 的**整体每笔净bp** 仍显著高于现实基准 ⇒ 值得实现
  · 若严格口径下**触发率大幅低于** H164 ⇒ H164 的高估主要来自"价格擦到但没被吃"
  · 同时给出"从触达到成交的耗时中位"，用于评估实现复杂度

用法：
    .venv\\Scripts\\python.exe scripts\\h165_take_profit_strict.py
"""
from __future__ import annotations

import argparse
import statistics as st
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
TAKER_FEE_BP = 4.0
THRESHOLDS = [5, 8, 12, 20, 30]
BASELINE_BP = 0.1374      # 本时代实测每笔净额（H164 给出）


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
    ap.add_argument("--since", default="2026-09-21T12:28:25")
    ap.add_argument("--horizon", type=float, default=180.0)
    ap.add_argument("--limit", type=int, default=300)
    a = ap.parse_args()

    with psycopg.connect(dsn("alpha_arena")) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT id, ts, symbol, meta_json->>'side', coalesce(notional,0),
                       coalesce(meta_json->>'flatten','false')
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= %s
                ORDER BY id
            """, (LANE, a.since))
            rows = cur.fetchall()
    entries = [(r[0], r[1], r[2], r[3]) for r in rows if r[5] == "false"][-a.limit:]

    print("=" * 96)
    print("H165  止盈规则 · 严格成交口径验证")
    print("=" * 96)
    print(f"  入场 {len(entries)} 笔   回放 {a.horizon:.0f}s   taker {TAKER_FEE_BP}bp")
    print(f"  判定：价格必须**被对手方打到**才算成交（引擎同口径）")

    # 对每个阈值统计：是否成交、成交耗时
    hit = {T: [] for T in THRESHOLDS}      # 成交耗时（秒）
    for i, (rid, ts, sym, side) in enumerate(entries, 1):
        t0ms = int(ts.timestamp() * 1000)
        with psycopg.connect(dsn("alpha_market")) as mc:
            with mc.cursor() as cur:
                cur.execute("""
                    SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND ask_px > bid_px AND bid_px > 0
                    ORDER BY event_ts_ms
                """, (sym + "USDT", t0ms, int((ts.timestamp() + a.horizon) * 1000)))
                seq = [(int(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
        if len(seq) < 5:
            continue
        _, b0, ask0 = seq[0]
        mid0 = (b0 + ask0) / 2.0
        if mid0 <= 0:
            continue
        sgn = 1.0 if side == "buy" else -1.0
        # 目标价（我们要成交的价）
        for T in THRESHOLDS:
            tgt = mid0 * (1.0 + sgn * T / 1e4)
            done = None
            for (ms, bb, aa) in seq:
                # 平仓方向：多头 ⇒ 卖出 ⇒ 需要 bid ≥ 目标价；空头 ⇒ 买入 ⇒ ask ≤ 目标价
                ok = (bb >= tgt) if sgn > 0 else (aa <= tgt)
                if ok:
                    done = (ms - seq[0][0]) / 1000.0
                    break
            if done is not None:
                hit[T].append(done)
        if i % 60 == 0:
            print(f"    进度 {i}/{len(entries)} …")

    n = len(entries)
    print(f"\n  ── 严格口径下的触发率与规则期望 ──")
    print(f"  {'阈值T':>6} {'触发率':>8} {'成交耗时中位':>13} {'整体每笔净bp':>13} "
          f"{'对比基准':>10}")
    print("  " + "-" * 66)
    best = (None, -1e9)
    for T in THRESHOLDS:
        hs = hit[T]
        rate = len(hs) / n if n else 0.0
        med = st.median(hs) if hs else float("nan")
        net_hit = T - TAKER_FEE_BP
        overall = rate * net_hit        # 未触发按被动出库的额外收益≈0（保守）
        mult = overall / BASELINE_BP if BASELINE_BP else 0.0
        if overall > best[1]:
            best = (T, overall)
        print(f"  {T:>6} {rate*100:>7.1f}% {med:>12.1f}s {overall:>+13.4f} "
              f"{mult:>9.1f}×")
    print(f"\n  现实基准（本时代实测每笔净额）= {BASELINE_BP:+.4f} bp")

    print(f"\n  ── 判读 ──")
    print(f"    · 严格口径触发率**接近** H164 的乐观口径 ⇒ 价格一旦到过就能成交，")
    print(f"      说明挂单在这类币上**不愁没对手方** ⇒ 规则可落地。")
    print(f"    · 严格口径触发率**远低于** H164 ⇒ 高估主要来自「擦到但没被吃」，")
    print(f"      实现时要考虑排队位置与最小成交额。")
    if best[0]:
        print(f"\n  ⇒ 最佳阈值 **T={best[0]}bp**，规则期望 **{best[1]:+.4f} bp/笔**"
              f"（基准 {BASELINE_BP:+.4f}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
