# -*- coding: utf-8 -*-
"""逐币择时边际的持有期扫描（正确计换手成本）。

## 为什么需要

`_walk_forward_backtest` 的 `net_return` 是**累计和**，且成本按每次仓位变化计
（`cost * turn/2`）。直接把它除以 `trades` 会**高估每笔成本**——因为连续同向持仓
的 `turn=0`，并不付费。本脚本按逐笔明细重算：

  - 每笔毛收益 = pos × 前瞻收益
  - 每笔成本 = cost × |Δpos| / 2（真实换手）
  - 输出每笔毛/净（bp）、真实平均换手、以及**盈亏平衡所需的最低毛收益**

用法：
    .venv\\Scripts\\python.exe backend/scripts/scan_holding_period.py
输出：控制台表格 + `data/holding_period_scan.json`
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "holding_period_scan.json"

FORMULAS = {
    "ts_rank_dev": "-1 * (ts_rank(close, 20) - 0.5)",
    "mom_skip_rev": "-1 * (close / delay(close, 5) - 1)",
    "vwap_dev_rev": "-1 * (close - vwap) / close",
    "wick_upper_fade": "-1 * (upper_wick / close)",
    "rev10": "-1 * (close / delay(close, 10) - 1)",
}
HORIZONS = (6, 12, 24, 42, 84, 168)  # 4h bars: 1d / 2d / 4d / 7d / 14d / 28d


def _wf_detail(factor_vals, closes, fwd, cost):
    """复刻 _walk_forward_backtest 的 3 折逻辑，但返回逐笔明细。"""
    n = len(closes)
    fwd_ret = np.full(n, np.nan)
    if n > fwd:
        fwd_ret[: n - fwd] = (closes[fwd:] - closes[: n - fwd]) / closes[: n - fwd]
    f = factor_vals.copy()
    mask = np.isfinite(f) & np.isfinite(fwd_ret)
    idx = np.where(mask)[0]
    if len(idx) < 60:
        return []
    folds = 3
    seg = len(idx) // folds
    rows = []
    for k in range(1, folds):
        train_idx = idx[(k - 1) * seg: k * seg]
        test_idx = idx[k * seg: (k + 1) * seg] if k < folds - 1 else idx[k * seg:]
        if len(train_idx) < 20 or len(test_idx) < 10:
            continue
        tf, tr = f[train_idx], fwd_ret[train_idx]
        if np.std(tf) < 1e-12 or np.std(tr) < 1e-12:
            continue
        ic = float(np.corrcoef(tf, tr)[0, 1])
        orient = 1.0 if ic >= 0 else -1.0
        mu, sd = np.mean(tf), np.std(tf)
        if sd < 1e-12:
            continue
        prev = 0.0
        for t in test_idx[::max(1, fwd)]:
            r = fwd_ret[t]
            if not np.isfinite(r):
                continue
            pos = np.sign((f[t] - mu) / sd) * orient
            gross = float(pos * r)
            turn = abs(pos - prev)
            c = cost * (turn / 2.0)
            rows.append({"gross": gross, "cost": c, "turn": turn})
            prev = pos
    return rows


def main() -> int:
    from backend.services.factor_engine.factor_backtest_scorer import (
        FactorBacktestScorer,
        resolve_roundtrip_cost,
    )

    s = FactorBacktestScorer()
    cost = resolve_roundtrip_cost(0.0009)
    syms = [x.strip() for x in (os.getenv("FACTOR_SCORER_SYMBOLS") or "").split(",") if x.strip()][:20]
    print(f"往返成本 {cost*10000:.1f}bp | 面板 {len(syms)} 币 | 周期 4h")
    print(f"\n{'因子':<16}{'fwd':>5}{'持有':>7}{'n':>7}{'毛bp':>9}{'成本bp':>9}{'净bp':>9}{'换手':>7}{'胜率':>7}")

    out = []
    for name, formula in FORMULAS.items():
        for fwd in HORIZONS:
            gross_all, cost_all, turns, wins = [], [], [], []
            for sym in syms:
                kl = s._load_klines(sym, "4h", 2400)
                if not kl:
                    continue
                arrays, _ts = s._to_arrays(kl)
                if arrays is None:
                    continue
                fv = s._eval_formula(formula, arrays)
                if fv is None:
                    continue
                for row in _wf_detail(fv, arrays["close"], fwd, cost):
                    gross_all.append(row["gross"])
                    cost_all.append(row["cost"])
                    turns.append(row["turn"])
                    wins.append(1 if row["gross"] - row["cost"] > 0 else 0)
            n = len(gross_all)
            if n == 0:
                continue
            g = float(np.mean(gross_all)) * 10000
            c = float(np.mean(cost_all)) * 10000
            net = g - c
            turn = float(np.mean(turns))
            wr = float(np.mean(wins))
            hold_h = fwd * 4
            rec = {"factor": name, "fwd_bars": fwd, "hold_hours": hold_h, "n": n,
                   "gross_bp": round(g, 2), "cost_bp": round(c, 2), "net_bp": round(net, 2),
                   "avg_turnover": round(turn, 3), "win_rate": round(wr, 3)}
            out.append(rec)
            print(f"{name:<16}{fwd:>5}{hold_h:>6}h{n:>7}{g:>9.2f}{c:>9.2f}{net:>9.2f}{turn:>7.2f}{wr:>7.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roundtrip_cost_bp": round(cost * 10000, 2),
        "symbols": syms,
        "rows": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")

    pos = [r for r in out if r["net_bp"] > 0]
    print(f"\n净边际 > 0 的组合: {len(pos)} / {len(out)}")
    for r in sorted(pos, key=lambda x: -x["net_bp"])[:10]:
        print(f"  ✅ {r['factor']} 持有{r['hold_hours']}h: 毛{r['gross_bp']}bp 成本{r['cost_bp']}bp "
              f"净{r['net_bp']}bp 换手{r['avg_turnover']} 胜率{r['win_rate']} n={r['n']}")
    if not pos:
        print("[WARN] 全部为负 —— 逐币择时路径在 4h 周期上无可用边际。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
