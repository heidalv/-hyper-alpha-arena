"""Agent 证据清单构建 — L0/L1 事实层，供 Swing/Trend LLM 引用。"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class AgentEvidenceFact:
    id: str
    source: str
    value: Any
    available: bool
    timestamp: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _ms(symbol: str, market_envs: dict) -> dict:
    if not isinstance(market_envs, dict):
        return {}
    raw = market_envs.get(symbol) or market_envs.get(symbol.upper()) or {}
    return raw if isinstance(raw, dict) else {}


def _indicators(ms: dict, tf: str) -> dict:
    raw = ms.get(f"indicators_{tf}") or {}
    return raw if isinstance(raw, dict) else {}


def _fact(fid: str, source: str, value: Any, available: bool = True) -> AgentEvidenceFact:
    avail = available and value is not None and value != ""
    return AgentEvidenceFact(
        id=fid,
        source=source,
        value=value,
        available=avail,
        timestamp=time.time(),
    )


def _orch_bias_conf(orch: dict, bias_key: str, conf_key: str, conf_alt: str) -> tuple:
    bias = orch.get(bias_key)
    conf = orch.get(conf_key)
    if conf is None:
        conf = orch.get(conf_alt)
    return bias, conf


def _memory_summary(db, nature: str) -> tuple:
    if not db:
        return None, False
    try:
        from backend.services.trade_memory_context import build_recent_trades_section
        sec = build_recent_trades_section(db, limit=3, nature=nature)
        if sec:
            return sec[:240], True
    except Exception:
        pass
    return None, False


def _cycle_prob_facts(symbol: str, ms: dict, tier: str) -> List[AgentEvidenceFact]:
    """周期方向概率引擎的证据（数据锚定的方向先验，用于抑制 LLM 主观幻觉）。

    tier: swing→"mid"(主周期1h)，trend→"long"(主周期4h)。
    概率来自 backend/services/cycle_direction_probability，若模型未训练则整体标记缺失。
    """
    from backend.services.cycle_direction_probability import (
        cycle_probability_engine,
        extract_features_from_indicators,
        TIER_PRIMARY,
    )

    tf = TIER_PRIMARY.get(tier, "1h")
    ind = _indicators(ms, tf)
    try:
        feats = extract_features_from_indicators(ind)
        res = cycle_probability_engine.estimate(tier, feats)
    except Exception:
        res = None

    avail = bool(res and res.available)
    facts: List[AgentEvidenceFact] = []
    if avail:
        facts.append(_fact(
            f"cycle_prob_dir_{tier}", "cycle_prob_engine",
            f"{res.direction}(涨{res.prob_up:.0%}/跌{res.prob_down:.0%}/震荡{res.prob_range:.0%})", True))
        facts.append(_fact(f"cycle_prob_conf_{tier}", "cycle_prob_engine", round(res.confidence, 3), True))
        # 校准质量：模型历史上准不准，低则 LLM 可弱化其权重
        facts.append(_fact(
            f"cycle_prob_calibration_{tier}", "cycle_prob_engine",
            round(res.calibration_quality, 3), True))
        facts.append(_fact(
            "cycle_prob_top_driver", "cycle_prob_engine",
            ",".join(res.top_drivers) if res.top_drivers else None, bool(res.top_drivers)))
    else:
        facts.append(_fact(f"cycle_prob_dir_{tier}", "cycle_prob_engine", None, False))
        facts.append(_fact(f"cycle_prob_conf_{tier}", "cycle_prob_engine", None, False))
        facts.append(_fact(f"cycle_prob_calibration_{tier}", "cycle_prob_engine", None, False))
        facts.append(_fact("cycle_prob_top_driver", "cycle_prob_engine", None, False))
    return facts


def _build_shared_facts(
    symbol: str,
    market_envs: dict,
    db=None,
    *,
    memory_nature: str,
    memory_id: str = "swing_memory",
    prob_tier: str = "mid",
) -> List[AgentEvidenceFact]:
    ms = _ms(symbol, market_envs)
    orch = ms.get("orchestrator") if isinstance(ms.get("orchestrator"), dict) else {}

    facts: List[AgentEvidenceFact] = []

    for tf, fid in (("1h", "rsi_1h"), ("4h", "rsi_4h")):
        ind = _indicators(ms, tf)
        facts.append(_fact(fid, f"indicators_{tf}", ind.get("rsi")))

    ind_1h = _indicators(ms, "1h")
    facts.append(_fact("ema_trend_1h", "indicators_1h", ind_1h.get("ema_trend")))
    facts.append(_fact("vol_ratio_1h", "indicators_1h", ind_1h.get("vol_ratio")))

    mid_bias, mid_conf = _orch_bias_conf(orch, "mid_bias", "mid_confidence", "mid_conf")
    facts.append(_fact("mid_bias", "orchestrator", mid_bias))
    facts.append(_fact("mid_confidence", "orchestrator", mid_conf))

    facts.append(_fact("funding_rate", "market_summary", ms.get("funding_rate")))
    facts.append(_fact("oi_delta_1h", "indicators_1h", ind_1h.get("oi_delta")))

    try:
        from backend.services.crypto_alpha_signals import crypto_alpha
        lm = crypto_alpha.liquidation_magnet(symbol)
        val = f"{lm.direction}/{lm.severity}" if lm.available else None
        facts.append(_fact("liquidation_magnet", "crypto_alpha", val, lm.available))
    except Exception:
        facts.append(_fact("liquidation_magnet", "crypto_alpha", None, False))

    try:
        from backend.services.macro_regime_service import macro_regime_service
        st = macro_regime_service.get_state("GLOBAL")
        facts.append(_fact("macro_cycle_phase", "macro_regime", st.cycle_phase, True))
    except Exception:
        facts.append(_fact("macro_cycle_phase", "macro_regime", None, False))

    regime = ms.get("regime") or ms.get("market_cycle")
    facts.append(_fact("regime", "market_summary", regime))

    mem_val, mem_ok = _memory_summary(db, memory_nature)
    facts.append(_fact(memory_id, "trade_memory", mem_val or "无近期同类型战绩", mem_ok))

    facts.extend(_cycle_prob_facts(symbol, ms, prob_tier))

    return facts


def build_swing_evidence(
    symbol: str,
    market_envs: dict,
    db=None,
) -> List[AgentEvidenceFact]:
    """SwingAgent 最低 12 项证据清单。"""
    return _build_shared_facts(
        symbol, market_envs, db, memory_nature="swing", memory_id="swing_memory",
        prob_tier="mid",
    )


def build_trend_evidence(
    symbol: str,
    market_envs: dict,
    db=None,
) -> List[AgentEvidenceFact]:
    """TrendAgent 最低 15 项证据清单（含 Swing 12 项 + 3 项长线专属）。"""
    facts = _build_shared_facts(
        symbol, market_envs, db, memory_nature="trend", memory_id="swing_memory",
        prob_tier="long",
    )
    ms = _ms(symbol, market_envs)
    ind_4h = _indicators(ms, "4h")
    ind_1d = _indicators(ms, "1d")
    ind_1w = _indicators(ms, "1w")

    ema_4h = ind_4h.get("ema_trend")
    facts.append(_fact("trend_4h", "indicators_4h", ema_4h))

    ema_1d = ind_1d.get("ema_trend")
    facts.append(_fact("trend_1d", "indicators_1d", ema_1d))

    ema_1w = ind_1w.get("ema_trend")
    facts.append(_fact("trend_1w", "indicators_1w", ema_1w))

    resonance = None
    if ema_4h and ema_1d:
        if ema_4h == ema_1d and ema_4h in ("bullish", "bearish"):
            resonance = f"共振_{ema_4h}"
        else:
            resonance = f"分歧_4h={ema_4h}_1d={ema_1d}"
    facts.append(_fact("trend_4h_1d_resonance", "derived", resonance))

    price_label = None
    recent = ind_1d.get("recent_klines") or []
    if isinstance(recent, list) and len(recent) >= 10:
        closes = [float(k.get("close")) for k in recent if k.get("close") is not None]
        if closes:
            cur, hi, lo = closes[-1], max(closes), min(closes)
            if hi > lo:
                pos = (cur - lo) / (hi - lo)
                price_label = "高位" if pos > 0.7 else "低位" if pos < 0.3 else "中位"
    facts.append(_fact("price_vs_90d_range", "kline", price_label))

    try:
        from backend.services.macro_regime_service import macro_regime_service
        st = macro_regime_service.get_state("GLOBAL")
        facts.append(_fact("macro_direction_constraint", "macro_regime", st.direction_constraint, True))
    except Exception:
        facts.append(_fact("macro_direction_constraint", "macro_regime", None, False))

    facts.append(_fact("lifecycle_stage", "llm_output", None, False))
    facts.append(_fact("scenario_a_trigger", "llm_output", None, False))
    return facts


def format_evidence_for_prompt(facts: List[AgentEvidenceFact]) -> str:
    """格式化证据块注入 prompt。"""
    if not facts:
        return ""
    lines = [
        "## 证据清单（只能引用下列 fact_id，禁止编造未列出的数字）",
        "输出 JSON 必须包含 cited_fact_ids: [\"rsi_1h\", ...] 列出你实际引用的证据 ID。",
        "",
    ]
    for f in facts:
        tag = "可用" if f.available else "缺失"
        lines.append(f"- **{f.id}** [{f.source}] ({tag}): {f.value}")

    # 周期方向概率引擎先验的使用说明（数据锚定，抑制主观幻觉）
    if any(getattr(f, "id", "").startswith("cycle_prob_dir_") and f.available for f in facts):
        lines += [
            "",
            "### 如何使用 cycle_prob_*（周期方向概率引擎，基于历史K线条件频率）",
            "- 它是**数据锚定的方向先验**：请把 cycle_prob_dir_* 作为默认方向倾向；",
            "  只有当你能在上述其它证据里找到明确、可引用的反向理由时，才推翻它，并在 reasoning 说明。",
            "- 先验强弱看 cycle_prob_calibration_*（校准质量 0~1）：**接近 0 表示该周期方向历史上很难预测，",
            "  此时不要因为这个先验就重仓下注，应更依赖结构/衍生品等其它证据**。",
            "- 若你的最终方向与 cycle_prob_dir_* 一致或相反，都必须在 cited_fact_ids 里引用对应的 cycle_prob_* fact。",
        ]
    return "\n".join(lines)


def facts_to_audit_payload(facts: List[AgentEvidenceFact]) -> List[Dict[str, Any]]:
    """供 decision_snapshot / Hermes 落库的精简清单。"""
    return [
        {"id": f.id, "value": f.value, "available": f.available, "source": f.source}
        for f in facts
    ]
