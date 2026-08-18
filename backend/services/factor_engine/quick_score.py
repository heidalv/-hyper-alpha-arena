"""因子工厂「写公式 → 秒级反馈」（升级计划 v3.0 S3/R4 · 对标 WorldQuant WebSim）。

只读评估：parse + audit → 逐币求值 + 中性化 → IC/ICIR/衰减/换手 + 与 active 集
最大 |corr| + 门禁通过/拒绝预览。不注册、不晋升、不登记试验计数。
目标时延 <3s（GPU 面板单树 ≈15ms + 诊断毫秒级）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


def _cfg(name: str, default):
    from backend.config import settings as _s
    return getattr(_s, name, default)


def quick_score(ast: dict, tier: str = "midlong") -> Dict[str, Any]:
    """公式 AST → 秒级诊断 + 门禁预览。"""
    from backend.services.factor_engine.expr.parser import parse
    from backend.services.factor_engine.expr.audit import audit
    from backend.services.factor_engine.factor_backtest_scorer import (
        FactorBacktestScorer,
        midlong_lookback_for,
        midlong_min_bars_for,
        _period_fwd_bars,
    )

    t0 = time.perf_counter()
    tier = str(tier or "midlong").strip().lower()
    if tier in ("scalp", "short", "1h"):
        interval = "1h"
        lookback = int(_cfg("FACTOR_SCORER_LOOKBACK_BARS", 720))
        min_bars = int(_cfg("FACTOR_SCORER_SCALP_MIN_BARS", 500))
        fwd = _period_fwd_bars("1h")
        min_sharpe = float(_cfg("FACTOR_SCORER_MIN_SHARPE", 0.5))
    else:
        interval = "4h"
        lookback = midlong_lookback_for("4h")
        min_bars = midlong_min_bars_for("4h")
        fwd = int(_cfg("FACTOR_SCORER_MIDLONG_FWD_4H", 6))
        min_sharpe = float(_cfg("FACTOR_SCORER_MIDLONG_MIN_SHARPE", 0.4))

    a = audit(ast)
    if not a.ok:
        return {"ok": False, "error": "审计失败：" + "; ".join(a.errors), "elapsed_ms": int((time.perf_counter() - t0) * 1000)}
    try:
        expr = parse(ast)
    except Exception as e:
        return {"ok": False, "error": f"解析失败: {e}", "elapsed_ms": int((time.perf_counter() - t0) * 1000)}

    syms = [s.strip().upper() for s in str(_cfg("FACTOR_SCORER_SYMBOLS", "BTC,ETH,SOL")).split(",") if s.strip()][:9]
    scorer = FactorBacktestScorer()
    panels: Dict[str, tuple] = {}
    factor_by_sym: Dict[str, np.ndarray] = {}
    for sym in syms:
        try:
            klines = scorer._load_klines(sym, interval, lookback)
            if not klines or len(klines) < min_bars:
                continue
            arrays, ts = scorer._to_arrays(klines)
            if arrays is None:
                continue
            fv = np.asarray(expr.evaluate(arrays), dtype=float).reshape(-1)
            if np.isfinite(fv).sum() < 60:
                continue
            factor_by_sym[sym] = fv
            panels[sym] = (ts, arrays["close"])
        except Exception:
            continue
    if len(panels) < 3:
        return {"ok": False, "error": f"有效币种不足（{len(panels)}<3）", "elapsed_ms": int((time.perf_counter() - t0) * 1000)}

    # 中性化收益（与门禁同口径）
    neutral: Dict[str, np.ndarray] = {}
    try:
        from backend.services.factor_engine.neutralization import build_neutralized_returns
        neutral = build_neutralized_returns(panels, fwd)
    except Exception:
        neutral = {}

    from backend.services.factor_engine.factor_evaluator import get_factor_evaluator
    evaluator = get_factor_evaluator(forward_period=fwd)
    ic_list, icir_list, decay_list, turn_list = [], [], [], []
    import pandas as pd
    for sym in panels:
        try:
            fv = factor_by_sym[sym]
            nr = pd.Series(neutral[sym], index=np.arange(len(fv))) if sym in neutral else None
            rep = evaluator.evaluate_factor(
                f"quick_{sym}", pd.Series(fv), pd.Series(panels[sym][1]),
                forward_period=fwd, neutral_returns=nr,
            )
            if rep.data_points >= 30:
                ic_list.append(rep.ic_mean)
                icir_list.append(rep.icir)
                decay_list.append(rep.ic_decay_halflife)
                turn_list.append(rep.turnover)
        except Exception:
            continue
    if not ic_list:
        return {"ok": False, "error": "有效 IC 样本不足", "elapsed_ms": int((time.perf_counter() - t0) * 1000)}

    # 与 active 集最大相关（只读）
    max_corr = 0.0
    try:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        from backend.services.factor_engine.factor_backtest_scorer import _resolve_admin_tenant
        active = custom_factor_store.list_active(tenant_id=_resolve_admin_tenant()) or []
        cand = factor_by_sym[next(iter(factor_by_sym))]
        for rec in active:
            try:
                _f = rec.get("formula") or ""
                if not _f:
                    continue
                a2 = scorer._to_arrays(scorer._load_klines(next(iter(factor_by_sym)), interval, lookback) or [])
                if a2 is None:
                    continue
                av = scorer._eval_formula(_f, a2[0])
                if av is None:
                    continue
                m = np.isfinite(cand) & np.isfinite(av)
                if m.sum() < 30:
                    continue
                c = abs(float(np.corrcoef(cand[m], av[m])[0, 1]))
                if np.isfinite(c):
                    max_corr = max(max_corr, c)
            except Exception:
                continue
    except Exception:
        pass

    ic_mean = float(np.mean(ic_list))
    icir = float(np.mean(icir_list))
    # 门禁预览（与 score_formula 同阈值，只读）
    preview = {
        "ic_ok": abs(ic_mean) >= 0.03,
        "icir_ok": abs(icir) > 0.3,
        "redundant": max_corr >= float(_cfg("FACTOR_SCORER_REDUNDANCY_CORR", 0.7)),
        "min_sharpe": min_sharpe,
        "note": "预览口径与正式门禁同一评分函数；DSR/PBO 与样本外回测需正式打分确认",
    }
    return {
        "ok": True,
        "ic_mean": round(ic_mean, 4),
        "icir": round(icir, 4),
        "ic_decay_halflife": int(np.mean(decay_list)) if decay_list else 0,
        "turnover": round(float(np.mean(turn_list)), 4) if turn_list else 0.0,
        "max_corr_with_active": round(max_corr, 4),
        "n_symbols": len(ic_list),
        "preview": preview,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }
