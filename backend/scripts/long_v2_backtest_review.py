# -*- coding: utf-8 -*-
"""A7 复核：长线策略回测（decide_long 同核 + weekly_atr_causal 无前视 + 参数网格）。

与 Phase C/E 原版的差异（复核项）：
1. weekly_atr → weekly_atr_causal（修复原版前向填充的 6 天前视）；
2. 决策函数 = 实盘同款 decide_long（含 no_progress/极端回撤/金字塔三档）；
3. 网格扩展：mult 1.5/2/2.5/3 × L1 阈值 2/3 × 金字塔开/关。
输出 data/long_v2_backtest_review.json。
"""
import os, sys, json, time
os.chdir(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
os.environ.setdefault("LEARNING_LOOP_ENABLED", "false")

import numpy as np
import pandas as pd

from backend.core.tenant import tenant_id_var, is_admin_var
tenant_id_var.set(326); is_admin_var.set(326)

from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer as _fbs
from backend.services.trend_layer import classify_series
from backend.services.long_tier_manager import weekly_atr_causal, is_new_high, decide_long

_SYMS = ["BTC", "ETH", "SOL", "BNB", "XRP"]


def load(sym):
    rows = _fbs._load_klines(sym, "1d", 3000)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close", "high", "low"]).reset_index(drop=True)


def run_one(sym, mult, l1_thr, pyramid_on):
    df = load(sym)
    if df is None or len(df) < 400:
        return None
    cls = classify_series(df)
    atr_series = weekly_atr_causal(df)
    close = df["close"].astype(float).values
    high = df["high"].astype(float).values
    state = cls["state"].values
    scores = cls["score"].values
    nh = is_new_high(pd.Series(high), window=60).values
    n = len(close)

    trades = []
    i = 260
    pos = None
    while i < n:
        up = (state[i] == "up") and float(scores[i]) >= l1_thr
        atr_i = float(atr_series.iloc[i]) if pd.notna(atr_series.iloc[i]) else 0.0
        if atr_i <= 0:
            i += 1
            continue
        if pos is None:
            if up and not ((state[i - 1] == "up") and float(scores[i - 1]) >= l1_thr):
                entry = close[i]
                pos = {"i0": i, "entry": entry, "highest": entry,
                       "stop": entry - mult * atr_i, "cur_sl": None,
                       "peak_r": 0.0, "pyr_batch": 0, "total_scale": 1.0}
            i += 1
            continue
        pos["highest"] = max(pos["highest"], close[i])
        pos["stop"] = max(pos["stop"], pos["highest"] - mult * atr_i)
        r0 = mult * atr_series.iloc[pos["i0"]]
        r_mult = (close[i] - pos["entry"]) / r0
        hold_days = float(i - pos["i0"])
        peak_r = max(pos["peak_r"], r_mult)
        pos["peak_r"] = peak_r
        dd = max(0.0, 1.0 - (1.0 + (close[i] / pos["entry"] - 1)) / (1.0 + peak_r * 0.05)) \
            if peak_r > 0 else 0.0
        d = decide_long(
            l1_state=("up" if up else "down"), close=close[i], stop=pos["stop"],
            new_high=bool(nh[i]), r_multiple=r_mult, in_position=True,
            cur_sl=pos["cur_sl"], peak_r=peak_r, hold_days=hold_days,
            drawdown_pct=dd, pyr_batch=(pos["pyr_batch"] if pyramid_on else 99),
        )
        if d["action"] == "close":
            trades.append({"r": pos["total_scale"] * (close[i] - pos["entry"]) / r0,
                           "hold_days": hold_days, "reason": d["reason"]})
            pos = None
        elif d["action"] == "reduce":
            trades.append({"r": pos["total_scale"] * float(d.get("ratio") or 0.5) * (close[i] - pos["entry"]) / r0,
                           "hold_days": hold_days, "reason": d["reason"]})
            pos["total_scale"] *= (1.0 - float(d.get("ratio") or 0.5))
        elif d["action"] == "add" and d.get("topup"):
            pos["total_scale"] += float(d.get("ratio") or 0.5)
        elif d["action"] == "add":
            pos["pyr_batch"] += 1
            pos["total_scale"] += float(d.get("ratio") or 0.25)
        elif d["action"] == "tighten_sl":
            pos["cur_sl"] = d.get("new_sl")
        i += 1
    if pos is not None:
        trades.append({"r": pos["total_scale"] * (close[n - 1] - pos["entry"]) /
                       (mult * atr_series.iloc[pos["i0"]]),
                       "hold_days": float(n - 1 - pos["i0"]), "reason": "eod"})
    return trades


def metrics(ts):
    rs = np.array([t["r"] for t in ts])
    holds = np.array([t["hold_days"] for t in ts])
    if len(rs) == 0:
        return None
    eq = np.cumsum(rs)
    maxdd = float((np.maximum.accumulate(eq) - eq).max())
    mean_r = float(rs.mean())
    std_r = float(rs.std(ddof=1)) if len(rs) > 1 else 0.0
    avg_hold = float(holds.mean()) if len(holds) else 20.0
    sharpe = mean_r / (std_r + 1e-12) * np.sqrt(365.0 / max(avg_hold, 1.0)) if std_r > 0 else 0.0
    return {"n": len(rs), "total_R": float(eq[-1]), "mean_R": mean_r,
            "win_rate": float((rs > 0).mean()), "avg_hold_d": avg_hold,
            "max_dd_R": maxdd, "sharpe": sharpe}


def main():
    t0 = time.time()
    grid = [(m, th, p) for m in (1.5, 2.0, 2.5, 3.0) for th in (2, 3) for p in (False, True)]
    results = []
    for mult, l1_thr, pyramid in grid:
        all_t = []
        for sym in _SYMS:
            t = run_one(sym, mult, l1_thr, pyramid)
            if t:
                all_t.extend(t)
        m = metrics(all_t)
        if m is None:
            continue
        results.append({"mult": mult, "l1_threshold": l1_thr, "pyramid": pyramid, **m})
        print(f"mult={mult} L1={l1_thr} pyr={int(pyramid)} n={m['n']} total_R={m['total_R']:.1f} "
              f"mean_R={m['mean_R']:.3f} win={m['win_rate']*100:.1f}% hold={m['avg_hold_d']:.1f} "
              f"maxDD={m['max_dd_R']:.2f} sharpe={m['sharpe']:.2f}", flush=True)
    report = {"updated_at": time.time(), "note": "A7 复核：decide_long 同核 + weekly_atr_causal 无前视",
              "results": results, "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join("data", "long_v2_backtest_review.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("WROTE data/long_v2_backtest_review.json", flush=True)


if __name__ == "__main__":
    main()
