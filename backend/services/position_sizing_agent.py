"""Unified position sizing agent.

This module is the single place that converts an entry decision into a
position-sizing plan. Direction agents decide *what* to trade; this module
decides *how much* risk to take, while downstream risk gates may only reduce
or reject the plan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def clamp_position_by_risk_cap(
    equity: float,
    notional_value: float,
    sl_pct: float,
    max_risk_pct: float,
) -> float:
    """单笔风险硬顶纯函数：把仓位名义价值缩放到"预期最大亏损 ≤ 权益×硬顶比例"以内。

    用途：任何下单路径在最终下单前都应调用本函数做二次校验，尤其是不经过
    ``PositionSizingAgent.build_plan``（本文件下方的主路径，已内置同款硬顶检查）
    的独立路径——例如 ScalpRouter 历史上用固定 ``SCALP_SIZE_PCT`` 直接按权益比例
    算保证金，完全绕过了这道"最后一道闸"。把该检查抽成纯函数，是为了让
    full_auto_trading_service.py 里的 Scalp 分支也能复用同一份风险数学，
    不必重新实现一遍、也不用依赖完整的 build_plan 上下文。

    参数：
        equity: 账户权益（USD），用于计算风险预算。
        notional_value: 计划下单的名义价值（USD，缩放前）。
        sl_pct: 止损距离百分比（0~1 小数，如 0.02 表示 2%）。
        max_risk_pct: 单笔最大风险占权益比例（0~1 小数，通常取
            ``settings.V5_MAX_TRADE_RISK_PCT``，默认 0.015 即 1.5%）。

    返回：
        缩放后的 notional_value。若预期最大亏损（notional_value × sl_pct）未超过
        权益×max_risk_pct，原样返回 notional_value；否则按比例缩小到刚好等于硬顶。
        任一输入非法或为 0（无法计算风险）时，原样返回 notional_value——不做
        缩放不代表放行，调用方仍应有其他兜底门禁（如 tp/sl 缺失检查）。
    """
    try:
        equity_f = max(float(equity or 0), 0.0)
        notional_f = max(float(notional_value or 0), 0.0)
        sl_f = max(float(sl_pct or 0), 0.0)
        cap_f = max(float(max_risk_pct or 0), 0.0)
    except (TypeError, ValueError):
        return notional_value
    if equity_f <= 0 or notional_f <= 0 or sl_f <= 0 or cap_f <= 0:
        return notional_value

    hard_risk_cap = equity_f * cap_f
    projected_loss = notional_f * sl_f
    if projected_loss <= hard_risk_cap:
        return notional_f
    return hard_risk_cap / sl_f


@dataclass
class PositionSizingInput:
    symbol: str
    side: str
    price: float
    confidence: float
    total_equity: float
    available_balance: float
    requested_leverage: Optional[float] = None
    requested_position_pct: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    volatility_pct: float = 0.015
    tier: str = "mid"
    trade_nature: str = "swing"
    market_regime: str = "unknown"
    risk_level: str = "medium"
    tier_position_cap_pct: float = 0.0
    # 单仓名义占权益上限覆盖（None=按 tier 默认 cap）。
    # 独立路径（如 ScalpRouter）可传大值绕过 short tier 的 8% 分散上限——
    # 该 cap 是给主链路多策略分散设计的，短线高杠杆小保证金策略不适用。
    position_cap_override: Optional[float] = None
    size_multiplier: float = 1.0
    leverage_cap: Optional[int] = None
    # 多周期共振仓位缩放（2026-06-24 修复"算而不用"）。
    # 来自 MultiFreqAlignment.recommended_position_scale：
    #   aligned(共振) = 1.15 / divergent(偏离) = 0.80 / conflicting(冲突) = 0.50 / unknown = 1.0
    # 之前这个系数被算出但从未参与仓位计算，导致多周期冲突时仍按正常仓位开仓。
    alignment_scale: float = 1.0


@dataclass
class PositionSizingPlan:
    symbol: str
    side: str
    leverage: int
    position_pct: float
    notional_usd: float
    margin_usd: float
    max_loss_usd: float
    stop_loss_pct: float
    source: str
    reasons: List[str] = field(default_factory=list)
    # Phase 3: 自 TradePlannerAgent 并入的追踪止损参考（执行层可选用）
    trailing_activation_pct: float = 0.0
    trailing_distance_pct: float = 0.0
    breakeven_activation_pct: float = 0.0

    def to_decision_fields(self) -> Dict[str, object]:
        return {
            "leverage": self.leverage,
            "position_pct": self.position_pct,
            "_sizing_notional_usd": self.notional_usd,
            "_sizing_margin_usd": self.margin_usd,
            "_sizing_max_loss_usd": self.max_loss_usd,
            "_sizing_source": self.source,
            "_sizing_reasons": list(self.reasons),
            "_respect_sizing_plan": True,
            "_trailing_activation_pct": self.trailing_activation_pct,
            "_trailing_distance_pct": self.trailing_distance_pct,
            "_breakeven_activation_pct": self.breakeven_activation_pct,
        }


class PositionSizingAgent:
    """Risk-budget based position sizing.

    The agent accepts AI suggestions, but caps them with deterministic risk
    math. This keeps the final plan auditable and prevents later layers from
    independently reinventing sizing.
    """

    # short tier 预算降至 8%（绩效归因：short/scalp 累计亏损）
    _TIER_CAP = {"short": 0.08, "mid": 0.18, "long": 0.22}
    _NATURE_SL_FLOOR = {
        "scalp": 0.012,
        "intraday": 0.018,
        "swing": 0.025,
        "trend_follow": 0.040,
        "position": 0.050,
    }
    # Phase 3: TradePlannerAgent._calculate_position_sizing 并入
    _NATURE_SIZE_MULT = {
        "scalp": 0.6,
        "intraday": 0.8,
        "swing": 1.0,
        "position": 1.2,
        "trend_follow": 1.3,
    }
    _VOL_SIZE_MULT = (
        (0.0, 0.01, 1.3),
        (0.01, 0.02, 1.0),
        (0.02, 0.03, 0.7),
        (0.03, 0.05, 0.5),
        (0.05, 1.0, 0.3),
    )
    _TRAILING_CONFIG = {
        "scalp": {"act": 0.008, "dist": 0.004, "be": 0.005},
        "intraday": {"act": 0.012, "dist": 0.008, "be": 0.008},
        "swing": {"act": 0.020, "dist": 0.012, "be": 0.015},
        "position": {"act": 0.030, "dist": 0.020, "be": 0.020},
        "trend_follow": {"act": 0.040, "dist": 0.025, "be": 0.025},
    }

    def build_plan(self, ctx: PositionSizingInput) -> PositionSizingPlan:
        price = max(float(ctx.price or 0), 0.0)
        available = max(float(ctx.available_balance or 0), 0.0)
        equity = max(float(ctx.total_equity or available or 0), 0.0)
        confidence = self._normalize_confidence(ctx.confidence)
        reasons: List[str] = []

        sl_pct = self._resolve_stop_loss_pct(ctx)
        risk_pct = self._risk_budget_pct(confidence, ctx.risk_level, ctx.market_regime)
        risk_pct = self._apply_planner_adjustments(ctx, risk_pct, reasons)
        risk_budget = equity * risk_pct

        requested_pct = self._normalize_position_pct(ctx.requested_position_pct)
        leverage = self._resolve_leverage(ctx, confidence, sl_pct, reasons)
        if ctx.leverage_cap is not None and leverage > ctx.leverage_cap:
            reasons.append(f"risk_lev_cap {leverage}->{ctx.leverage_cap}")
            leverage = max(5, int(ctx.leverage_cap))

        cap_pct = self._position_cap_pct(ctx)
        if requested_pct is not None:
            target_pct = requested_pct
            source = "ai_strategy"
            reasons.append(f"ai_pct={target_pct:.2%}")
        else:
            target_notional_by_risk = risk_budget / sl_pct if sl_pct > 0 else 0
            target_pct = target_notional_by_risk / available if available > 0 else 0
            source = "risk_budget"
            reasons.append(f"risk_budget={risk_pct:.2%}")

        target_pct = max(0.0, min(target_pct, cap_pct))
        size_mult = max(0.3, min(1.0, float(ctx.size_multiplier or 1.0)))
        if size_mult < 1.0:
            target_pct *= size_mult
            reasons.append(f"risk_size_mult×{size_mult:.2f}")

        # 多周期共振仓位缩放（2026-06-24 修复"算而不用"）。
        # alignment_scale: aligned=1.15 / divergent=0.80 / conflicting=0.50 / unknown=1.0
        # 钳到 [0.4, 1.2] 安全区间，冲突时最多砍到 40% 仓位，共振时最多加 20%。
        # 这是"值不值得冒险"的核心：短中期矛盾时自动降仓，避免在周期对立时重仓冒险。
        align_scale = max(0.4, min(1.2, float(ctx.alignment_scale or 1.0)))
        if align_scale < 1.0:
            target_pct *= align_scale
            reasons.append(f"周期对齐缩仓×{align_scale:.2f}(冲突/偏离)")
        elif align_scale > 1.0:
            target_pct *= align_scale
            reasons.append(f"周期共振加仓×{align_scale:.2f}")

        notional = available * target_pct

        # Risk budget is a hard cap. AI can request less, but not risk more.
        max_notional_by_loss = risk_budget / sl_pct if sl_pct > 0 else notional
        if max_notional_by_loss > 0 and notional > max_notional_by_loss:
            reasons.append(
                f"risk_cap {notional:.0f}->{max_notional_by_loss:.0f}"
            )
            notional = max_notional_by_loss
            target_pct = notional / available if available > 0 else 0.0

        # ── V5 单笔风险硬顶：max_loss ≤ 权益 × V5_MAX_TRADE_RISK_PCT ──
        # 最后一道闸（兜住 ai_pct / size_mult / sl 解析等任何路径的漏算），
        # 杜绝单笔 -38k（权益 7%）级别的灾难单。
        try:
            from backend.config.settings import V5_MAX_TRADE_RISK_PCT
        except Exception:
            V5_MAX_TRADE_RISK_PCT = 0.015
        _capped_notional = clamp_position_by_risk_cap(
            equity=equity, notional_value=notional, sl_pct=sl_pct,
            max_risk_pct=float(V5_MAX_TRADE_RISK_PCT),
        )
        if _capped_notional < notional:
            reasons.append(
                f"v5_hard_risk_cap {notional:.0f}->{_capped_notional:.0f}"
                f"(max_loss≤{V5_MAX_TRADE_RISK_PCT:.1%}equity)"
            )
            notional = _capped_notional
            target_pct = notional / available if available > 0 else 0.0

        margin = notional / leverage if leverage > 0 else notional
        max_loss = notional * sl_pct

        trail = self._trailing_config(ctx.trade_nature, ctx.volatility_pct)
        plan = PositionSizingPlan(
            symbol=ctx.symbol,
            side=ctx.side,
            leverage=leverage,
            position_pct=round(max(0.0, min(target_pct, cap_pct)), 6),
            notional_usd=round(notional, 2),
            margin_usd=round(margin, 2),
            max_loss_usd=round(max_loss, 2),
            stop_loss_pct=round(sl_pct, 6),
            source=source,
            reasons=reasons,
            trailing_activation_pct=trail["act"],
            trailing_distance_pct=trail["dist"],
            breakeven_activation_pct=trail["be"],
        )
        logger.info(
            "[SizingAgent] %s %s lev=%sx pct=%.2f%% notional=$%.0f "
            "margin=$%.0f max_loss=$%.0f source=%s reasons=%s",
            plan.symbol,
            plan.side,
            plan.leverage,
            plan.position_pct * 100,
            plan.notional_usd,
            plan.margin_usd,
            plan.max_loss_usd,
            plan.source,
            ",".join(plan.reasons[:4]),
        )
        return plan

    @staticmethod
    def _normalize_confidence(confidence: float) -> float:
        conf = float(confidence or 0)
        if conf > 1:
            conf /= 100.0
        return max(0.0, min(1.0, conf))

    @staticmethod
    def _normalize_position_pct(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        try:
            pct = float(value)
        except (TypeError, ValueError):
            return None
        if pct <= 0:
            return None
        if pct > 1.0:
            pct /= 100.0
        return max(0.0, min(0.35, pct))

    def _resolve_stop_loss_pct(self, ctx: PositionSizingInput) -> float:
        price = float(ctx.price or 0)
        sl = float(ctx.stop_loss_price or 0)
        if price > 0 and sl > 0:
            dist = abs(price - sl) / price
            if dist > 0:
                return max(0.003, min(0.25, dist))

        floor = self._NATURE_SL_FLOOR.get((ctx.trade_nature or "").lower(), 0.025)
        vol = max(float(ctx.volatility_pct or 0.0), floor)
        return max(0.003, min(0.25, vol))

    @staticmethod
    def _risk_budget_pct(confidence: float, risk_level: str, market_regime: str) -> float:
        if confidence >= 0.85:
            base = 0.012
        elif confidence >= 0.75:
            base = 0.010
        elif confidence >= 0.60:
            base = 0.007
        else:
            base = 0.005

        risk_mult = {
            "low": 1.15,
            "medium": 1.0,
            "high": 0.65,
            "critical": 0.35,
        }.get((risk_level or "medium").lower(), 1.0)
        regime_mult = 0.75 if "volatile" in (market_regime or "").lower() else 1.0
        return max(0.0025, min(0.015, base * risk_mult * regime_mult))

    def _resolve_leverage(
        self,
        ctx: PositionSizingInput,
        confidence: float,
        sl_pct: float,
        reasons: List[str],
    ) -> int:
        """V5 重写：波动率+置信度连续映射杠杆 2-20x。

        历史问题：AI 请求值被原样采纳（恒定 15x），波动率和确信度完全
        不参与定杠杆。现在确定性公式为主，AI 建议只能在 ±30% 内修正。
        """
        # 1) 波动率基准：vol 1.5% → ~6.7x；vol 0.5% → 20x；vol 3% → ~3.3x
        # 2026-06-22: 分子受本金影响——小本金分子大（允许更高 base leverage），
        # 大本金分子小（压杠杆保本）。衰减幂 0.3（比动态杠杆的 0.5 温和，避免重复打折过狠）。
        vol = max(0.004, float(ctx.volatility_pct or 0.015))
        _equity = max(float(ctx.total_equity or 0), 1.0)
        try:
            from backend.config.settings import DYNAMIC_LEVERAGE_EQUITY_REF
            _ref = float(DYNAMIC_LEVERAGE_EQUITY_REF)
        except Exception:
            _ref = 5000.0
        _mol_mult = max(0.6, min(2.0, (_ref / _equity) ** 0.3))
        _molecule = 0.10 * _mol_mult
        base = _molecule / vol

        # 2) 置信度连续乘数：conf 0.50 → ×0.70；0.70 → ×0.94；0.90 → ×1.18
        conf_mult = 0.7 + max(0.0, min(0.5, confidence - 0.5)) * 1.2

        # 3) 性质/风险修正
        nature = (ctx.trade_nature or "swing").lower()
        if nature in ("position", "trend_follow"):
            conf_mult *= 0.85  # 长持仓暴露时间长，降杠杆
        if (ctx.risk_level or "").lower() in ("high", "critical"):
            conf_mult *= 0.6
        if "volatile" in (ctx.market_regime or "").lower():
            conf_mult *= 0.8

        computed = base * conf_mult
        reasons.append(f"v5_lev mol={_molecule:.3f}×conf{conf_mult:.2f}={computed:.1f}(eq=${_equity:.0f})")

        # 4) AI 建议只允许在确定性值 ±30% 内修正
        requested = ctx.requested_leverage
        try:
            ai_lev = float(requested) if requested and float(requested) > 0 else None
        except (TypeError, ValueError):
            ai_lev = None
        if ai_lev is not None:
            bounded = max(computed * 0.7, min(computed * 1.3, ai_lev))
            if abs(bounded - ai_lev) > 0.5:
                reasons.append(f"ai_lev_bounded {ai_lev:.0f}->{bounded:.1f}")
            else:
                reasons.append(f"ai_lev={ai_lev:.0f}")
            computed = bounded

        lev = int(round(computed))

        # 5) 杠杆×SL 乘积 > 50% 太脆弱（一次 SL 打掉一半保证金）
        if sl_pct > 0:
            max_by_sl = max(2, int(0.50 / sl_pct))
            if lev > max_by_sl:
                reasons.append(f"sl_cap {lev}->{max_by_sl}")
                lev = max_by_sl

        return max(2, min(20, lev))

    def _apply_planner_adjustments(
        self, ctx: PositionSizingInput, risk_pct: float, reasons: List[str],
    ) -> float:
        """TradePlanner 波动率/性质乘数并入风险预算。"""
        vol = float(ctx.volatility_pct or 0.015)
        vol_mult = 1.0
        for lo, hi, mult in self._VOL_SIZE_MULT:
            if lo <= vol < hi:
                vol_mult = mult
                break
        nature_mult = self._NATURE_SIZE_MULT.get(
            (ctx.trade_nature or "swing").lower(), 1.0,
        )
        adjusted = risk_pct * vol_mult * nature_mult
        reasons.append(f"vol×{vol_mult:.1f},nature×{nature_mult:.1f}")
        return max(0.0025, min(0.015, adjusted))

    def _trailing_config(self, trade_nature: str, volatility_pct: float) -> Dict[str, float]:
        cfg = self._TRAILING_CONFIG.get(
            (trade_nature or "swing").lower(),
            self._TRAILING_CONFIG["swing"],
        )
        vol_adj = max(1.0, float(volatility_pct or 0.015) / 0.015)
        return {
            "act": round(cfg["act"] * vol_adj, 4),
            "dist": round(cfg["dist"] * vol_adj, 4),
            "be": round(cfg["be"] * vol_adj, 4),
        }

    def _position_cap_pct(self, ctx: PositionSizingInput) -> float:
        tier = (ctx.tier or "mid").lower()
        if ctx.position_cap_override is not None and ctx.position_cap_override > 0:
            cap = max(0.01, min(5.0, float(ctx.position_cap_override)))
        else:
            cap = self._TIER_CAP.get(tier, 0.18)
            if ctx.tier_position_cap_pct and ctx.tier_position_cap_pct > 0:
                cap = min(cap, float(ctx.tier_position_cap_pct))
        if (ctx.risk_level or "").lower() in ("high", "critical"):
            cap *= 0.6
        _upper = 5.0 if ctx.position_cap_override is not None else 0.35
        return max(0.01, min(_upper, cap))


position_sizing_agent = PositionSizingAgent()
