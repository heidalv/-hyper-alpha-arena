# -*- coding: utf-8 -*-
"""[2026-09-23] R2 机制分解：追踪 5.0/2.5 相对基线（逐笔快照）到底改了哪些单、怎么改的。

对每笔（kline 主源 / agg 复现）比较基线 vs 候选的出场（why, net_pp），归类：
  A  基线 sl / 候选 trail 且 >0         —— 把止损变成小赢（锁回撤）
  B  两者都 trail                        —— 同是追踪，谁更早/更晚
  C  候选 trail>0 / 基线 tp或信号且更高   —— 提前锁利砍掉了更多利润
  D  候选 sl / 基线 trail>0              —— 追踪变松后反而不保护
  E  出场相同或仅时间微差                 —— 不变
"""
from __future__ import annotations

import io
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import cf_mid_trail_grid as G  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main() -> int:
    trades = G.load_trades("30")
    base_cfg = {"name": "baseline", "use_snapshot": True, "cap_signal": True}
    cand = {"name": "追踪 5.0/2.5", "act": 5.0, "cb": 2.5}

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
        rows_b = {t["id"]: G.simulate(t, paths[t["id"]], dict(base_cfg, cap_signal=True)) for t in usable}
        rows_c = {t["id"]: G.simulate(t, paths[t["id"]], dict(cand, cap_signal=True)) for t in usable}

        cats = defaultdict(list)
        for t in usable:
            b, c = rows_b[t["id"]], rows_c[t["id"]]
            d = c["net_pp"] - b["net_pp"]
            bw, cw = b["why"], c["why"]
            if abs(d) < 0.01:
                cats["E 不变"].append(d)
            elif bw == "sl" and cw == "trail" and c["net_pp"] > 0:
                cats["A sl→追踪小赢"].append(d)
            elif bw == "trail" and cw == "trail":
                cats["B 同追踪(更松/更紧)"].append(d)
            elif cw == "trail" and c["net_pp"] > 0 and bw in ("tp", "signal_actual") and d < 0:
                cats["C 提前锁利砍掉利润"].append(d)
            elif cw == "sl" and bw == "trail" and b["net_pp"] > 0:
                cats["D 追踪变松反而不保护"].append(d)
            else:
                cats["其他(出场类型互变)"].append(d)
        print("=" * 96)
        print("source=%s n=%d  base_exp=%+.4f cand_exp=%+.4f Δ=%+.4f" % (
            source, len(usable),
            st.mean([r["net_pp"] for r in rows_b.values()]),
            st.mean([r["net_pp"] for r in rows_c.values()]),
            st.mean([r["net_pp"] for r in rows_c.values()]) - st.mean([r["net_pp"] for r in rows_b.values()])))
        for k in ("A sl→追踪小赢", "B 同追踪(更松/更紧)", "C 提前锁利砍掉利润", "D 追踪变松反而不保护",
                  "其他(出场类型互变)", "E 不变"):
            vals = cats.get(k)
            if not vals:
                continue
            print("  %-22s n=%3d  合计Δpp=%+8.2f  平均Δ=%+7.4f  其中变好%d笔/变差%d笔" % (
                k, len(vals), sum(vals), st.mean(vals),
                sum(1 for v in vals if v > 0), sum(1 for v in vals if v < 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
