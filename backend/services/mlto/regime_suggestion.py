# -*- coding: utf-8 -*-
"""
regime_suggestion — v6 阶段 2（S2-7）regime 参数建议通道

计划 6.3 第 4 项：LLM 输出 regime 判定 + 参数档位建议（SL 倍数/TP 触发/
trailing/加仓节奏），规则校验后执行。

设计原则：
- LLM 建议是「参考通道」，规则判定是「权威」：LLM regime 与
  regime_agent.classify_regime 冲突时以规则为准（conflict 标记）。
- 数值档位全 clamp 到安全区间（SL 倍数 [0.5, 3.0]、TP 触发 [1.0, 8.0]×ATR），
  非法枚举拒绝并回退默认（none / 不启用）。
- 校验通过的 applied 参数通过 `apply_regime_params` 合并进 market summary，
  执行层（orchestrator._llm_stops / structure_stops）读取执行。

字段语义（LLM 输出建议档位）：
  regime         : trend | ranging | extreme | unknown（LLM 判定，供参考）
  sl_multiplier  : 结构止损倍数的档位建议（1.0=基准；>1 放宽，<1 收紧）
  tp_trigger     : TP 触发阈值（ATR 倍数档位）
  trailing       : 是否启用 trailing stop（bool）
  addon_rhythm   : 加仓节奏 none | conservative | aggressive
  rationale      : 建议理由（LLM 叙事，落库可观测）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

REGIME_WHITELIST = ("trend", "ranging", "extreme", "unknown")
ADDON_RHYTHMS = ("none", "conservative", "aggressive")

# 档位 clamp 边界
SL_MULT_MIN, SL_MULT_MAX = 0.5, 3.0
TP_TRIGGER_MIN, TP_TRIGGER_MAX = 1.0, 8.0


@dataclass
class RegimeSuggestion:
    regime: str = "unknown"
    sl_multiplier: float = 1.0
    tp_trigger: float = 2.0
    trailing: bool = False
    addon_rhythm: str = "none"
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_regime_suggestion(raw: Any) -> Optional[RegimeSuggestion]:
    """宽容解析 LLM 输出的 regime_suggestion 块；缺失/坏类型 → None（不阻断）。"""
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        regime = str(raw.get("regime") or "unknown").strip().lower()
        if regime not in REGIME_WHITELIST:
            regime = "unknown"
        return RegimeSuggestion(
            regime=regime,
            sl_multiplier=_to_float(raw.get("sl_multiplier"), 1.0),
            tp_trigger=_to_float(raw.get("tp_trigger"), 2.0),
            trailing=_to_bool(raw.get("trailing"), False),
            addon_rhythm=_to_str(raw.get("addon_rhythm"), "none", ADDON_RHYTHMS),
            rationale=str(raw.get("rationale") or "")[:500],
        )
    except Exception:
        return None


def _to_float(v: Any, default: float) -> float:
    try:
        f = float(v)
        return f if f == f else default  # NaN → default
    except (TypeError, ValueError):
        return default


def _to_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return default


def _to_str(v: Any, default: str, whitelist: tuple) -> str:
    s = str(v or "").strip().lower()
    return s if s in whitelist else default


def validate_regime_suggestion(
    sugg: Optional[RegimeSuggestion],
    market_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    规则校验 LLM 建议 → {applied, adjusted, conflicts, rejected}。

    - regime：与 classify_regime(market_data) 冲突 → applied.regime=规则判定，
      conflicts 记录（LLM 判定仅作参考通道）。
    - sl_multiplier / tp_trigger：clamp 到安全区间，adjusted 记录原值。
    - trailing / addon_rhythm：非法回退默认（rejected 记录）。
    - sugg 为 None → 空建议（applied 默认值，全部 rejected）。
    """
    rule_regime = "unknown"
    try:
        if isinstance(market_data, dict):
            from backend.services.decision_core.regime_agent import classify_regime
            rule_regime = classify_regime(market_data).regime
    except Exception:
        pass

    applied = {
        "regime": rule_regime,
        "sl_multiplier": 1.0,
        "tp_trigger": 2.0,
        "trailing": False,
        "addon_rhythm": "none",
        "rationale": "",
        "source": "rule_default",
    }
    adjusted: Dict[str, Any] = {}
    conflicts: list = []
    rejected: list = []

    if sugg is None:
        return {"applied": applied, "adjusted": adjusted,
                "conflicts": conflicts, "rejected": rejected}

    # regime 一致性：规则为准
    if sugg.regime != rule_regime:
        conflicts.append(
            f"LLM 判定 {sugg.regime} ≠ 规则判定 {rule_regime}，以规则为准"
        )
    applied["regime"] = rule_regime
    applied["rationale"] = sugg.rationale

    # SL 倍数 clamp
    raw_mult = sugg.sl_multiplier
    if not (SL_MULT_MIN <= raw_mult <= SL_MULT_MAX):
        adjusted["sl_multiplier"] = raw_mult
        rejected.append(f"sl_multiplier {raw_mult} 越界 → {SL_MULT_MIN}~{SL_MULT_MAX}")
    applied["sl_multiplier"] = max(SL_MULT_MIN, min(SL_MULT_MAX, raw_mult))

    # TP 触发 clamp
    raw_tp = sugg.tp_trigger
    if not (TP_TRIGGER_MIN <= raw_tp <= TP_TRIGGER_MAX):
        adjusted["tp_trigger"] = raw_tp
        rejected.append(f"tp_trigger {raw_tp} 越界 → {TP_TRIGGER_MIN}~{TP_TRIGGER_MAX}")
    applied["tp_trigger"] = max(TP_TRIGGER_MIN, min(TP_TRIGGER_MAX, raw_tp))

    applied["trailing"] = sugg.trailing
    applied["addon_rhythm"] = sugg.addon_rhythm

    applied["source"] = "llm_validated"
    return {"applied": applied, "adjusted": adjusted,
            "conflicts": conflicts, "rejected": rejected}


def apply_regime_params(
    ms: Dict[str, Any],
    validated: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    把校验后的 regime 参数合并进 market summary 供执行层读取
    （ms["regime_suggestion"]）。原 dict 不被修改（复制合并）。
    """
    out = dict(ms or {})
    if not validated or not validated.get("applied"):
        return out
    out["regime_suggestion"] = dict(validated["applied"])
    return out


def consume_sl_multiplier(
    ms: Dict[str, Any],
    base_sl_pct: float,
    *,
    min_sl: float = 0.005,
    max_sl: float = 0.20,
) -> float:
    """
    执行层消费：从 ms["regime_suggestion"] 取 sl_multiplier 应用于结构止损。
    最终 SL 仍 clamp 到 [min_sl, max_sl] 物理界限；无建议 → 原值。
    """
    rs = (ms or {}).get("regime_suggestion")
    if not isinstance(rs, dict):
        return base_sl_pct
    try:
        mult = float(rs.get("sl_multiplier") or 1.0)
    except (TypeError, ValueError):
        return base_sl_pct
    return max(min_sl, min(max_sl, base_sl_pct * mult))
