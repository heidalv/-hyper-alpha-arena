"""[2026-09-20] 中线 `exit_policy:min_roi_decay` 规则测试（只读）。

为什么单独测它：信号平仓通道归因里，只有这一条在**两个独立价源上逐位吻合**
（9 笔，Δ 分别 +2.90pp / +2.89pp，Δ_total ≈ +$195，5/9 判为"砍早了"）。
它是确定性规则（满 12h 且 ROI<+0.5% ⇒ 平），不含 LLM 判断 ⇒ **可以被精确重放**，
所以不再用"持有到窗口末"的上界，而是真的把规则换成候选值再跑一遍。

规则顺序（每根棒，保守：先看不利用方向）：
  SL(逐笔快照 sl_pct) → TP(落库 tp_price) → 追踪(逐笔快照 2.5/1.2 或 1.0/0.5) → **min_roi 候选** → 时间上限
  实际由信号/策略平掉的单：仍在其实际时刻平仓（除非候选规则更早触发）⇒ 只改我们要测的那一条。

复用 `cf_mid_trail_grid.py` 的取数、路径、逐笔参数与统计函数（避免二次实现走样）。

用法：
  .venv\\Scripts\\python.exe scripts/cf_mid_minroi_rule.py --days 90 --source kline
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import statistics as st
import sys
from typing import Dict, List, Optional

import psycopg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cf_mid_trail_grid as G  # noqa: E402

VARIANTS = [
    ("现状 12h/0.5%", None),
    ("12h/0.0%", [[43200, 0.0], [86400, 0.0]]),
    ("24h/0.0%", [[86400, 0.0]]),
    ("24h/-0.5%", [[86400, -0.5]]),
    ("关掉 min_roi", []),
]


def snapshot_minroi(t: Dict) -> List:
    try:
        pol = (G.json.loads(t["exit_state_json"] or "{}").get("exit_policy") or {})
        v = pol.get("min_roi")
        return v if isinstance(v, list) else [[43200, 0.5], [86400, 0.0]]
    except Exception:
        return [[43200, 0.5], [86400, 0.0]]


def simulate(t: Dict, bars, minroi: Optional[List], cap_signal: bool = True) -> Dict:
    if minroi is None:
        minroi = snapshot_minroi(t)
    side = t["side"]
    long_ = str(side).lower().startswith("l")
    sign = 1.0 if long_ else -1.0
    entry = float(t["entry_price"])
    eff_sl, tp_pct, tl, act0, cb0 = G.per_trade_params(t)
    sl = entry * (1 - sign * eff_sl / 100.0)
    tp = entry * (1 + sign * tp_pct / 100.0)
    peak = 0.0
    o = int(t["opened_at"].replace(tzinfo=G.CST).timestamp())
    c_ep = int(t["closed_at"].replace(tzinfo=G.CST).timestamp())
    keep_signal = cap_signal and str(t["close_reason"] or "") not in G.MECH_EXITS
    exit_px, why = None, None
    for ts, _op, hi, lo, cl in bars:
        hold = ts - o
        if hold > tl:
            exit_px, why = cl, "time_limit"
            break
        if act0 and peak >= act0:
            cand = entry * (1 + sign * (peak - cb0) / 100.0)
            sl = max(sl, cand) if long_ else min(sl, cand)
        adv, fav = (lo, hi) if long_ else (hi, lo)
        if (adv <= sl) if long_ else (adv >= sl):
            exit_px, why = sl * (1 - sign * G.PEN_PP / 100.0), "sl/trail"
            break
        if (fav >= tp) if long_ else (fav <= tp):
            exit_px, why = tp, "tp"
            break
        for th, roi in sorted(minroi):
            if hold >= th:
                cur_roi = ((cl - entry) / entry) * sign * 100
                if cur_roi < roi:
                    exit_px, why = cl, "min_roi"
                    break
        if exit_px is not None:
            break
        if keep_signal and ts >= c_ep:
            exit_px, why = float(t["close_price"]), "signal_actual"
            break
        ext = max(hi, cl) if long_ else min(lo, cl)
        peak = max(peak, ((ext - entry) / entry) * sign * 100)
    if exit_px is None:
        exit_px, why = bars[-1][4], "data_end"
    gross = ((exit_px - entry) / entry) * sign * 100
    net = gross - G.FEE_PP
    return {"id": t["id"], "why": why, "net_pp": net,
            "usd": net / 100.0 * (float(t["size"]) * entry), "opened_at": t["opened_at"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="90")
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--source", default="kline", choices=("agg", "kline"))
    ap.add_argument("--post-hours", type=float, default=72.0)
    a = ap.parse_args(argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    trades = G.load_trades(a.days)
    mech = [t for t in trades if str(t["close_reason"] or "") in G.MECH_EXITS]
    print("样本: %d 笔 mid 已平仓（近 %s 天）其中实际机械出场 %d 笔；源=%s:%s"
          % (len(trades), a.days, len(mech), a.source, a.exchange))
    paths: Dict[int, List] = {}
    with psycopg.connect(G.MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for t in trades:
            o = int(t["opened_at"].replace(tzinfo=G.CST).timestamp())
            c = int(t["closed_at"].replace(tzinfo=G.CST).timestamp())
            paths[t["id"]] = G.load_path(cur, t["symbol"], a.exchange, o,
                                         max(c, o + 3600) + int(a.post_hours * 3600), a.source)
    usable = [t for t in trades if len(paths.get(t["id"]) or []) >= 5]
    half = len(usable) // 2
    print("  %-16s %-9s %-10s %-9s %-9s %-9s %s" %
          ("配置", "单笔期望", "总USD", "Δ期望", "前半Δ", "后半Δ", "min_roi触发"))
    base = None
    out = {}
    for label, mr in VARIANTS:
        rows = [simulate(t, paths[t["id"]], mr) for t in usable]
        net = [r["net_pp"] for r in rows]
        usd = sum(r["usd"] for r in rows)
        if base is None:
            base = net
        fh = [r["net_pp"] for r in (rows[:half])]
        sh = [r["net_pp"] for r in (rows[half:])]
        bfh, bsh = base[:half], base[half:]
        fired = sum(1 for r in rows if r["why"] == "min_roi")
        out[label] = {"exp_pp": round(st.mean(net), 4), "usd": round(usd, 1),
                      "delta_exp": round(st.mean(net) - st.mean(base), 4),
                      "delta_first": round(st.mean(fh) - st.mean(bfh), 4),
                      "delta_second": round(st.mean(sh) - st.mean(bsh), 4), "fired": fired}
        print("  %-16s %+-9.4f %+-10.1f %+-9.4f %+-9.4f %+-9.4f %d" %
              (label, st.mean(net), usd, st.mean(net) - st.mean(base),
               st.mean(fh) - st.mean(bfh), st.mean(sh) - st.mean(bsh), fired))
    print("  [口径] Δ 为相对「现状 12h/0.5%」的同批路径差；样本为滚动窗口，跨天复跑会有少量漂移")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
