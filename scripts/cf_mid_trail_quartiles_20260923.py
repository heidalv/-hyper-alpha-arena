# -*- coding: utf-8 -*-
"""[2026-09-23] 补充稳健性：把 30 天样本按 opened_at 切成 4 段，逐段算 Δ（vs 基线快照）。

验收标准（先写死，与 09-20 一致）只用了"前后两半"，但用户这次痛点在**最近几天的变盘段**：
两半口径可能对"最后 5 天的连续止损"不敏感，这里补一个 4 段视图，不改验收标准本身，
只作为补充证据披露。若某配置在最后一段（变盘段）Δ 为负，如实报告。
"""
from __future__ import annotations

import io
import statistics as st
import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import cf_mid_trail_grid as G  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main() -> int:
    for source in ("kline", "agg"):
        trades = G.load_trades("30")
        paths = {}
        with G.psycopg.connect(G.MARKET, autocommit=True) as mc:
            cur = mc.cursor()
            for t in trades:
                o = int(t["opened_at"].replace(tzinfo=G.CST).timestamp())
                c = int(t["closed_at"].replace(tzinfo=G.CST).timestamp())
                end = max(c, o + 3600) + int(72 * 3600)
                paths[t["id"]] = G.load_path(cur, t["symbol"], "binance", o, end, source)
        usable = [t for t in trades if len(paths.get(t["id"]) or []) >= 5]
        usable.sort(key=lambda t: t["opened_at"])
        n = len(usable)
        q = n // 4
        segs = [usable[:q], usable[q:2*q], usable[2*q:3*q], usable[3*q:]]

        def run(trades_sel, cfg):
            rows = [G.simulate(t, paths[t["id"]], dict(cfg, cap_signal=True)) for t in trades_sel]
            return st.mean([r["net_pp"] for r in rows]) if rows else 0.0

        base_cfg = {"name": "baseline", "use_snapshot": True, "cap_signal": True}
        cands = [
            {"name": "追踪 3.0/1.5", "act": 3.0, "cb": 1.5},
            {"name": "追踪 3.5/2.0", "act": 3.5, "cb": 2.0},
            {"name": "追踪 5.0/2.5", "act": 5.0, "cb": 2.5},
            {"name": "追踪 5.0/3.5", "act": 5.0, "cb": 3.5},
            {"name": "TP 10% + 追踪 5.0/2.5", "act": 5.0, "cb": 2.5, "tp_pct": 10.0},
        ]
        print("=" * 96)
        print("source=%s  n=%d  (4 段按 opened_at 均分, 每段 %d 笔)" % (source, n, q))
        seg_dates = ["%s~%s" % (str(s[0]["opened_at"])[5:10], str(s[-1]["opened_at"])[5:10]) for s in segs]
        print("段日期: %s" % " | ".join(seg_dates))
        hdr = "  %-22s" % "配置" + "".join(" %-9s" % d for d in seg_dates) + "  %-8s" % "两半Δ(min)"
        print(hdr)
        for cfg in cands:
            deltas = [run(s, cfg) - run(s, base_cfg) for s in segs]
            half1 = run(usable[:n//2], cfg) - run(usable[:n//2], base_cfg)
            half2 = run(usable[n//2:], cfg) - run(usable[n//2:], base_cfg)
            cells = "".join(" %+9.4f" % d for d in deltas)
            print("  %-22s%s  min(两半)=%+.4f" % (cfg["name"], cells, min(half1, half2)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
