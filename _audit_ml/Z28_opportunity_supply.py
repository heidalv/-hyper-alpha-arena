# -*- coding: utf-8 -*-
"""Z28：市场可入场机会盘点——把门条件铺到全市场 1h bar 上（§24 #25）。

Z27 显示：论题供给充足（982/30d），但 recommend_open → opened 转化仅 ~8%，
且只分析 9 个币种。若「门条件」在更多币种上同样经常满足，
那么**扩大扫描币种**就能在不放宽门（=不降质量）的前提下提高流量。

本脚本：对全市场（有 30 天 1h 数据的币种）逐 bar 判定生产门，
统计「满足门的连续段落（episode）」数量，给出每币每天的机会数，
并与当前扫描宇宙（9 币）对比。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, learned_ok_prod  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

MARKET_URL = os.getenv("MARKET_DATABASE_URL",
                       "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
DAYS = 30
CURRENT_UNIVERSE = {"XPL", "UNI", "VIRTUAL", "BTC", "XRP", "SOL", "BNB", "ASTER", "ETH"}


def main() -> int:
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        # 选有足够 1h 历史的币种（优先 asterdex，其次 binance）
        rows = c.execute(text("""
            select exchange, symbol, count(*) n, min(timestamp) mn, max(timestamp) mx
            from crypto_klines
            where period='1h' and timestamp >= extract(epoch from now() - interval '45 days')
            group by 1,2 having count(*) >= 400
        """)).fetchall()
        sym_ex = defaultdict(dict)
        for ex, sym, n, mn, mx in rows:
            sym_ex[sym][ex] = (n, int(mn), int(mx))
        print(f"可用币种（1h≥400 根）= {len(sym_ex)}")

        h1 = defaultdict(list)
        for ex, sym, ts, o, h, l, cl in c.execute(text("""
            select exchange, symbol, timestamp, open_price, high_price, low_price, close_price
            from crypto_klines
            where period='1h' and timestamp >= extract(epoch from now() - interval '45 days')
            order by symbol, timestamp
        """)).fetchall():
            h1[(ex, sym)].append((int(ts), float(o), float(h), float(l), float(cl)))
        d1 = defaultdict(list)
        for ex, sym, ts, o, h, l, cl in c.execute(text("""
            select exchange, symbol, timestamp, open_price, high_price, low_price, close_price
            from crypto_klines
            where period='1d' and timestamp >= extract(epoch from now() - interval '400 days')
            order by symbol, timestamp
        """)).fetchall():
            d1[(ex, sym)].append((int(ts), float(o), float(h), float(l), float(cl)))

    per_sym = {}
    for sym, exs in sym_ex.items():
        best = None
        for ex in ("asterdex", "binance"):
            s = h1.get((ex, sym))
            ds = d1.get((ex, sym))
            if s and ds and len(s) >= 400 and len(ds) >= 70:
                best = (s, ds)
                break
        if not best:
            continue
        s, ds = best
        reg, pos, chg = build_bar_features(s, ds)
        # 只统计最近 DAYS 天
        cutoff = s[-1][0] - DAYS * 86400
        episodes = 0
        in_ep = False
        ok_bars = 0
        regs = defaultdict(int)
        for k in range(len(s)):
            if s[k][0] < cutoff:
                continue
            ok = learned_ok_prod(reg[k], pos[k], chg[k])
            if ok:
                ok_bars += 1
                regs[reg[k]] += 1
                if not in_ep:
                    episodes += 1
                    in_ep = True
            else:
                in_ep = False
        per_sym[sym] = {"episodes": episodes, "ok_bars": ok_bars, "n_bars": len(s),
                        "regs": dict(regs)}

    print(f"可判定币种 = {len(per_sym)}")
    tot_ep = sum(v["episodes"] for v in per_sym.values())
    tot_ok = sum(v["ok_bars"] for v in per_sym.values())
    cur_ep = sum(v["episodes"] for k, v in per_sym.items() if k in CURRENT_UNIVERSE)
    print(f"\n近 {DAYS} 天全市场满足门的「段落」总数 = {tot_ep}"
          f"（{tot_ep/DAYS:.1f}/天），满足门的 bar 数 = {tot_ok}")
    print(f"当前 9 币宇宙覆盖 = {cur_ep} 段（{cur_ep/max(tot_ep,1):.1%}）"
          f"→ 扩到全市场理论上可放大 {tot_ep/max(cur_ep,1):.1f}x")

    print(f"\n=== 每币每天机会数 TOP25 ===")
    print(f"{'symbol':<12}{'段落':>6}{'每币每天':>10}{'ok_bars':>9}{'regime 分布':<30}")
    for sym, v in sorted(per_sym.items(), key=lambda x: -x[1]["episodes"])[:25]:
        print(f"{sym:<12}{v['episodes']:>6}{v['episodes']/DAYS:>10.2f}{v['ok_bars']:>9}"
              f"  {str(v['regs'])[:40]}")

    print(f"\n=== 当前宇宙逐币 ===")
    for sym in sorted(CURRENT_UNIVERSE):
        v = per_sym.get(sym)
        if not v:
            print(f"  {sym:<10} 无数据")
            continue
        print(f"  {sym:<10} 段落={v['episodes']:>3}（{v['episodes']/DAYS:.2f}/天）"
              f" ok_bars={v['ok_bars']:>4} regime={v['regs']}")

    # 去掉当前宇宙后的「新增可扫描池」
    extra = {k: v for k, v in per_sym.items() if k not in CURRENT_UNIVERSE}
    extra_ep = sum(v["episodes"] for v in extra.values())
    print(f"\n=== 当前宇宙之外：{len(extra)} 个币，{extra_ep} 段机会"
          f"（{extra_ep/DAYS:.1f}/天）===")
    top_extra = sorted(extra.items(), key=lambda x: -x[1]["episodes"])[:15]
    print("  TOP15: " + ", ".join(f"{s}({v['episodes']})" for s, v in top_extra))

    # 流动性过滤后的候选宇宙
    print("\n=== 候选扩展宇宙（按「段落数」排序，仅流动性 ≥$0.5M/h）===")
    with eng.connect() as c:
        liq = {}
        for sym, med in c.execute(text("""
            select symbol, percentile_cont(0.5) within group (order by volume * close_price) med
            from crypto_klines
            where period='1h' and timestamp >= extract(epoch from now() - interval '30 days')
            group by 1
        """)).fetchall():
            liq[sym] = float(med or 0)
    cand = [(s, v) for s, v in extra.items() if liq.get(s, 0) >= 5e5]
    cand.sort(key=lambda x: -x[1]["episodes"])
    print(f"  满足流动性的宇宙外币种数 = {len(cand)}，"
          f"合计 {sum(v['episodes'] for _s, v in cand)} 段（"
          f"{sum(v['episodes'] for _s, v in cand)/DAYS:.1f}/天）")
    print(f"  {'symbol':<10}{'段落':>5}{'$/h':>12}{'up段':>6}{'chop段':>7}")
    for s, v in cand[:30]:
        print(f"  {s:<10}{v['episodes']:>5}{liq.get(s,0)/1e6:>11.2f}M"
              f"{v['regs'].get('up',0):>6}{v['regs'].get('chop',0):>7}")
    print("\n  建议：把这些币按「会话扫描宇宙」纳入（不改门条件）；"
          "chop regime 仍以主流币为主（§37.5）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
