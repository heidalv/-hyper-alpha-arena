# -*- coding: utf-8 -*-
"""新种子族评估器（第四轮）：把「从未进入存活因子的字段」写成候选种子，
用**真实打分链**（walk-forward + IC/ICIR + OOS 费后净收益 + DSR/PBO）评估，
只登记通过的候选。

## 背景

`backend/scripts/audit_factor_library_health.py` 实测：19 个存活因子的 `expr_ast`
只用到 **3 个基础字段**（close/returns/volume）和 **8 个算子**，而表达式语言支持
**17 个字段 / 31 个算子**。`vwap` / `upper_wick` / `lower_wick` / `body` /
`wick_ratio` / `amount` / `turnover` 全部可用却从未进入任何存活因子
——探索空间是被**种子池人为收窄**的，MCTS 只能在同一族里变异。

本脚本补上"候选池多样性"这一环，但**不做占位种子**：
每个候选都走 `FactorBacktestScorer.score_formula`（与晋升同一引擎），
只把通过 `admitted` 的候选写进 `custom_factor_store`（幂等 register_reference）。

## 用法

    # 只评估不写库（默认）
    .venv\\Scripts\\python.exe backend/scripts/seed_factor_families.py --dry-run
    # 评估并登记通过者
    .venv\\Scripts\\python.exe backend/scripts/seed_factor_families.py --apply

输出：控制台表格 + `data/seed_factor_eval.json`
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "seed_factor_eval.json"

# ── 候选种子：按「未使用字段 × 家族」组织 ──
# 命名 seed2_<家族>_<字段>_<窗口>；公式用打分器命名空间（OHLCV + vwap/wick/body/returns）
CANDIDATES: list[dict] = [
    # A. 影线 / 插针形态（此前 DSL 不可表达 → 从未挖到）
    {"id": "seed2_wick_upper_fade_4h", "family": "wick", "interval": "4h",
     "formula": "-1 * (upper_wick / close)"},
    {"id": "seed2_wick_lower_bounce_4h", "family": "wick", "interval": "4h",
     "formula": "lower_wick / close"},
    {"id": "seed2_wick_ratio_rev_4h", "family": "wick", "interval": "4h",
     "formula": "-1 * wick_ratio"},
    {"id": "seed2_body_ratio_rev_4h", "family": "body", "interval": "4h",
     "formula": "-1 * (body / close)"},
    {"id": "seed2_body_wick_discord_4h", "family": "body", "interval": "4h",
     "formula": "(body - upper_wick - lower_wick) / close"},
    # B. VWAP 偏离（vwap 派生字段，此前从未使用）
    {"id": "seed2_vwap_dev_rev_4h", "family": "vwap", "interval": "4h",
     "formula": "-1 * (close - vwap) / close"},
    {"id": "seed2_vwap_dev_z_4h", "family": "vwap", "interval": "4h",
     "formula": "-1 * (close - vwap) / (ts_std(close, 20) + 1e-9)"},
    {"id": "seed2_vwap_slope_4h", "family": "vwap", "interval": "4h",
     "formula": "delta(vwap, 5) / close"},
    # C. 量价结构（volume 用过，但只做 std/mean/corr；此处换结构口径）
    {"id": "seed2_vol_zscore_rev_4h", "family": "volume", "interval": "4h",
     "formula": "-1 * (volume - ts_mean(volume, 20)) / (ts_std(volume, 20) + 1e-9)"},
    {"id": "seed2_vol_price_div_4h", "family": "volume", "interval": "4h",
     "formula": "ts_corr(close, volume, 20)"},
    {"id": "seed2_range_expansion_4h", "family": "volatility", "interval": "4h",
     "formula": "-1 * (high - low) / close"},
    {"id": "seed2_true_range_ratio_4h", "family": "volatility", "interval": "4h",
     "formula": "ts_std(returns, 10) / (ts_std(returns, 50) + 1e-9)"},
    # D. 动量 / 反转的非均值口径（打破 -mean(returns,N) 同质族）
    {"id": "seed2_mom_skip_rev_4h", "family": "momentum", "interval": "4h",
     "formula": "-1 * (close / delay(close, 5) - 1)"},
    {"id": "seed2_mom_accel_4h", "family": "momentum", "interval": "4h",
     "formula": "(close / delay(close, 5) - 1) - (delay(close, 5) / delay(close, 10) - 1)"},
    {"id": "seed2_ts_rank_dev_4h", "family": "momentum", "interval": "4h",
     "formula": "-1 * (ts_rank(close, 20) - 0.5)"},
    # E. 日线同族（中长线决策周期对齐）
    {"id": "seed2_wick_upper_fade_1d", "family": "wick", "interval": "1d",
     "formula": "-1 * (upper_wick / close)"},
    {"id": "seed2_vwap_dev_rev_1d", "family": "vwap", "interval": "1d",
     "formula": "-1 * (close - vwap) / close"},
    {"id": "seed2_range_expansion_1d", "family": "volatility", "interval": "1d",
     "formula": "-1 * (high - low) / close"},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="把通过候选登记到 custom_factor_store")
    ap.add_argument("--interval", default="", help="只评估指定周期（4h/1d）")
    args = ap.parse_args()

    from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer

    scorer = FactorBacktestScorer()
    rows = []
    print(f"{'候选':<32}{'周期':<5}{'IC':>9}{'ICIR':>8}{'Sharpe':>9}{'OOS净':>10}{'笔数':>6}{'admitted':>10}")
    for cand in CANDIDATES:
        if args.interval and cand["interval"] != args.interval:
            continue
        try:
            r = scorer.score_formula(
                cand["id"], cand["formula"],
                interval=cand["interval"],
                count_trial=True,
            )
        except Exception as exc:
            print(f"{cand['id']:<32}{cand['interval']:<5}  ERROR {str(exc)[:60]}")
            rows.append({**cand, "error": str(exc)[:200]})
            continue
        rows.append({
            **cand,
            "grade": r.grade, "admitted": bool(r.admitted),
            "ic_mean": round(float(r.ic_mean or 0), 4),
            "icir": round(float(r.icir or 0), 4),
            "oos_sharpe": round(float(r.oos_sharpe or 0), 4),
            "oos_net_return": round(float(r.oos_net_return or 0), 6),
            "oos_trades": int(r.oos_trades or 0),
            "lag1_net_return": round(float(r.lag1_net_return or 0), 6),
            "reason": str(r.reason or "")[:200],
        })
        print(f"{cand['id']:<32}{cand['interval']:<5}{r.ic_mean:>9.4f}{r.icir:>8.3f}"
              f"{r.oos_sharpe:>9.3f}{r.oos_net_return:>10.5f}{r.oos_trades:>6}{str(r.admitted):>10}")

    passed = [r for r in rows if r.get("admitted")]
    print(f"\n通过 {len(passed)} / {len(rows)}")
    for r in passed:
        print(f"  ✅ {r['id']}  IC={r['ic_mean']} ICIR={r['icir']} Sharpe={r['oos_sharpe']} "
              f"OOS净={r['oos_net_return']}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scorer": "FactorBacktestScorer.score_formula",
        "n_candidates": len(rows),
        "n_passed": len(passed),
        "rows": rows,
    }

    if args.apply and passed:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        registered = 0
        for r in passed:
            try:
                res = custom_factor_store.register_reference(
                    r["id"], registry_factor_id=r["id"],
                    horizon="midlong", timeframe=r["interval"],
                )
                if res.get("ok"):
                    registered += 1
            except Exception as exc:
                print(f"  登记失败 {r['id']}: {str(exc)[:80]}")
        report["registered"] = registered
        print(f"已登记 {registered} 个通过候选（custom_factor_store，幂等）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"已写入 {OUT}")

    if not passed:
        print("[WARN] 无候选通过晋升门 —— 这是诚实结果：新字段族在当前 9 币 / 4h·1d 样本上"
              "没有统计边际。继续扩 symbol/周期，而不是降低门槛。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
