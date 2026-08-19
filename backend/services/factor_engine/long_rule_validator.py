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


def _research_ohlc(symbol: str, limit: int = 3000):
    """binance 1d OHLC（research 用途）。返回 (close, high, low) 数组。

    [A6 修复] 此前只用 close 且把 close 冒充 high/low 喂给 classify——
    ADX 动能信号（依赖真实 high/low 的 true range）被破坏，验证结论失真。
    """
    try:
        from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer
        rows = FactorBacktestScorer._load_klines(symbol, "1d", limit)
        if not rows or len(rows) < 300:
            return None

        def _col(name):
            return np.array([
                float(r[name] if isinstance(r, dict) else getattr(r, name, 0))
                for r in rows
            ])
        return _col("close"), _col("high"), _col("low")
    except Exception as e:
        logger.debug("[LongRuleValidator] 1d 加载失败 %s: %s", symbol, e)
        return None


def _l1_score_series(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                     lookback: int = 260) -> np.ndarray:
    """用 trend_layer.classify 逐窗滚动 L1 评分（真实 OHLC，每根 bar 一窗）。"""
    import pandas as pd
    from backend.services.trend_layer import classify

    n = len(closes)
    scores = np.full(n, np.nan)
    for i in range(lookback, n):
        df = pd.DataFrame({
            "close": closes[i - lookback + 1: i + 1],
            "high": highs[i - lookback + 1: i + 1],
            "low": lows[i - lookback + 1: i + 1],
        })
        try:
            c = classify(df)
            scores[i] = float(c.get("score") or 0.0)
        except Exception:
            continue
    return scores


def _single_side_backtest(sig: np.ndarray, closes: np.ndarray, fwd: int,
                          cost: float = 0.0021) -> Dict[str, Any]:
    """[A6 修复] 单边口径回测：信号=1 持有 fwd 根、信号=0 空仓（不做空）。

    与因子口径 _walk_forward_backtest 的差别：后者 pos=sign(z)×orient 在信号=0 时
    仍满仓做空——对「L1=up 多头单边」策略是测错对象（加密长偏下双边必然亏）。
    本函数 3 折 walk-forward、非重叠采样（每 fwd 根调仓一次）、仓位 0/1。
    """
    n = len(closes)
    fwd_ret = np.full(n, np.nan)
    fwd_ret[:-fwd] = (closes[fwd:] - closes[:-fwd]) / closes[:-fwd]
    m = np.isfinite(sig) & np.isfinite(fwd_ret)
    idx = np.where(m)[0]
    if len(idx) < 60:
        return {"net_return": 0.0, "sharpe": 0.0, "win_rate": 0.0, "trades": 0}

    folds = 3
    seg = len(idx) // folds
    oos_returns = []
    for k in range(1, folds):
        train_idx = idx[(k - 1) * seg: k * seg]
        test_idx = idx[k * seg: (k + 1) * seg] if k < folds - 1 else idx[k * seg:]
        if len(train_idx) < 20 or len(test_idx) < 10:
            continue
        tr_ic = float(np.corrcoef(sig[train_idx], fwd_ret[train_idx])[0, 1])
        orient = 1.0 if tr_ic >= 0 else -1.0
        sample = test_idx[::max(1, fwd)]
        prev_pos = 0.0
        for t in sample:
            pos = float(sig[t]) * orient if float(sig[t]) > 0 else 0.0  # 信号=0 空仓
            r = fwd_ret[t]
            if not np.isfinite(r):
                continue
            gross = pos * r
            turn = abs(pos - prev_pos)
            trade_cost = cost * (turn / 2.0)
            oos_returns.append(float(gross - trade_cost))
            prev_pos = pos

    if not oos_returns:
        return {"net_return": 0.0, "sharpe": 0.0, "win_rate": 0.0, "trades": 0}
    arr = np.array(oos_returns)
    mean_r = float(np.mean(arr))
    std_r = float(np.std(arr))
    sharpe = float(mean_r / (std_r + 1e-12) * np.sqrt(min(len(arr), 252 / max(fwd, 1))))
    return {
        "net_return": round(float(np.sum(arr)), 6),
        "sharpe": round(sharpe, 4),
        "win_rate": round(float(np.mean(arr > 0)), 4),
        "trades": len(arr),
    }


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
        ohlc = _research_ohlc(sym)
        if ohlc is None:
            continue
        closes, highs, lows = ohlc
        closes_by_sym[sym] = closes
        l1_by_sym[sym] = _l1_score_series(closes, highs, lows)

    results: List[Dict[str, Any]] = []
    combo_icirs: List[float] = []  # [A6] 每组合一个跨币 ICIR，供最终 DSR/PBO
    for l1_thr in l1_grid:
        for fwd in fwd_grid:
            ic_list: List[float] = []
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
                # [A6] 单边回测：信号=0 空仓（不再用因子口径的隐式做空）
                bt = _single_side_backtest(sig, closes, fwd, cost=0.0021)
                if bt["trades"] > 0:
                    net_list.append(bt["net_return"])
                    sharpe_list.append(bt["sharpe"])
                    trades_total += bt["trades"]
            ic_mean = float(np.mean(ic_list)) if ic_list else 0.0
            # [A6] 组合级 ICIR = 跨币 IC 的 mean/std（此前 icir_list 从不填充恒为 0）
            icir = 0.0
            if len(ic_list) >= 2:
                _ic_std = float(np.std(ic_list))
                if _ic_std > 1e-12:
                    icir = float(np.mean(ic_list)) / _ic_std
            combo_icirs.append(icir)
            oos_sharpe = float(np.mean(sharpe_list)) if sharpe_list else 0.0
            # 单组合先记占位，全部组合跑完后统一 DSR/PBO（见下方收尾段）
            dsr_ok, pbo = False, 1.0
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

    # [A6] 全部组合跑完后统一 DSR/PBO：样本 = 12 个组合的跨币 ICIR
    if len(combo_icirs) >= 2:
        try:
            from backend.services.factor_engine.dsr_pbo import compute_dsr_pbo_for_factors
            r = compute_dsr_pbo_for_factors(
                icir_list=list(combo_icirs),
                n_total_candidates=max(len(l1_grid) * len(fwd_grid), 1),
                sample_len=max(int(np.mean([len(v) for v in l1_by_sym.values()])), 50),
            )
            _pbo_r = r.get("pbo_result") or {}
            if not bool(_pbo_r.get("indeterminate")):
                dsr_sig = bool((r.get("dsr_result") or {}).get("significant", False))
                pbo = float(_pbo_r.get("pbo", 1.0))
                dsr_ok = bool(dsr_sig and pbo <= 0.5)
            for _res in results:
                _res["dsr_ok"] = bool(dsr_ok)
                _res["pbo"] = round(float(pbo), 4)
            logger.info("[LongRuleValidator] 组合级 DSR/PBO: dsr_ok=%s pbo=%.3f (n=%d)",
                        dsr_ok, pbo, len(combo_icirs))
        except Exception as e:
            logger.debug("[LongRuleValidator] 组合级 DSR/PBO 失败: %s", e)

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
