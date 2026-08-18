"""长线规则验证器（升级计划 v3.0 S3/M5 · P5）。

把 long_trend_v2 的**入场规则**信号化（L1=up → 0/1 信号序列），跑与公式因子
同口径的诊断（IC/ICIR + walk-forward + DSR/PBO），对参数网格做小规模 PBO 扫描，
产出 data/long_v2_rule_report.json。规则参数调整必须引用本报告（证据审批）。

范围（诚实版）：
- V1 验证入场规则：L1 阈值网格 [2,3,4,5] × 前瞻 [1,2,3]；
- Chandelier ATR 倍数 / 金字塔 R 属持仓管理参数，需持仓模拟器，留待 V2
  （本验证器只读不改实盘参数）。
- 数据源：binance 1d 长历史（research，8-9 年）；实盘仍按实盘所 1d 判市况（分工不变）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_REPORT_PATH = os.path.join("data", "long_v2_rule_report.json")


def _research_1d(symbol: str, limit: int = 3000) -> Optional[np.ndarray]:
    """binance 1d 收盘（research 用途）。返回 close 数组。"""
    try:
        from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer
        rows = FactorBacktestScorer._load_klines(symbol, "1d", limit)
        if not rows or len(rows) < 300:
            return None
        closes = np.array([
            float(r["close"] if isinstance(r, dict) else getattr(r, "close", 0))
            for r in rows
        ])
        return closes
    except Exception as e:
        logger.debug("[LongRuleValidator] 1d 加载失败 %s: %s", symbol, e)
        return None


def _l1_score_series(closes: np.ndarray, lookback: int = 260) -> np.ndarray:
    """用 trend_layer.classify 逐窗滚动 L1 评分（每根 bar 一窗，O(n×classify)）。"""
    import pandas as pd
    from backend.services.trend_layer import classify

    n = len(closes)
    scores = np.full(n, np.nan)
    for i in range(lookback, n):
        df = pd.DataFrame({
            "close": closes[i - lookback + 1: i + 1],
            "high": closes[i - lookback + 1: i + 1],
            "low": closes[i - lookback + 1: i + 1],
        })
        try:
            c = classify(df)
            scores[i] = float(c.get("score") or 0.0)
        except Exception:
            continue
    return scores


def validate_entry_rule(
    symbols: Optional[List[str]] = None,
    l1_grid: Optional[List[int]] = None,
    fwd_grid: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """L1 阈值 × 前瞻网格 → 每组合 IC/ICIR/OOS Sharpe/DSR/PBO。"""
    symbols = [str(s or "").upper() for s in (symbols or ["BTC", "ETH", "SOL"])]
    l1_grid = l1_grid or [2, 3, 4, 5]
    fwd_grid = fwd_grid or [1, 2, 3]
    from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer
    scorer = FactorBacktestScorer()

    closes_by_sym: Dict[str, np.ndarray] = {}
    l1_by_sym: Dict[str, np.ndarray] = {}
    for sym in symbols:
        closes = _research_1d(sym)
        if closes is None:
            continue
        closes_by_sym[sym] = closes
        l1_by_sym[sym] = _l1_score_series(closes)

    results: List[Dict[str, Any]] = []
    for l1_thr in l1_grid:
        for fwd in fwd_grid:
            ic_list: List[float] = []
            icir_list: List[float] = []
            net_list: List[float] = []
            sharpe_list: List[float] = []
            trades_total = 0
            for sym in l1_by_sym:
                closes = closes_by_sym[sym]
                sig = (l1_by_sym[sym] >= l1_thr).astype(float)
                n = len(closes)
                fwd_ret = np.full(n, np.nan)
                fwd_ret[:-fwd] = (closes[fwd:] - closes[:-fwd]) / closes[:-fwd]
                m = np.isfinite(sig) & np.isfinite(fwd_ret)
                if int(m.sum()) < 60 or np.std(sig[m]) < 1e-12:
                    continue
                ic = float(np.corrcoef(sig[m], fwd_ret[m])[0, 1])
                if np.isfinite(ic):
                    ic_list.append(ic)
                bt = scorer._walk_forward_backtest(sig, closes, fwd, cost=0.0021)
                if bt["trades"] > 0:
                    net_list.append(bt["net_return"])
                    sharpe_list.append(bt["sharpe"])
                    trades_total += bt["trades"]
            ic_mean = float(np.mean(ic_list)) if ic_list else 0.0
            icir = float(np.mean(icir_list)) if icir_list else 0.0
            oos_sharpe = float(np.mean(sharpe_list)) if sharpe_list else 0.0
            # DSR/PBO（与因子同口径：跨币 ICIR 样本 + n_trials = 网格组合总数）
            dsr_ok, pbo = False, 1.0
            if len(ic_list) >= 2:
                try:
                    from backend.services.factor_engine.dsr_pbo import compute_dsr_pbo_for_factors
                    r = compute_dsr_pbo_for_factors(
                        icir_list=list(ic_list),
                        n_total_candidates=max(len(l1_grid) * len(fwd_grid), 1),
                        sample_len=max(int(np.mean([len(v) for v in l1_by_sym.values()])), 50),
                    )
                    _pbo_r = r.get("pbo_result") or {}
                    if not bool(_pbo_r.get("indeterminate")):
                        dsr_sig = bool((r.get("dsr_result") or {}).get("significant", False))
                        pbo = float(_pbo_r.get("pbo", 1.0))
                        dsr_ok = bool(dsr_sig and pbo <= 0.5)
                except Exception as e:
                    logger.debug("[LongRuleValidator] DSR/PBO 失败: %s", e)
            results.append({
                "l1_threshold": int(l1_thr),
                "fwd_bars": int(fwd),
                "ic_mean": round(ic_mean, 4),
                "icir": round(icir, 4),
                "oos_sharpe": round(oos_sharpe, 4),
                "oos_trades": int(trades_total),
                "dsr_ok": bool(dsr_ok),
                "pbo": round(float(pbo), 4),
                "n_symbols": len(ic_list),
            })

    report = {"updated_at": time.time(), "symbols": symbols, "results": results}
    try:
        os.makedirs(os.path.dirname(_REPORT_PATH) or ".", exist_ok=True)
        with open(_REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info("[LongRuleValidator] 规则验证报告写入 %s（%d 组合）", _REPORT_PATH, len(results))
    except Exception as e:
        logger.warning("[LongRuleValidator] 报告落盘失败: %s", e)
    return report


if __name__ == "__main__":
    t0 = time.time()
    rep = validate_entry_rule()
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:3000])
    print(f"elapsed={time.time()-t0:.1f}s")
