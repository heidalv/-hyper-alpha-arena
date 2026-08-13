"""K线因子候选参数邻域稳健性扫描（防参数孤岛）。

对候选（AAVE 4h meanrev / AVAX 1h meanrev / LTC 1h meanrev / XRP 1h hybrid）
逐个扰动 threshold / z_window / max_hold / SL-TP，统计每个配置：
- n / 每笔净收益 / PF / t / permutation p
- 前后半段 PF

判定：
- promising：PF>=1.0 且前段 PF>=0.95 且后段 PF>=0.95 且 n>=100
- robust：promising 且 t>1.0

用法（仓库根目录）：
    python scripts/sweep_kline_factors.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]

EXCHANGE = "asterdex"

CANDIDATES: List[Dict[str, Any]] = [
    {
        "name": "AAVE_4h_meanrev",
        "symbol": "AAVE", "period": "4h", "days": 365, "factor_set": "meanrev",
        "threshold": 0.5, "z_window": 120, "max_hold": 6,
        "sl": 0.02, "tp": 0.04,
        "cooldown": 2,
        "max_holds": [4, 5, 6, 8],
        "sl_tp_pairs": [(0.015, 0.03), (0.02, 0.04), (0.03, 0.06)],
    },
    {
        "name": "AVAX_1h_meanrev",
        "symbol": "AVAX", "period": "1h", "days": 180, "factor_set": "meanrev",
        "threshold": 0.5, "z_window": 120, "max_hold": 12,
        "sl": 0.01, "tp": 0.02,
        "cooldown": 3,
        "max_holds": [8, 10, 12, 16],
        "sl_tp_pairs": [(0.008, 0.016), (0.01, 0.02), (0.015, 0.03)],
    },
    {
        "name": "LTC_1h_meanrev",
        "symbol": "LTC", "period": "1h", "days": 180, "factor_set": "meanrev",
        "threshold": 0.5, "z_window": 120, "max_hold": 12,
        "sl": 0.01, "tp": 0.02,
        "cooldown": 3,
        "max_holds": [8, 10, 12, 16],
        "sl_tp_pairs": [(0.008, 0.016), (0.01, 0.02), (0.015, 0.03)],
    },
    {
        "name": "XRP_1h_hybrid",
        "symbol": "XRP", "period": "1h", "days": 180, "factor_set": "hybrid",
        "threshold": 0.5, "z_window": 120, "max_hold": 12,
        "sl": 0.01, "tp": 0.02,
        "cooldown": 3,
        "max_holds": [8, 10, 12, 16],
        "sl_tp_pairs": [(0.008, 0.016), (0.01, 0.02), (0.015, 0.03)],
    },
]

THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7]
Z_WINDOWS = [90, 120, 160, 200]


def _configs(c: Dict[str, Any]) -> List[Tuple[float, int, int, float, float]]:
    out = set()
    for thr in THRESHOLDS:
        out.add((thr, c["z_window"], c["max_hold"], c["sl"], c["tp"]))
    for z in Z_WINDOWS:
        out.add((c["threshold"], z, c["max_hold"], c["sl"], c["tp"]))
    for mh in c["max_holds"]:
        out.add((c["threshold"], c["z_window"], mh, c["sl"], c["tp"]))
    for sl, tp in c["sl_tp_pairs"]:
        out.add((c["threshold"], c["z_window"], c["max_hold"], sl, tp))
    return sorted(out)


def _verdict(res: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    total = res.get("total") or {}
    older = res.get("older_half") or {}
    newer = res.get("newer_half") or {}
    n = int(total.get("n", 0) or 0)
    pf = total.get("profit_factor")
    t = total.get("t_stat")
    older_pf = older.get("profit_factor")
    newer_pf = newer.get("profit_factor")
    promising = (
        n >= 100
        and pf is not None and pf >= 1.0
        and older_pf is not None and older_pf >= 0.95
        and newer_pf is not None and newer_pf >= 0.95
    )
    robust = promising and t is not None and t > 1.0
    return ("robust" if robust else "promising" if promising else "no"), {
        "n": n,
        "avg_net_ret": total.get("avg_net_ret"),
        "profit_factor": pf,
        "t_stat": t,
        "permutation_p": total.get("permutation_p"),
        "older_pf": older_pf,
        "newer_pf": newer_pf,
    }


def main() -> int:
    from backend.services.scalp.kline_factor_backtest import run_kline_factor_backtest

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exchange": EXCHANGE,
        "scenario": "realistic",
        "candidates": {},
    }
    for c in CANDIDATES:
        cfg_list = _configs(c)
        rows: List[Dict[str, Any]] = []
        n_promising = 0
        n_robust = 0
        for thr, z, mh, sl, tp in cfg_list:
            res = run_kline_factor_backtest(
                symbols=[c["symbol"]],
                days=c["days"],
                exchange=EXCHANGE,
                period=c["period"],
                threshold=thr,
                sl_pct=sl,
                tp_pct=tp,
                max_hold_candles=mh,
                z_window=z,
                cooldown_candles=c["cooldown"],
                n_perm=100,
                scenario="realistic",
                factor_set=c["factor_set"],
                save_report=False,
            )
            verdict, stats = _verdict(res)
            if verdict == "promising":
                n_promising += 1
            if verdict == "robust":
                n_robust += 1
            rows.append({
                "threshold": thr, "z_window": z, "max_hold_candles": mh,
                "sl_pct": sl, "tp_pct": tp,
                "verdict": verdict,
                **stats,
            })
        rows.sort(key=lambda r: (r["verdict"] != "robust", r["verdict"] != "promising",
                                 -(r.get("profit_factor") or 0)))
        report["candidates"][c["name"]] = {
            "symbol": c["symbol"], "period": c["period"], "factor_set": c["factor_set"],
            "n_configs": len(cfg_list), "n_promising": n_promising, "n_robust": n_robust,
            "rows": rows,
        }
        print(
            "%s: configs=%d promising=%d robust=%d"
            % (c["name"], len(cfg_list), n_promising, n_robust)
        )

    out_dir = ROOT / "reports" / "scalp_backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (
        "kline_factor_robustness_%s.json" % datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("报告已保存:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
