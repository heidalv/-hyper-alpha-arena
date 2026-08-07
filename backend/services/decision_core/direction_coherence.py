"""Direction Coherence Protocol (DCP) — 全链路单一方向裁判。

编排器（MultiTimeframeOrchestrator）为方向权威；Master LLM 只能在
顺势或满足 nature 分档逆势例外时开仓。预筛选（HYBRID）永远不能覆盖 BLOCK。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# enforce | audit | off
def _dcp_mode(trading_mode: str = "paper") -> str:
    """按交易模式返回 DCP 生效模式。

    2026-07-06 整改：Paper 默认仍是 audit（只记日志不拦截，让 AI 方向判断优先，
    用于探索/积累样本）；但这个"探索期"默认值不能沿用到 Live——真实资金环境下
    三周期编排器与 AI 方向明显冲突时必须硬拦截，Live 强制 enforce。
    """
    try:
        from backend.config.settings import DIRECTION_COHERENCE_MODE
        _paper_mode = (DIRECTION_COHERENCE_MODE or "enforce").strip().lower()
    except Exception:
        _paper_mode = os.getenv("DIRECTION_COHERENCE_MODE", "enforce").strip().lower()
    if (trading_mode or "paper").strip().lower() == "paper":
        return _paper_mode
    return os.getenv("LIVE_DIRECTION_COHERENCE_MODE", "enforce").strip().lower()

TREND_NATURES = frozenset({"trend_follow", "position"})
SWING_NATURES = frozenset({"swing", "intraday", "scalp"})

CONTRARIAN_MIN_CONF = 75.0
CONTRARIAN_MAX_ORCH_CONF = 50.0
PENALTY_THRESHOLD_BUMP = 10

STRONG_OPPOSE_ORCH_CONF = 0.30


@dataclass
class DirectionVerdict:
    allowed: bool
    rule: str = ""
    penalty: int = 0
    reason: str = ""
    audit_only: bool = False

    @property
    def is_penalty(self) -> bool:
        return self.allowed and self.penalty > 0


def _normalize_confidence(confidence: float) -> float:
    from backend.services.decision_core.threshold_resolver import normalize_confidence_pct
    return normalize_confidence_pct(confidence)


def _ai_side(action: str) -> str:
    a = (action or "").lower()
    if a in ("buy", "pyramid", "dca", "long"):
        return "long"
    if a in ("sell", "short"):
        return "short"
    return ""


def _bias_to_side(bias: str) -> str:
    b = (bias or "").lower()
    if b in ("bullish", "strongly_bullish", "long", "long_only"):
        return "long"
    if b in ("bearish", "strongly_bearish", "short", "short_only"):
        return "short"
    return ""


def _is_strong_opposite(ai_side: str, bias: str) -> bool:
    b = (bias or "").lower()
    if ai_side == "long" and b == "strongly_bearish":
        return True
    if ai_side == "short" and b == "strongly_bullish":
        return True
    return False


def _is_weak_opposite(ai_side: str, bias: str) -> bool:
    b = (bias or "").lower()
    if ai_side == "long" and b == "bearish":
        return True
    if ai_side == "short" and b == "bullish":
        return True
    return False


def _tier_bias_key(tier: str) -> str:
    t = (tier or "mid").lower()
    if t in ("short", "mid", "long"):
        return f"{t}_bias"
    return "mid_bias"


def _tier_view_bias_key(tier: str) -> str:
    t = (tier or "mid").lower()
    if t in ("short", "mid", "long"):
        return f"{t}_view_bias"
    return "mid_view_bias"


def _get_tier_bias(orchestrator: dict, tier: str) -> tuple[str, float]:
    if not orchestrator:
        return "", 0.0
    bias_key = _tier_bias_key(tier)
    conf_key = bias_key.replace("_bias", "_conf")
    bias = orchestrator.get(bias_key) or orchestrator.get(_tier_view_bias_key(tier)) or ""
    conf = float(orchestrator.get(conf_key, 0) or 0)
    return str(bias), conf


def evaluate_direction_coherence(
    *,
    action: str,
    confidence: float,
    tier: str = "mid",
    trade_nature: str = "swing",
    orchestrator: Optional[dict] = None,
    fan_branch: str = "",
    symbol: str = "",
    trading_mode: str = "paper",
) -> DirectionVerdict:
    """评估开仓方向与编排器是否一致。返回 DirectionVerdict。"""
    mode = _dcp_mode(trading_mode)
    if mode == "off":
        return DirectionVerdict(allowed=True, rule="disabled")

    orch = orchestrator or {}
    ai_side = _ai_side(action)
    if not ai_side:
        return DirectionVerdict(allowed=True, rule="not_directional")

    conf = _normalize_confidence(confidence)
    nature = (trade_nature or "swing").lower()
    tier_l = (tier or "mid").lower()
    sym = (symbol or "").upper()

    final_side = (orch.get("final_side") or "").lower()
    weighted_conf = float(orch.get("weighted_confidence", 0) or 0)
    if weighted_conf <= 1.0:
        weighted_conf *= 100.0

    tier_bias, tier_conf = _get_tier_bias(orch, tier_l)
    if tier_conf <= 1.0:
        tier_conf *= 100.0

    def _finish(verdict: DirectionVerdict) -> DirectionVerdict:
        if mode == "audit" and not verdict.allowed:
            logger.info(
                "[DCP] AUDIT would_block symbol=%s action=%s rule=%s reason=%s",
                sym, action, verdict.rule, verdict.reason,
            )
            return DirectionVerdict(
                allowed=True,
                rule=verdict.rule,
                penalty=verdict.penalty,
                reason=verdict.reason,
                audit_only=True,
            )
        if verdict.allowed:
            logger.debug(
                "[DCP] ALLOW symbol=%s action=%s rule=%s penalty=%s",
                sym, action, verdict.rule, verdict.penalty,
            )
        else:
            logger.info(
                "[DCP] BLOCK symbol=%s action=%s rule=%s reason=%s",
                sym, action, verdict.rule, verdict.reason,
            )
            try:
                from backend.services.decision_core.unified_gate import record_block_event
                record_block_event(sym, action, f"dcp_{verdict.rule}", verdict.reason)
            except Exception:
                pass
        return verdict

    # ── 宏观周期心智硬门：decline/risk_off 禁止 trend 开多 ──
    if nature in TREND_NATURES and ai_side == "long":
        _macro_phase = (orch.get("macro_cycle_phase") or "").lower()
        _macro_conf = float(orch.get("macro_phase_confidence", 0) or 0)
        _macro_regime = (orch.get("macro_regime") or "").lower()
        _macro_blocks = bool(orch.get("macro_blocks_trend_long", False))
        if _macro_blocks or (
            _macro_phase == "decline" and _macro_conf >= 0.6
        ) or (
            _macro_regime == "risk_off" and _macro_conf >= 0.6
        ):
            return _finish(DirectionVerdict(
                allowed=False,
                rule="macro_regime_block_trend_long",
                reason=(
                    f"{sym} 宏观硬门拦截 trend 开多: phase={_macro_phase} "
                    f"conf={_macro_conf:.0%} regime={_macro_regime}"
                ),
            ))

    # FanOut 已标记 weak_oppose — 直接 BLOCK（执行层双保险）
    if fan_branch == "weak_oppose":
        return _finish(DirectionVerdict(
            allowed=False,
            rule="fan_weak_oppose",
            reason=f"{sym} FanOut weak_oppose 与编排器 tier 反向",
        ))

    # 强反向 tier bias
    if _is_strong_opposite(ai_side, tier_bias) and tier_conf >= STRONG_OPPOSE_ORCH_CONF * 100:
        return _finish(DirectionVerdict(
            allowed=False,
            rule="strong_tier_oppose",
            reason=(
                f"{sym}[{tier_l}] 强反向 bias={tier_bias} conf={tier_conf:.0f}% "
                f"vs AI {action}"
            ),
        ))

    # final_side 明确对立
    if final_side in ("long", "short") and final_side != ai_side and weighted_conf >= 10:
        if nature in TREND_NATURES:
            # 长线趋势单不应被全局 final_side（混入短/中线噪音）一刀切拦截。
            # 应以该 tier 自身的 bias 为准：tier 自身支持则放行，tier 自身反对才拦截。
            tier_side = _bias_to_side(tier_bias)
            if tier_side and tier_side != ai_side:
                # tier 自身方向也反对 → 确认拦截
                return _finish(DirectionVerdict(
                    allowed=False,
                    rule="trend_strict",
                    reason=(
                        f"{sym} trend/position tier_bias={tier_bias} 与 AI {action} 矛盾 "
                        f"(final_side={final_side} conf={weighted_conf:.0f}%)"
                    ),
                ))
            # tier 自身方向不反对（支持或中性）→ 放行，不因短中线噪音拦截长线单
            if conf >= CONTRARIAN_MIN_CONF:
                return _finish(DirectionVerdict(
                    allowed=True,
                    rule="trend_tier_aligned",
                    penalty=PENALTY_THRESHOLD_BUMP,
                    reason=(
                        f"{sym} trend/position tier_bias={tier_bias} 支持，"
                        f"忽略全局 final_side={final_side}"
                    ),
                ))
            # 置信度偏低但 tier 自身不反对 → 不拦，交给后续 tier_bias 检查
            logger.info(
                "[DCP] trend/position tier_bias=%s 不反对，放行(final_side=%s conf=%.0f%%)",
                tier_bias, final_side, conf,
            )

        tier_side = _bias_to_side(tier_bias)
        if tier_side and tier_side != ai_side and tier_side != "neutral":
            if _is_weak_opposite(ai_side, tier_bias) or _is_strong_opposite(ai_side, tier_bias):
                if conf >= CONTRARIAN_MIN_CONF and tier_conf < CONTRARIAN_MAX_ORCH_CONF:
                    return _finish(DirectionVerdict(
                        allowed=True,
                        rule="contrarian_high_conf",
                        penalty=PENALTY_THRESHOLD_BUMP,
                        reason=(
                            f"{sym} 逆势例外: conf={conf:.0f}% orch_tier_conf={tier_conf:.0f}%"
                        ),
                    ))
                return _finish(DirectionVerdict(
                    allowed=False,
                    rule="swing_oppose",
                    reason=(
                        f"{sym} swing/scalp 逆势需 conf≥{CONTRARIAN_MIN_CONF:.0f}% "
                        f"且 tier_conf<{CONTRARIAN_MAX_ORCH_CONF:.0f}% "
                        f"(got {conf:.0f}% / {tier_conf:.0f}%)"
                    ),
                ))

        if _is_weak_opposite(ai_side, tier_bias) or (
            final_side != ai_side and weighted_conf >= 10
        ):
            if conf >= CONTRARIAN_MIN_CONF and weighted_conf < CONTRARIAN_MAX_ORCH_CONF:
                return _finish(DirectionVerdict(
                    allowed=True,
                    rule="contrarian_high_conf",
                    penalty=PENALTY_THRESHOLD_BUMP,
                    reason=f"{sym} final_side 逆势高置信例外 conf={conf:.0f}%",
                ))
            return _finish(DirectionVerdict(
                allowed=False,
                rule="final_side_oppose",
                reason=(
                    f"{sym} 编排器 final_side={final_side}({weighted_conf:.0f}%) "
                    f"与 AI {action} 矛盾"
                ),
            ))

    # tier bias 温和反向（无 final_side 冲突时仍检查）
    if tier_bias and _bias_to_side(tier_bias) == (
        "short" if ai_side == "long" else "long" if ai_side == "short" else ""
    ):
        if _is_weak_opposite(ai_side, tier_bias):
            if nature in TREND_NATURES:
                # 长线趋势单：tier 自身 bias 反对时也允许高置信逆势例外
                if conf >= CONTRARIAN_MIN_CONF and tier_conf < CONTRARIAN_MAX_ORCH_CONF:
                    return _finish(DirectionVerdict(
                        allowed=True,
                        rule="contrarian_trend_high_conf",
                        penalty=PENALTY_THRESHOLD_BUMP,
                        reason=(
                            f"{sym}[{tier_l}] trend 逆势高置信例外: "
                            f"conf={conf:.0f}% tier_conf={tier_conf:.0f}%"
                        ),
                    ))
                return _finish(DirectionVerdict(
                    allowed=False,
                    rule="trend_tier_oppose",
                    reason=f"{sym}[{tier_l}] trend 与 tier_bias={tier_bias} 反向",
                ))
            if conf >= CONTRARIAN_MIN_CONF and tier_conf < CONTRARIAN_MAX_ORCH_CONF:
                return _finish(DirectionVerdict(
                    allowed=True,
                    rule="contrarian_high_conf",
                    penalty=PENALTY_THRESHOLD_BUMP,
                    reason=f"{sym} tier 逆势高置信例外",
                ))
            return _finish(DirectionVerdict(
                allowed=False,
                rule="tier_weak_oppose",
                reason=f"{sym}[{tier_l}] tier_bias={tier_bias} 与 AI {action} 温和反向",
            ))

    return _finish(DirectionVerdict(allowed=True, rule="aligned"))


def is_direction_opposed(
    *,
    action: str,
    orchestrator: Optional[dict] = None,
    tier: str = "mid",
) -> bool:
    """供 _calibrate_confidence 判断 AI 是否与编排器反向（不含逆势例外）。"""
    verdict = evaluate_direction_coherence(
        action=action,
        confidence=0,
        tier=tier,
        trade_nature="trend_follow",
        orchestrator=orchestrator,
    )
    return not verdict.allowed
