# -*- coding: utf-8 -*-
"""[2026-09-24 第3轮] 中线：**入场质量 vs 止损距离** 分离验证（只用 09-15 后样本）。

背景（第3轮待解问题）：09-15 后中线 n=116 笔里 **79% 的单峰值连 +2.5% 都到不了**
（116 笔里 0 笔到过 +8%）⇒ 出场结构对这批单无能为力：无论怎么调追踪/分档，它们都是亏或平。
本脚本回答两件事：
  A. 亏损到底来自"从没动过"的单，还是"动过又还回去"的单（峰值分桶 × 实现盈亏）；
  B. 有没有**入场期可观测**的筛子能把"从没动过"的单挡在门外（入场时 4h 趋势位置 /
     模型方向 / 时段 / 币种），并做**前后半双段**验证（防单段过拟合）。

只读；不改任何参数。
"""
from __future__ import annotations

import datetime as dt
import io
import statistics as st
import sys

import psycopg

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
ANALYTICS = "postgresql://laobao:alpha_pass@localhost:5432/alpha_analytics"
CST = dt.timezone(dt.timedelta(hours=8))


def load():
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, side, leverage, entry_price, size, opened_at, closed_at,
                      coalesce(unrealized_pnl,0) pnl, peak_pnl_pct, close_reason
               from paper_positions
               where account_id=14 and timeframe_tier='mid' and status in ('closed','liquidated')
                 and closed_at > timestamp '2026-09-15 00:00:00'
               order by opened_at""")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def enrich(trades):
    # 入场时 4h 趋势位置（EMA21 距离 %、方向标签）
    syms = {}
    for t in trades:
        syms.setdefault(t["symbol"], []).append(t)
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for sym, ts in syms.items():
            o_min = min(int(x["opened_at"].replace(tzinfo=CST).timestamp()) for x in ts)
            cur.execute(
                """select timestamp, close_price from crypto_klines
                   where symbol=%s and exchange='binance' and period='4h' and environment='mainnet'
                     and timestamp between %s and %s order by timestamp""",
                (sym, o_min - 20 * 86400, o_min + 30 * 86400))
            bars = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
            for t in ts:
                o_ep = int(t["opened_at"].replace(tzinfo=CST).timestamp())
                upto = [c for (ts_, c) in bars if ts_ <= o_ep]
                if len(upto) < 30:
                    t["ema_dist"] = None
                    t["ema_label"] = "no_data"
                    continue
                k = 2 / 22
                ema = upto[0]
                emas = []
                for c in upto:
                    ema = ema + k * (c - ema)
                    emas.append(ema)
                t["ema_dist"] = (upto[-1] - emas[-1]) / emas[-1] * 100.0
                rising, falling = emas[-1] > emas[-5], emas[-1] < emas[-5]
                t["ema_label"] = ("up" if (upto[-1] > emas[-1] and rising)
                                  else "down" if (upto[-1] < emas[-1] and falling) else "chop")
    # 入场时模型方向
    with psycopg.connect(ANALYTICS, autocommit=True) as ac:
        cur = ac.cursor()
        cache = {}
        for t in trades:
            sym = t["symbol"]
            if sym not in cache:
                cur.execute("""select created_at, regime_direction from market_analysis_snapshots
                               where symbol=%s order by created_at""", (sym,))
                cache[sym] = [(r[0].replace(tzinfo=CST).timestamp(), r[1])
                              for r in cur.fetchall() if r[0] is not None]
            o_ep = int(t["opened_at"].replace(tzinfo=CST).timestamp())
            pick = None
            for v, d in cache[sym]:
                if v <= o_ep:
                    pick = d
                else:
                    break
            t["model_dir"] = pick or "unknown"
    return trades


def agg(rows):
    if not rows:
        return {"n": 0, "total": 0.0, "avg": 0.0, "win": 0.0, "avg_peak": 0.0}
    pnls = [float(r["pnl"] or 0) for r in rows]
    peaks = [float(r["peak_pnl_pct"] or 0) for r in rows]
    return {"n": len(rows), "total": round(sum(pnls), 2), "avg": round(st.mean(pnls), 2),
            "win": round(100.0 * sum(1 for p in pnls if p > 0) / len(pnls), 1),
            "avg_peak": round(st.mean(peaks) * 100, 2)}


def main() -> int:
    trades = enrich(load())
    half = len(trades) // 2
    print("样本: 09-15 后中线已平仓 %d 笔（前半 %d / 后半 %d）" % (len(trades), half, len(trades) - half))
    base = agg(trades)
    print("基线: 总 %+.2f 均 %+.2f/笔 胜率 %.1f%% 均峰值 %.2f%%"
          % (base["total"], base["avg"], base["win"], base["avg_peak"]))

    # ── A. 峰值分桶 × 实现盈亏 ──
    print("\n== A. 峰值分桶（裸出场结构能不能救它们）==")
    buckets = [("从没动过 peak<1%", lambda p: p < 0.01),
               ("微弱 peak 1~2.5%", lambda p: 0.01 <= p < 0.025),
               ("动过 peak 2.5~5%", lambda p: 0.025 <= p < 0.05),
               ("大动 peak>=5%", lambda p: p >= 0.05)]
    for name, fn in buckets:
        rows = [t for t in trades if fn(float(t["peak_pnl_pct"] or 0))]
        a = agg(rows)
        print("  %-20s n=%3d 总 %+8.2f 均 %+6.2f 胜率 %5.1f%% 峰值均值 %5.2f%%"
              % (name, a["n"], a["total"], a["avg"], a["win"], a["avg_peak"]))

    # ── B. 入场期筛子（前后半双段验证）──
    print("\n== B. 入场期筛子：剔除后对'均/笔'与'总'的影响（前后半都要为正才算过）==")
    cands = [
        ("入场价高于 4h EMA21 >3%", lambda t: (t.get("ema_dist") or 0) > 3.0),
        ("入场价高于 4h EMA21 >5%", lambda t: (t.get("ema_dist") or 0) > 5.0),
        ("入场价低于 4h EMA21", lambda t: (t.get("ema_dist") or 0) < 0),
        ("模型方向=short（逆模型做多）", lambda t: t.get("model_dir") == "short"),
        ("模型方向=short 或 neutral", lambda t: t.get("model_dir") in ("short", "neutral")),
        ("4h 标签=chop", lambda t: t.get("ema_label") == "chop"),
        ("4h 标签=up", lambda t: t.get("ema_label") == "up"),
        ("入场价高于 EMA21 且标签=up", lambda t: (t.get("ema_dist") or 0) > 2 and t.get("ema_label") == "up"),
    ]
    # 需要剔除的"坏单"数量与收益影响：剔除 sel 后剩余样本的表现
    for name, fn in cands:
        bad = [t for t in trades if fn(t)]
        keep = [t for t in trades if not fn(t)]
        if not bad or not keep:
            print("  %-30s 样本不足（bad=%d keep=%d）" % (name, len(bad), len(keep)))
            continue
        ab, ak = agg(bad), agg(keep)
        # 前后半：分别算"被剔除组的均/笔"，两者都应为负 ⇒ 稳定坏单
        bad_h1 = [t for t in trades[:half] if fn(t)]
        bad_h2 = [t for t in trades[half:] if fn(t)]
        a1, a2 = agg(bad_h1), agg(bad_h2)
        print("  %-30s 剔除 n=%3d(总 %+8.2f, 均 %+6.2f) | 剩余 n=%3d(总 %+8.2f, 均 %+6.2f)"
              % (name, ab["n"], ab["total"], ab["avg"], ak["n"], ak["total"], ak["avg"]))
        print("  %-30s   前后半剔除组均/笔: %+.2f (n=%d) / %+.2f (n=%d)  %s"
              % ("", a1["avg"], a1["n"], a2["avg"], a2["n"],
                 "✅两段都坏" if (a1["n"] and a2["n"] and a1["avg"] < 0 and a2["avg"] < 0) else "⚠️不一致"))
    # ── C. 时段 ──
    print("\n== C. 入场时段（北京时间）==")
    for lo, hi, label in ((0, 8, "00-08 亚洲夜盘"), (8, 16, "08-16 亚洲日盘"),
                          (16, 20, "16-20 欧洲盘"), (20, 24, "20-24 美盘")):
        rows = [t for t in trades if lo <= t["opened_at"].hour < hi]
        a = agg(rows)
        print("  %-14s n=%3d 总 %+8.2f 均 %+6.2f 胜率 %5.1f%%" % (label, a["n"], a["total"], a["avg"], a["win"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
