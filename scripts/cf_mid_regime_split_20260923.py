# -*- coding: utf-8 -*-
"""[2026-09-23] regime 分层反事实（深入研究 R1/R3）：

背景：`regime_type` 自 08-10 起 80%+ 为 'unknown'（分类器停摆）⇒ 09-20 报告的 regime 分层
做不了。本脚本用两条**可审计**的替代标签重做分层，不改验收标准，只回答
"追踪增益是趋势段红利还是全域成立"：

  标签A（模型方向）：`market_analysis_snapshots.regime_direction`（入仓前最近一条 15m 快照，
    long / short / neutral；该列 30 天窗口全覆盖，见 probe_regime_coverage）。
  标签B（价格趋势，模型面=0）：4h K 线 EMA21 —— 入场时 close>EMA21 且 EMA21 近 4 根上升=up；
    close<EMA21 且下降=down；其余=chop。

对每个标签桶：基线期望 vs 候选配置期望的 Δ（两价源 kline/agg 各自独立跑）。
"""
from __future__ import annotations

import io
import statistics as st
import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import cf_mid_trail_grid as G  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def load_labels(trades):
    """给每笔贴：model_dir（regime_direction）与 price_trend（4h EMA21）。"""
    import datetime as dt

    # 标签A：模型方向（created_at naive=北京钟面；opened_at 同为北京钟面）
    with G.psycopg.connect(G.ANALYTICS, autocommit=True) as ac:
        acur = ac.cursor()
        cache = {}
        for t in trades:
            sym = t["symbol"]
            if sym not in cache:
                acur.execute(
                    """select created_at, regime_direction from market_analysis_snapshots
                       where symbol=%s order by created_at""", (sym,))
                cache[sym] = sorted(
                    [(r[0].replace(tzinfo=G.CST).timestamp(), r[1])
                     for r in acur.fetchall() if r[0] is not None])
            o_ep = int(t["opened_at"].replace(tzinfo=G.CST).timestamp())
            pick = None
            for v, d in cache[sym]:
                if v <= o_ep:
                    pick = d
                else:
                    break
            t["model_dir"] = pick or "unknown"

    # 标签B：4h EMA21 价格趋势（逐币种一次性取全区间 K 线）
    need = {}
    for t in trades:
        sym = t["symbol"]
        o = int(t["opened_at"].replace(tzinfo=G.CST).timestamp())
        need.setdefault(sym, o)
        need[sym] = min(need[sym], o)
    with G.psycopg.connect(G.MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for sym, o in need.items():
            cur.execute(
                """select timestamp, close_price from crypto_klines
                   where symbol=%s and exchange='binance' and period='4h' and environment='mainnet'
                     and timestamp between %s and %s order by timestamp""",
                (sym, o - 20 * 86400, o + 6 * 86400))
            bars = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
            # 每条仓单独立回放 EMA（入场前的 bar 序列是公共前缀，可增量）
            closes = [b[1] for b in bars]
            ts = [b[0] for b in bars]
            for t in trades:
                if t["symbol"] != sym:
                    continue
                o_ep = int(t["opened_at"].replace(tzinfo=G.CST).timestamp())
                upto = [c for (tsi, c) in zip(ts, closes) if tsi <= o_ep]
                if len(upto) < 30:
                    t["price_trend"] = "no_data"
                    continue
                ema = upto[0]
                k = 2 / (21 + 1)
                emas = []
                for c in upto:
                    ema = ema + k * (c - ema)
                    emas.append(ema)
                close_now = upto[-1]
                rising = emas[-1] > emas[-5]
                falling = emas[-1] < emas[-5]
                if close_now > emas[-1] and rising:
                    t["price_trend"] = "up"
                elif close_now < emas[-1] and falling:
                    t["price_trend"] = "down"
                else:
                    t["price_trend"] = "chop"
    return trades


def main() -> int:
    trades = G.load_trades("30")
    trades = load_labels(trades)

    cands = [
        {"name": "追踪 3.5/2.0", "act": 3.5, "cb": 2.0},
        {"name": "追踪 5.0/2.5", "act": 5.0, "cb": 2.5},
        {"name": "追踪 5.0/3.5", "act": 5.0, "cb": 3.5},
    ]
    base_cfg = {"name": "baseline", "use_snapshot": True, "cap_signal": True}

    for source in ("kline", "agg"):
        paths = {}
        with G.psycopg.connect(G.MARKET, autocommit=True) as mc:
            cur = mc.cursor()
            for t in trades:
                o = int(t["opened_at"].replace(tzinfo=G.CST).timestamp())
                c = int(t["closed_at"].replace(tzinfo=G.CST).timestamp())
                end = max(c, o + 3600) + int(72 * 3600)
                paths[t["id"]] = G.load_path(cur, t["symbol"], "binance", o, end, source)
        usable = [t for t in trades if len(paths.get(t["id"]) or []) >= 5]

        def exp_pp(sel, cfg):
            if not sel:
                return 0.0, 0
            rows = [G.simulate(t, paths[t["id"]], dict(cfg, cap_signal=True)) for t in sel]
            return (st.mean([r["net_pp"] for r in rows]), len(rows))

        print("=" * 100)
        print("source=%s  可用样本 n=%d" % (source, len(usable)))
        for label in ("price_trend", "model_dir"):
            buckets = {}
            for t in usable:
                buckets.setdefault(t[label], []).append(t)
            print("\n  ── 标签 %s ──" % label)
            order = ["up", "down", "chop", "no_data", "long", "short", "neutral", "unknown"]
            hdr = "    %-10s %5s %-9s" % ("桶", "n", "基线期望")
            for c in cands:
                hdr += " %-11s" % c["name"].replace("追踪 ", "Δ追踪")
            print(hdr)
            for b in [k for k in order if k in buckets]:
                sel = buckets[b]
                b_exp, bn = exp_pp(sel, base_cfg)
                line = "    %-10s %5d %+9.4f" % (b, bn, b_exp)
                for c in cands:
                    c_exp, _ = exp_pp(sel, c)
                    line += " %+11.4f" % (c_exp - b_exp)
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
