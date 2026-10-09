# -*- coding: utf-8 -*-
"""融合层 —— 双通道秩归一 + James-Stein 收缩加权 + regime gate。

权重学（设计 §3.3，James-Stein/Efron-Morris）：
    w_channel = (1-λ)·w_ic + λ·w_prior，  w_prior=(0.5, 0.5)
    λ = min(1, k·std(滚动IC通道差))  —— IC 估计越不稳，越信先验
    有效样本 < 20 日 → λ=1（全收缩，即 50/50）
regime gate（对齐 midlong_regime_weights 的 regime 口径）：
    trend_up / trend_down → A 通道加成（数值信号在趋势态占优）
    range / unknown      → 均衡
    high_vol             → 收缩加强（k×1.5）
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_K_SHRINK = 4.0          # 收缩系数（IC 差标准差 ~0.25 时 λ≈1）
_MIN_SAMPLE = 20         # IC 统计最少有效日


def read_regime() -> str:
    """市场状态读取（尽力而为；失败 → unknown，走均衡+强收缩）。"""
    for mod, attr in (
        ("backend.services.market_regime_service", "current_regime"),
        ("backend.services.regime_service", "get_regime"),
    ):
        try:
            m = __import__(mod, fromlist=[attr])
            fn = getattr(m, attr, None)
            if callable(fn):
                r = fn()
                if r:
                    return str(r).lower()
        except Exception:
            continue
    # 本地文件兜底：daily brief json
    try:
        import json as _json
        from pathlib import Path
        p = Path(__file__).resolve().parents[2] / "data" / "analysis" / "latest_daily_brief.json"
        if p.exists():
            d = _json.loads(p.read_text(encoding="utf-8"))
            for key in ("regime", "market_regime", "state"):
                v = d.get(key)
                if v:
                    return str(v).lower()
    except Exception:
        pass
    return "unknown"


def regime_adjust(regime: str) -> Tuple[float, float]:
    """regime → (A加成, 收缩系数倍率)。返回对 prior/IC 权重的修正参数。"""
    r = (regime or "unknown").lower()
    if r in ("trend_up", "trend_down", "trend", "趋势"):
        return 0.60, 1.0     # A 通道先验权重上调
    if r in ("high_vol", "highvol", "crisis", "高波"):
        return 0.50, 1.5     # 均衡 + 强收缩
    return 0.50, 1.0         # range / unknown：均衡


def channel_weights(ic_stats: Optional[Dict], regime: Optional[str] = None) -> Dict[str, float]:
    """计算通道权重 {a, b, lambda, regime, n_days, source}。"""
    regime = regime or read_regime()
    w_prior_a, k_mult = regime_adjust(regime)
    st = ic_stats or {}
    days = int(st.get("days") or 0)
    if days < _MIN_SAMPLE or not st.get("ic_a"):
        return {"a": w_prior_a, "b": 1.0 - w_prior_a, "lambda": 1.0,
                "regime": regime, "n_days": days, "source": "prior_only"}
    ic_a = float(st.get("ic_a") or 0.0)
    ic_b = float(st.get("ic_b") or 0.0)
    # IC 差的滚动标准差（记录于 ic_stats.std_diff；缺省用保守 0.25）
    sd = float(st.get("std_diff") or 0.25) or 0.25
    lam = min(1.0, _K_SHRINK * k_mult * sd)
    # w_ic：两通道正 IC 的 softmax（都非正 → 回先验）
    pos_a, pos_b = max(0.0, ic_a), max(0.0, ic_b)
    if pos_a + pos_b <= 1e-9:
        w_ic = w_prior_a
    else:
        w_ic = pos_a / (pos_a + pos_b)
    wa = (1.0 - lam) * w_ic + lam * w_prior_a
    return {"a": float(np.clip(wa, 0.1, 0.9)), "b": float(np.clip(1.0 - wa, 0.1, 0.9)),
            "lambda": round(lam, 3), "regime": regime, "n_days": days,
            "source": "ic_shrunk", "ic_a": round(ic_a, 4), "ic_b": round(ic_b, 4)}


def _pct_rank(scores: Dict[str, float]) -> Dict[str, float]:
    """横截面百分位（n<3 → 原值 0.5 化处理：用 score 本身）。"""
    if not scores:
        return {}
    if len(scores) < 3:
        return {k: float(np.clip(v, 0.0, 1.0)) for k, v in scores.items()}
    order = sorted(scores, key=lambda k: float(scores[k]))
    n = len(order)
    return {k: (i / (n - 1)) for i, k in enumerate(order)}


def fuse(channel_a: Dict[str, Dict[str, object]],
         channel_b: Optional[Dict[str, Dict[str, object]]],
         ic_stats: Optional[Dict] = None,
         thesis_scores: Optional[Dict[str, float]] = None) -> Dict[str, Dict[str, object]]:
    """融合入口。返回 {sym: {hybrid, w_a, w_b, rank, regime, lambda, arm, a1}}。

    arm 标注消融档：channel_b 缺席 → "A0"，在场 → "A3"（A2 档 = 仅 LLM 综合器分数，
    由 score_log 的 arm_fields.A2 直接承载；A1 = thesis 通道混合）。
    thesis_scores（[流B 2026-09-17]）：{sym: score∈[0,1]}，来自分析师 thesis 数值化
    （ai_coin_unified mid/short 档）；A1 = 0.5·A百分位 + 0.5·T百分位（TradingAgents
    范式：分析师观点=alpha 源）。thesis 缺席的币 A1=None。
    """
    w = channel_weights(ic_stats)
    a_scores = {s: float(r.get("score") or 0.0) for s, r in channel_a.items()}
    a_ranked = _pct_rank(a_scores)
    b_present = bool(channel_b)
    b_ranked = _pct_rank({s: float(r.get("score") or 0.0) for s, r in (channel_b or {}).items()}) if b_present else {}
    t_ranked = _pct_rank(thesis_scores) if thesis_scores else {}
    out: Dict[str, Dict[str, object]] = {}
    for s, r in channel_a.items():
        a_p = a_ranked.get(s, 0.5)
        b_p = b_ranked.get(s, 0.5)
        hybrid = w["a"] * a_p + w["b"] * b_p
        t_p = t_ranked.get(s)
        a1 = round(0.5 * a_p + 0.5 * t_p, 4) if t_p is not None else None
        out[s] = {
            "hybrid": round(float(hybrid), 4),
            "a_pct": round(float(a_p), 4),
            "b_pct": round(float(b_p), 4) if b_present else None,
            "a1": a1,
            "thesis_pct": round(float(t_p), 4) if t_p is not None else None,
            "w_a": w["a"], "w_b": w["b"], "lambda": w["lambda"],
            "regime": w["regime"], "n_ic_days": w["n_days"],
            "arm": "A3" if b_present else "A0",
            "backend": r.get("backend"),
        }
        if b_present and s in (channel_b or {}):
            out[s]["llm"] = {"score": channel_b[s].get("score"),
                             "confidence": channel_b[s].get("confidence"),
                             "direction": channel_b[s].get("direction"),
                             "rationale": channel_b[s].get("rationale")}
    order = sorted(out, key=lambda s: -out[s]["hybrid"])
    for i, s in enumerate(order):
        out[s]["rank"] = i + 1
    return out
