"""
因子报告卡（factor card）— v6 阶段 2（S2-4）报告卡落库。

把因子评估从"IC/ICIR 两指标"升级为完整报告卡 JSON（L274/L287）：
    - ic        ：跨品种 IC 均值 / ICIR / 显著性（单边 t 检验 p）
    - quantile  ：分层回测（5 分位净值、多头/空头/多空收益、单调性、多头最大回撤）
    - decay     ：IC 半衰期 + 滚动窗口衰退检验（trend_slope/前后半段/neg_streak）
    - turnover  ：平均换手
    - parsimony ：AST 节点数 + 复杂度惩罚（公式膨胀控制）
    - data_quality：数据完整率（缺失比例进报告卡，L101）
    - admission ：admission_gate 判定（5.3.3，对齐 WorldQuant BRAIN 提交门槛）

输出 JSON 安全（numpy 标量全部转原生 float/int），单项失败容错降级为 None，
绝不因报告卡生成异常阻塞主评估流程。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CARD_VERSION = "1.0.0"


def _safe_float(v) -> Optional[float]:
    """numpy 标量 → 原生 float；非有限值 → None（JSON 安全）。"""
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _node_count(ast: Any) -> int:
    """统计 AST 节点数（parsimony 复杂度）。"""
    if not isinstance(ast, dict):
        return 1
    return 1 + sum(_node_count(a) for a in ast.get("args", []) if isinstance(a, dict))


def _forward_returns(close: np.ndarray, horizon: int) -> np.ndarray:
    """前向收益：t 时刻对未来 horizon 根的收益。"""
    fwd = np.zeros(len(close))
    if len(close) > horizon:
        fwd[:-horizon] = close[horizon:] / close[:-horizon] - 1.0
    return fwd


def _kline_to_fields(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    from backend.services.alpha.factor_compute import kline_df_to_fields
    return kline_df_to_fields(df)


def _data_quality_metrics(df: pd.DataFrame, factor_values: np.ndarray, fwd: np.ndarray) -> Dict[str, float]:
    """数据完整率：K线必需列与因子值/前向收益的缺失比例（L101 数据质量入卡）。"""
    kline_complete = 1.0
    try:
        need = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        if need:
            kline_n = int(df[need].notna().sum().sum())
            kline_complete = kline_n / int(df[need].size)
    except Exception:
        pass
    fv = np.asarray(factor_values, dtype=float)
    fw = np.asarray(fwd, dtype=float)
    if fv.size == 0 or fw.size == 0:
        return {"completeness": 0.0, "missing_pct": 1.0,
                "kline_completeness": round(kline_complete, 6)}
    valid = np.isfinite(fv) & np.isfinite(fw)
    completeness = float(valid.mean()) if fv.size else 0.0
    return {
        "completeness": round(completeness, 6),
        "missing_pct": round(1.0 - completeness, 6),
        "kline_completeness": round(kline_complete, 6),
    }


def build_factor_card(
    *,
    factor_id: str,
    expr,
    dfs: Dict[str, pd.DataFrame],
    period: str = "4h",
    horizon: int = 5,
    source: str = "",
    n_quantiles: int = 5,
    max_pool_corr: float = 0.0,
) -> Dict[str, Any]:
    """
    生成完整因子报告卡 JSON（可安全落库进 factor_evolution_log.metrics）。

    Args:
        factor_id: 因子 ID
        expr: FactorExpr 实例（evaluate(fields) 返回因子值数组）
        dfs: {symbol: K线 DataFrame}（品种越多 IC 横截面越稳健）
        period: K线周期（年化口径）
        horizon: 前向收益期数
        source: 因子来源（rev/mom/gp/...）
        n_quantiles: 分位档数（默认 5）
        max_pool_corr: 与活跃池最大相关（无池数据传 0.0，不参与硬拦截）

    Returns:
        报告卡 dict：{card_version, basic, ic, quantile, decay, turnover,
                      parsimony, data_quality, admission}
    """
    from backend.services.factor_engine.evaluation import (
        admission_gate,
        evaluate_factor,
        ic_significance,
        information_coefficient,
        parsimony_penalty,
        quantile_backtest,
        rolling_decay,
    )

    card: Dict[str, Any] = {
        "card_version": CARD_VERSION,
        "basic": {
            "factor_id": factor_id,
            "source": source,
            "period": period,
            "horizon": horizon,
            "expr_id": getattr(expr, "expr_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "ic": {"mean": None, "icir": None, "p_value": None, "per_symbol": {}},
        "quantile": {},
        "decay": {"halflife_bars": None, "rolling": None},
        "turnover": None,
        "parsimony": {"node_count": None, "penalty": None},
        "data_quality": {
            "per_symbol": {},
            "mean_completeness": None,
            "mean_missing_pct": None,
        },
        "admission": {"passed": False, "reasons": [], "details": {}},
    }

    per_sym: Dict[str, Dict[str, Any]] = {}
    sym_ics: list[float] = []
    icirs: list[float] = []
    turnovers: list[float] = []
    halves: list[int] = []
    best_qb = None
    best_sym = ""
    best_top_sharpe = -1e9

    for sym, df in dfs.items():
        try:
            fields = _kline_to_fields(df)
            factor_values = expr.evaluate(fields)
            close = df["close"].values.astype(float)
            fwd = _forward_returns(close, horizon)
            fv = np.asarray(factor_values, dtype=float)
            mask = np.isfinite(fv) & np.isfinite(fwd)
            if mask.sum() < 50:
                per_sym[sym] = {"skipped": "样本不足"}
                continue
            fs = pd.Series(fv[mask], index=df.index[mask])
            rs = pd.Series(fwd[mask], index=df.index[mask])

            r = evaluate_factor(factor_id, fs, rs, method="spearman")
            dq = _data_quality_metrics(df, fv, fwd)
            per_sym[sym] = {
                "ic": _safe_float(r.ic_mean),
                "icir": _safe_float(r.icir),
                "rank_ic": _safe_float(r.rank_ic_mean),
                "turnover": _safe_float(r.turnover),
                "halflife_bars": _safe_int(r.halflife_bars),
                "monotonicity_p": _safe_float(r.monotonicity_p),
                "n_samples": _safe_int(r.n_samples),
                "data_quality": dq,
            }
            sym_ics.append(float(r.ic_mean))
            icirs.append(float(r.icir))
            turnovers.append(float(r.turnover))
            halves.append(int(r.halflife_bars))

            qb = quantile_backtest(
                fs, rs, factor_id=factor_id, n_quantiles=n_quantiles, period=period,
            )
            if qb is not None and qb.n_obs > 0 and qb.quantile_sharpe.size >= n_quantiles:
                if qb.quantile_sharpe[-1] > best_top_sharpe:
                    best_top_sharpe = float(qb.quantile_sharpe[-1])
                    best_qb = qb
                    best_sym = sym

            rd = rolling_decay(fs, rs, window=min(30, len(fs) // 4), step=7)
            per_sym[sym]["decay"] = {
                "trend_slope": _safe_float(rd.get("trend_slope")),
                "decay_p": _safe_float(rd.get("decay_p")),
                "first_half_mean_ic": _safe_float(rd.get("first_half_mean_ic")),
                "second_half_mean_ic": _safe_float(rd.get("second_half_mean_ic")),
                "neg_streak": _safe_int(rd.get("neg_streak")),
                "decayed": bool(rd.get("decayed", False)),
            }
        except Exception as e:
            per_sym[sym] = {"error": str(e)[:120]}
            logger.debug("[FactorCard] %s/%s 报告卡单项失败: %s", factor_id, sym, e)

    # ── 聚合 ──
    if sym_ics:
        card["ic"] = {
            "mean": round(float(np.mean(sym_ics)), 6),
            "icir": round(float(np.mean(icirs)), 6),
            "p_value": _safe_float(ic_significance(np.array(sym_ics))),
            "per_symbol": per_sym,
        }
        card["turnover"] = round(float(np.mean(turnovers)), 6)
        card["decay"]["halflife_bars"] = (
            int(np.mean(halves)) if halves else None
        )
    else:
        card["ic"]["per_symbol"] = per_sym

    # 分层回测（取多头夏普最优品种的完整结果）
    if best_qb is not None:
        card["quantile"] = {
            "n_quantiles": n_quantiles,
            "best_symbol": best_sym,
            "annual_ret": [_safe_float(v) for v in best_qb.quantile_annual_ret],
            "sharpe": [_safe_float(v) for v in best_qb.quantile_sharpe],
            "long_short_sharpe": _safe_float(best_qb.long_short_sharpe),
            "top_excess_annual": _safe_float(best_qb.top_excess_annual),
            "top_max_drawdown": _safe_float(best_qb.top_max_drawdown),
            "monotonic_r": _safe_float(best_qb.monotonic_r),
            "n_obs": _safe_int(best_qb.n_obs),
        }

    # 数据质量（跨品种均值）
    dqs = [v["data_quality"] for v in per_sym.values()
           if isinstance(v.get("data_quality"), dict)]
    if dqs:
        card["data_quality"]["mean_completeness"] = round(
            float(np.mean([d["completeness"] for d in dqs])), 6)
        card["data_quality"]["mean_missing_pct"] = round(
            float(np.mean([d["missing_pct"] for d in dqs])), 6)

    # parsimony 复杂度
    ast = getattr(expr, "ast", None)
    if ast is not None:
        n_nodes = _node_count(ast)
        card["parsimony"] = {
            "node_count": n_nodes,
            "penalty": round(parsimony_penalty(n_nodes), 6),
        }

    # admission_gate 判定（5.3.3）
    if card["ic"].get("mean") is not None and card["quantile"]:
        gate = admission_gate(
            factor_id=factor_id,
            top_quantile_sharpe=best_top_sharpe,
            fitness=float(card["ic"]["icir"] or 0.0),
            turnover=float(card["turnover"] or 0.0),
            max_pool_corr=max_pool_corr,
            ic_halflife_bars=int(card["decay"]["halflife_bars"] or 0),
            ic_mean=float(card["ic"]["mean"] or 0.0),
            ic_p=float(card["ic"]["p_value"] or 1.0),
        )
        card["admission"] = {
            "passed": bool(gate.passed),
            "reasons": list(gate.reasons),
            "details": {k: _safe_float(v) if isinstance(v, float) else v
                        for k, v in gate.details.items()},
        }
    else:
        card["admission"]["reasons"] = ["报告卡数据不足（无IC/分层）"]

    return card
