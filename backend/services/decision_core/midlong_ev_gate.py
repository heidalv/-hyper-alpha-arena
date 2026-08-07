"""MidLongEvGate — 中长线手续费感知期望值闸门（S1-2，泛化自 scalp_ev_gate）。

中长线开仓此前只判断"置信度/评分是否过门槛"，从不校验"扣掉往返手续费+滑点后，
这笔交易的数学期望是否为正"。本闸门在放行前计算相对名义仓位的期望收益率：

    EV_pct = p_win × (tp_pct × tp实现率)
             − (1 − p_win) × (sl_pct × sl实现率)
             − 往返成本(手续费+滑点)

- `p_win`：来自 S1-1 置信度校准器（swing/trend 各一套，冷启动回退线性映射）。
- 实现率：中长线更容易吃满趋势（tp实现率略高），亏损常吃满（sl实现率=1）。
- 往返成本：`fee_guard.estimate_breakeven_move`，按 nature=swing/trend_follow 取滑点档。

EV_pct 与杠杆无关。仅 `EV_pct ≥ {NATURE}_EV_MIN_PCT` 才放行。总开关
`MIDLONG_EV_GATE_ENABLED`，关闭时为影子模式（记录不拦截），可秒回滚。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class EvDecision:
    """EV 闸门裁决结果。"""
    allowed: bool = True
    ev_pct: float = 0.0
    p_win: float = 0.0
    tp_pct: float = 0.0
    sl_pct: float = 0.0
    round_trip_cost: float = 0.0
    ev_min: float = 0.0
    p_win_source: str = ""
    nature: str = ""
    reason: str = ""
    breakdown: Dict[str, Any] = field(default_factory=dict)


def _nature_prefix(nature: str) -> str:
    """swing→SWING_EV / trend_follow|position→TREND_EV。"""
    n = (nature or "").lower()
    if n in ("trend_follow", "position"):
        return "TREND_EV"
    return "SWING_EV"


class MidLongEvGate:
    """中长线开仓前置期望值闸门（单例，毫秒级，不调 LLM）。"""

    _instance: Optional["MidLongEvGate"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._stats: Dict[str, Dict[str, Any]] = {}
        return cls._instance

    @staticmethod
    def _cfg(name: str, default):
        from backend.config import settings as _s
        return getattr(_s, name, default)

    def _bump(self, nature: str, allowed: bool, reason: str) -> None:
        st = self._stats.setdefault(nature, {"pass": 0, "block": 0, "last_reason": ""})
        st["pass" if allowed else "block"] += 1
        st["last_reason"] = reason

    def get_stats(self) -> Dict[str, Any]:
        """按 nature 的放行率快照（供健康视图/验收）。"""
        out: Dict[str, Any] = {}
        for nature, st in self._stats.items():
            total = st["pass"] + st["block"]
            out[nature] = {
                "pass_count": st["pass"],
                "block_count": st["block"],
                "total": total,
                "pass_rate": round(st["pass"] / total, 4) if total else None,
                "last_reason": st["last_reason"],
            }
        return out

    def evaluate(
        self,
        *,
        nature: str,
        symbol: str,
        score: float,
        direction: str,
        tp_pct: float,
        sl_pct: float,
        notional_usd: float = 2000.0,
        exchange: Optional[str] = None,
        p_win_override: Optional[float] = None,
    ) -> EvDecision:
        """计算并裁决本次中长线开仓的期望值。"""
        nat = (nature or "swing").lower()
        prefix = _nature_prefix(nat)
        enabled = bool(self._cfg("MIDLONG_EV_GATE_ENABLED", True))
        ev_min = float(self._cfg(f"{prefix}_MIN_PCT", 0.0005) or 0.0)
        tp_real = float(self._cfg(f"{prefix}_TP_REALIZATION", 0.70) or 0.70)
        sl_real = float(self._cfg(f"{prefix}_SL_REALIZATION", 1.0) or 1.0)
        fallback_rr = float(self._cfg("MIDLONG_EV_FALLBACK_RR", 2.0) or 2.0)

        tp = float(tp_pct or 0.0)
        sl = float(sl_pct or 0.0)
        # 中长线常缺显式 tp（趋势骑乘）：用 sl×默认RR 兜底，避免因缺 tp 而误拦。
        if sl <= 0:
            sl = 0.04
        if tp <= 0:
            tp = sl * fallback_rr

        # p_win：优先外部传入，否则问对应校准器
        p_win = p_win_override
        p_src = "override"
        if p_win is None:
            try:
                from backend.services.calibration.confidence_calibrator import (
                    get_calibrator_for_nature,
                )
                _cal = get_calibrator_for_nature(nat).estimate_p_win(symbol, score, direction)
                p_win = _cal.p_win
                p_src = _cal.source
            except Exception as e:
                logger.debug(f"[MidLongEvGate] {symbol} 校准器失败，用保守回退 p_win: {e}")
                p_win = 0.45
                p_src = "fallback_const"
        p_win = max(0.01, min(0.99, float(p_win)))

        # 往返成本（手续费 + 滑点，按 nature 取滑点档）
        try:
            from backend.services.fee_guard import fee_guard
            round_trip_cost = fee_guard.estimate_breakeven_move(
                notional_usd=float(notional_usd or 2000.0),
                is_maker=False,
                trade_nature=nat if nat in ("swing", "trend_follow", "position") else "swing",
                exchange=exchange,
            )
        except Exception as e:
            logger.debug(f"[MidLongEvGate] {symbol} 成本估算失败，用保守回退: {e}")
            round_trip_cost = 0.0021

        eff_win = tp * tp_real
        eff_loss = sl * sl_real
        ev_pct = p_win * eff_win - (1.0 - p_win) * eff_loss - round_trip_cost
        allowed = ev_pct >= ev_min

        # 冷启动防死锁：p_win 未经历史校准（cold_linear/回退）时，EV 恒偏保守，
        # 若硬拦会导致"无成交→无样本→永不校准"的死循环。默认仅在校准生效后才硬拦，
        # 之前只影子记录，让样本先积累。可用 MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION 关闭。
        require_cal = bool(self._cfg("MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION", True))
        cold_start = p_src != "calibrated"
        shadow_cold = require_cal and cold_start

        reason = (
            f"EV={ev_pct:+.4%} {'≥' if allowed else '<'} 门槛{ev_min:+.4%} | "
            f"p_win={p_win:.3f}({p_src}) tp={tp:.3%}×{tp_real:.2f} "
            f"sl={sl:.3%}×{sl_real:.2f} 成本={round_trip_cost:.3%} [{nat}]"
        )

        decision = EvDecision(
            allowed=allowed,
            ev_pct=round(ev_pct, 6),
            p_win=round(p_win, 4),
            tp_pct=tp,
            sl_pct=sl,
            round_trip_cost=round(round_trip_cost, 6),
            ev_min=ev_min,
            p_win_source=p_src,
            nature=nat,
            reason=reason,
            breakdown={"eff_win": round(eff_win, 6), "eff_loss": round(eff_loss, 6)},
        )

        # 统计（影子模式也按"若启用是否会放行"计）
        self._bump(nat, allowed, reason)

        if not enabled:
            if not allowed:
                logger.info(f"[MidLongEvGate] {symbol} [影子·全局关] {reason}")
            decision.allowed = True
            return decision

        if shadow_cold:
            if not allowed:
                logger.info(f"[MidLongEvGate] {symbol} [影子·未校准放行] {reason}")
            decision.allowed = True
            decision.breakdown["shadow_cold_start"] = True
            return decision

        if not allowed:
            logger.info(f"[MidLongEvGate] {symbol} 期望值不足拦截: {reason}")
        return decision


# 全局单例
midlong_ev_gate = MidLongEvGate()
