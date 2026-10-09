# -*- coding: utf-8 -*-
"""[2026-09-23 深夜] 条件化出场（趋势门控）——针对"前半全负/行情红利"的定向实验：

前几轮的结论：mid 追踪网格与 long D1b/D2b 总 Δ 都为正，但**增益集中在趋势段**，
前半（震荡段）为负或不稳（agg 源上前半全负）。本脚本测试**门控版**：
  门 = 入场时 4h EMA21 方向标签（up/down=方向市，chop=震荡，与 cf_mid_regime_split 同款，模型面=0）
  规则 = 方向市 → 用候选出场（放宽追踪/分档）；震荡市 → 维持基线（该笔快照现状）
即"只在方向市放宽出场"（09-20 报告 §6 的方案 A）。

验收（写死）：两个价源 Δ 方向一致；前后两半 Δ 均非负；最差单笔不比基线差超过 1.5pp；
并单独列出"门控后震荡桶内不再亏钱"。
"""
from __future__ import annotations

import io
import statistics as st
import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import cf_mid_trail_grid as G  # noqa: E402
import cf_long_exit_grid_20260923 as LG  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def price_trend_label(t, cache):
    """入场时 4h EMA21 方向标签（与 cf_mid_regime_split 完全同口径）。"""
    sym = t["symbol"]
    o_ep = int(t["opened_at"].replace(tzinfo=G.CST).timestamp())
    if sym not in cache:
        with G.psycopg.connect(G.MARKET, autocommit=True) as mc:
            cur = mc.cursor()
            cur.execute(
                """select timestamp, close_price from crypto_klines
                   where symbol=%s and exchange='binance' and period='4h' and environment='mainnet'
                     and timestamp between %s and %s order by timestamp""",
                (sym, o_ep - 20 * 86400, o_ep + 6 * 86400))
            bars = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
        cache[sym] = bars
    else:
        bars = cache[sym]
    upto = [c for (ts, c) in bars if ts <= o_ep]
    if len(upto) < 30:
        return "no_data"
    k = 2 / 22
    ema = upto[0]
    emas = []
    for c in upto:
        ema = ema + k * (c - ema)
        emas.append(ema)
    cnow, rising, falling = upto[-1], emas[-1] > emas[-5], emas[-1] < emas[-5]
    if cnow > emas[-1] and rising:
        return "up"
    if cnow < emas[-1] and falling:
        return "down"
    return "chop"


def main() -> int:
    # ── MID ──
    mid_trades = G.load_trades("30")
    labels = {}
    for t in mid_trades:
        labels[t["id"]] = price_trend_label(t, {})
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
            for t in mid_trades:
                o = int(t["opened_at"].replace(tzinfo=G.CST).timestamp())
                c = int(t["closed_at"].replace(tzinfo=G.CST).timestamp())
                paths[t["id"]] = G.load_path(cur, t["symbol"], "binance", o,
                                             max(c, o + 3600) + int(72 * 3600), source)
        usable = [t for t in mid_trades if len(paths.get(t["id"]) or []) >= 5]
        usable.sort(key=lambda t: t["opened_at"])
        half = len(usable) // 2

        def run(sel, cfg, gated):
            rows = []
            for t in sel:
                lab = labels.get(t["id"]) or "chop"
                use = cfg if (not gated or lab in ("up", "down")) else base_cfg
                rows.append(G.simulate(t, paths[t["id"]], dict(use, cap_signal=True)))
            return rows

        base_rows = run(usable, base_cfg, False)
        b_exp = st.mean([r["net_pp"] for r in base_rows])
        b_worst = min(r["net_pp"] for r in base_rows)
        print("=" * 96)
        print("MID source=%s n=%d 基线exp=%+.4fpp 最差=%+.2f" % (source, len(usable), b_exp, b_worst))
        print("  %-24s %-9s %-9s %-9s %-9s %-9s" % ("配置", "Δ总pp", "前半Δ", "后半Δ", "方向市Δ", "震荡市Δ"))
        for cfg in cands:
            for gated, tag in ((False, "全开"), (True, "门控")):
                rows = run(usable, cfg, gated)
                fh = run(usable[:half], cfg, gated)
                sh = run(usable[half:], cfg, gated)
                b_fh = st.mean([r["net_pp"] for r in run(usable[:half], base_cfg, False)])
                b_sh = st.mean([r["net_pp"] for r in run(usable[half:], base_cfg, False)])
                dir_sel = [t for t in usable if (labels.get(t["id"]) or "chop") in ("up", "down")]
                ch_sel = [t for t in usable if (labels.get(t["id"]) or "chop") == "chop"]
                d_dir = (st.mean([r["net_pp"] for r in run(dir_sel, cfg, gated)])
                         - st.mean([r["net_pp"] for r in run(dir_sel, base_cfg, False)]))
                d_ch = (st.mean([r["net_pp"] for r in run(ch_sel, cfg, gated)])
                        - st.mean([r["net_pp"] for r in run(ch_sel, base_cfg, False)]))
                name = cfg["name"] + ("[门控]" if gated else "[全开]")
                print("  %-24s %+9.3f %+9.3f %+9.3f %+9.3f %+9.3f" % (
                    name, st.mean([r["net_pp"] for r in rows]) - b_exp,
                    st.mean([r["net_pp"] for r in fh]) - b_fh,
                    st.mean([r["net_pp"] for r in sh]) - b_sh, d_dir, d_ch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
