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
            # [§53] 影子放行计数 / 已提示"开始硬拦"标记
            cls._instance._shadow_warn: Dict[str, int] = {}
            cls._instance._enforcing_notified: Dict[str, bool] = {}
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
        calib_nature: Optional[str] = None,
    ) -> EvDecision:
        """计算并裁决本次中长线开仓的期望值。

        [P7 执行 2026-09-10] `calib_nature`：**校准器/门槛所依据的赛道语义**，
        与执行语义（`nature`，V5 归一后=trend_follow）分开。背景（§53.1/§53.2）：
        `pipeline.evaluate_midlong_open` 先把 `trade_nature='swing'` 归一成 `trend_follow`
        才调本闸 ⇒ 闸问的是**未校准的 trend 校准器** ⇒ `p_src != calibrated` ⇒
        因 `MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION=true` 而**永久影子放行**（563/563 全放行、
        0 拦截），而那时**真正已校准的 swing 校准器**从未被咨询。传入 `calib_nature` 后，
        mid 赛道会用自己的校准器与自己的 EV 门槛，闸才可能真正生效。
        """
        nat = (nature or "swing").lower()
        # 校准/门槛所依据的赛道：优先显式传入，否则退回执行语义（保持旧行为可复现）
        nat_calib = (calib_nature or nat).lower()
        prefix = _nature_prefix(nat_calib)
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
                _cal = get_calibrator_for_nature(nat_calib).estimate_p_win(symbol, score, direction)
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

        # [P7 执行 2026-09-10] **赛道强制开关**：接线修好之后，mid 赛道一旦校准生效就会
        # 从"永久影子"瞬间变成"硬拦"。§53.2 实测该口径下 mid 提案 EV ≈ −2.60% ⇒ 硬拦等于
        # **关闭 mid 车道**，属重大行为变更，故强制与否必须是一个**显式**开关，
        # 而不是接线修好的副作用：
        #   MIDLONG_EV_ENFORCE_MID=false（默认）⇒ mid 赛道接线后仍只**影子记录**，
        #       但此时影子数据是**用正确校准器**算的，可直接回答"若强制会拦多少"；
        #   =true ⇒ 允许 mid 赛道按 EV 硬拦。
        # 其它赛道（trend_follow/position）行为不变。
        enforce_mid = bool(self._cfg("MIDLONG_EV_ENFORCE_MID", False))
        shadow_lane = (nat_calib == "swing") and not enforce_mid
        shadow = shadow_cold or shadow_lane

        reason = (
            f"EV={ev_pct:+.4%} {'≥' if allowed else '<'} 门槛{ev_min:+.4%} | "
            f"p_win={p_win:.3f}({p_src}) tp={tp:.3%}×{tp_real:.2f} "
            f"sl={sl:.3%}×{sl_real:.2f} 成本={round_trip_cost:.3%} "
            f"[exec={nat}/calib={nat_calib}]"
            + (f" 影子:calib_required={shadow_cold}/lane_switch_off={shadow_lane}" if shadow else "")
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
            breakdown={
                "eff_win": round(eff_win, 6),
                "eff_loss": round(eff_loss, 6),
                "calib_nature": nat_calib,
                "ev_prefix": prefix,
                "shadow_cold": shadow_cold,
                "shadow_lane_switch": shadow_lane,
            },
        )

        # 统计（影子模式也按"若启用是否会放行"计）
        self._bump(nat, allowed, reason)

        if not enabled:
            if not allowed:
                logger.info(f"[MidLongEvGate] {symbol} [影子·全局关] {reason}")
            decision.allowed = True
            return decision

        if shadow:
            # [§53 修复 2026-09-10] **影子放行必须可见**（原来只在 `not allowed` 时打一行
            # INFO：若未校准但恰好 EV 达标 → 完全静默；且生产日志无法区分"闸生效了"与
            # "闸根本没生效"）。实测：本闸自上线以来 563 次评估 **全部**走此分支、
            # 拦截 0 笔（§53.1）。改为限流 WARNING + 状态变化 INFO，纯日志零行为变化。
            # [P7 执行 2026-09-10] 现在有两种影子原因：冷启动（校准未生效）/ 赛道开关未开。
            # 后者是**故意**的（mid 车道强制与否由用户显式决定），日志里必须能区分，
            # 否则运维会以为"闸又没接线"。
            self._warn_shadow(nat, symbol, reason, not allowed)
            decision.allowed = True
            if shadow_cold:
                decision.breakdown["shadow_cold_start"] = True
            if shadow_lane:
                decision.breakdown["shadow_lane_switch"] = True
            return decision

        if p_src == "calibrated":
            self._note_enforcing(nat)

        if not allowed:
            logger.info(f"[MidLongEvGate] {symbol} 期望值不足拦截: {reason}")
        return decision

    # ── [§53] 可观测性（纯日志，不改裁决）──
    def _warn_shadow(self, nature: str, symbol: str, reason: str, would_block: bool) -> None:
        """限流上报"闸未生效"：每个 nature 首次必报，之后每 200 次报一次。"""
        try:
            n = int(self._shadow_warn.get(nature, 0)) + 1
            self._shadow_warn[nature] = n
            if n == 1 or n % 200 == 0:
                logger.warning(
                    "[MidLongEvGate] %s 本闸**影子放行**（第 %d 次；本次 EV 判定=%s，"
                    "已无条件放行；原因见 reason 里的 影子: 标记）: %s",
                    nature, n, "该拦" if would_block else "可放行", reason,
                )
        except Exception:
            pass

    def _note_enforcing(self, nature: str) -> None:
        """首次看到某 nature 进入 calibrated：显式提示闸已开始硬拦（防无感行为突变）。"""
        try:
            if not self._enforcing_notified.get(nature):
                self._enforcing_notified[nature] = True
                logger.warning(
                    "[MidLongEvGate] %s 校准已生效 → 本闸**开始硬拦**（不再影子放行）",
                    nature,
                )
        except Exception:
            pass


# 全局单例
midlong_ev_gate = MidLongEvGate()
