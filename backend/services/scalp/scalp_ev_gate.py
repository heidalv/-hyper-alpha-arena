"""ScalpEvGate — 手续费感知的期望值闸门（阶段一 1.1，最高优先·核心）。

短线亏损的头号结构性根因：开仓只判断"因子分/置信度是否过线"，从不校验
"这笔交易在扣除往返手续费+滑点后，数学期望是否为正"。实盘往返成本 ~0.17%+滑点，
胜率仅 ~42%，于是"多而烂"的交易把账户慢慢磨亏。

本闸门在下单前计算每笔交易相对名义仓位的期望收益率：

    EV_pct = p_win × (tp_pct × tp实现率)
             − (1 − p_win) × (sl_pct × sl实现率)
             − 往返成本(手续费+滑点)

- `p_win`：校准后的胜率，来自 `scalp_confidence_calibrator`（冷启动回退线性映射）。
- `tp_pct / sl_pct`：本次交易计划的止盈/止损价格变动幅度（notional 口径）。
- 实现率：真实交易很少吃满 TP（分批/追踪/超时），亏损往往吃满——用可配置系数修正。
- 往返成本：复用 `fee_guard.estimate_breakeven_move`（含往返手续费+滑点，按交易所费率）。

EV_pct 是"相对名义仓位"的期望，与杠杆无关（杠杆等比放大盈亏，不改期望正负）。
只有 `EV_pct ≥ EV_MIN` 才放行。全程 flag 门控（`SCALP_EV_GATE_ENABLED`），
默认开启，可秒回滚。
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
    reason: str = ""
    breakdown: Dict[str, Any] = field(default_factory=dict)


class ScalpEvGate:
    """开仓前置的期望值闸门（单例，毫秒级，不调 LLM）。"""

    _instance: Optional["ScalpEvGate"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._pass_count = 0
            cls._instance._block_count = 0
            cls._instance._last_reason = ""
            # 按策略标签("trend"/"ranging_mr")分开计数，方便观察 MR 独立口径
            # 生效后放行率是否真的动起来了（2026-07-11）。
            cls._instance._by_tag: Dict[str, Dict[str, int]] = {}
        return cls._instance

    @staticmethod
    def _cfg(name: str, default):
        from backend.config import settings as _s
        return getattr(_s, name, default)

    def get_stats(self) -> Dict[str, Any]:
        """EV 闸门放行率快照（供健康视图/验收使用）。"""
        total = self._pass_count + self._block_count
        by_tag = {
            tag: {**c, "pass_rate": round(c["pass"] / max(c["pass"] + c["block"], 1), 4)}
            for tag, c in self._by_tag.items()
        }
        return {
            "pass_count": self._pass_count,
            "block_count": self._block_count,
            "total": total,
            "pass_rate": round(self._pass_count / total, 4) if total else None,
            "last_reason": self._last_reason,
            "by_strategy": by_tag,
        }

    def evaluate(
        self,
        symbol: str,
        factor_score: float,
        direction: str,
        tp_pct: float,
        sl_pct: float,
        notional_usd: float,
        exchange: Optional[str] = None,
        p_win_override: Optional[float] = None,
        strategy_tag: str = "trend",
        mode: str = "paper",
        funding_rate: float = 0.0,
    ) -> EvDecision:
        """计算并裁决本次开仓的期望值。

        Args:
            symbol: 交易对
            factor_score: 因子总分（喂给校准器换算 p_win）
            direction: long/short
            tp_pct: 计划止盈幅度（价格变动比例，>0）
            sl_pct: 计划止损幅度（价格变动比例，>0）
            notional_usd: 名义仓位价值（用于滑点分档估算）
            exchange: 交易所名（None 回退 hyperliquid 费率）
            p_win_override: 外部已算好的校准胜率（可选，避免重复计算）
            strategy_tag: "trend"(默认，原趋势跟随) / "ranging_mr"(震荡均值回归)。
                决定用哪一套止盈实现率折扣 + 哪一份独立校准模型（2026-07-11，见
                settings.py 对应注释：MR 止盈贴边缘、多为固定小目标，一般能吃满，
                不该套用趋势打法"常被中途砍仓"校准出的 0.55 折扣）。
            mode: "paper"/"live"——冷启动豁免(见下)只在 paper 且校准器还没攒够真实
                样本时生效，live 恒不豁免。

        Returns:
            EvDecision
        """
        enabled = bool(self._cfg("SCALP_EV_GATE_ENABLED", True))
        ev_min = float(self._cfg("SCALP_EV_MIN_PCT", 0.0) or 0.0)
        # [2026-07-10 校准] 基于真实57笔数据：
        # TP 实现率从 0.75 降到 0.55（大量盈利单被 master_running_reduce 中途砍仓，实际只吃到 TP 的~55%）
        # SL 实现率保持 1.0（亏损单基本吃满 SL）
        # [2026-07-11] MR 单独一档：贴区间边缘的固定小目标一般能吃满，不像趋势单会被
        # master_running_reduce 中途砍仓，用更贴近真实的 0.85 折扣，而非硬套趋势口径。
        if strategy_tag == "ranging_mr":
            tp_real = float(self._cfg("SCALP_MR_EV_TP_REALIZATION", 0.85) or 0.85)
        else:
            tp_real = float(self._cfg("SCALP_EV_TP_REALIZATION", 0.55) or 0.55)
        sl_real = float(self._cfg("SCALP_EV_SL_REALIZATION", 1.0) or 1.0)

        tp = float(tp_pct or 0.0)
        sl = float(sl_pct or 0.0)

        # 无有效 tp/sl → 无法评估期望；短线没有明确风控目标本就该拒绝。
        if tp <= 0 or sl <= 0:
            reason = f"缺少有效tp/sl(tp={tp:.4f} sl={sl:.4f})，无法评估EV"
            if not enabled:
                return EvDecision(allowed=True, reason="EV闸门关闭(" + reason + ")")
            return EvDecision(
                allowed=False, tp_pct=tp, sl_pct=sl,
                reason=reason, ev_min=ev_min,
            )

        # p_win：优先用外部传入，否则问校准器；可选与 usable meta 软混合
        p_win = p_win_override
        p_src = "override"
        if p_win is None:
            try:
                from backend.services.scalp.scalp_confidence_calibrator import (
                    scalp_confidence_calibrator,
                )
                _cal = scalp_confidence_calibrator.estimate_p_win(
                    symbol, factor_score, direction, strategy_tag=strategy_tag,
                )
                p_win = _cal.p_win
                p_src = _cal.source
            except Exception as e:
                logger.debug(f"[ScalpEvGate] {symbol} 校准器失败，用保守回退 p_win: {e}")
                p_win = 0.50 if strategy_tag == "ranging_mr" else 0.42
                p_src = "fallback_const"

        # P0-4B：仅 SCALP_META_IN_EV=1 且模型 usable 时软混入 meta p_win（默认关）
        try:
            import os as _os
            _meta_in_ev = (_os.getenv("SCALP_META_IN_EV", "0") or "0").strip().lower() in (
                "1", "true", "yes", "on",
            )
            if _meta_in_ev and p_win_override is None:
                from backend.services.scalp_meta_trainer import predict_win_prob
                _meta_p = predict_win_prob(
                    {
                        "factor_score": float(factor_score or 0),
                        "direction": str(direction or ""),
                    },
                    require_usable=True,
                )
                if _meta_p is not None:
                    _blend = float(self._cfg("SCALP_META_EV_BLEND", 0.35) or 0.35)
                    _blend = max(0.0, min(1.0, _blend))
                    # min 更保守：差信号更容易被压低；blend 做平滑
                    _mixed = (1.0 - _blend) * float(p_win) + _blend * float(_meta_p)
                    p_win = min(float(p_win), float(_mixed))
                    p_src = f"{p_src}+meta_soft"
        except Exception as _me:
            logger.debug(f"[ScalpEvGate] {symbol} meta 软接入跳过: {_me}")

        # [2026-08-13 P1-6] meta 硬过滤：usable 模型存在且预测「会赢」概率低于
        # 门槛 → 直接拒绝开仓（SCALP_META_HARD_FILTER 默认开，可回滚）。
        # 与上方软接入互补：软接入只压低 p_win，硬过滤直接一票否决。
        try:
            _meta_hard = (_os.getenv("SCALP_META_HARD_FILTER", "1") or "1").strip().lower() in (
                "1", "true", "yes", "on",
            )
            if _meta_hard:
                from backend.services.scalp_meta_trainer import predict_win_prob
                _meta_hp = predict_win_prob(
                    {
                        "factor_score": float(factor_score or 0),
                        "direction": str(direction or ""),
                    },
                    require_usable=True,
                )
                _min_pwin = float(self._cfg("SCALP_META_MIN_PWIN", 0.5) or 0.5)
                if _meta_hp is not None and float(_meta_hp) < _min_pwin:
                    return EvDecision(
                        allowed=False, tp_pct=tp, sl_pct=sl,
                        reason=(
                            f"meta硬过滤: usable模型胜率 {float(_meta_hp):.3f} "
                            f"< 门槛 {_min_pwin}"
                        ),
                        ev_min=ev_min,
                    )
        except Exception as _mh:
            logger.debug(f"[ScalpEvGate] {symbol} meta 硬过滤跳过: {_mh}")

        p_win = max(0.01, min(0.99, float(p_win)))

        # 往返成本（手续费 + 滑点 + 资金费率，按交易所费率）
        # [P0-4 2026-07-30] 新增资金费率成本（原仅费+滑，做多高费率时EV高估）
        try:
            from backend.services.fee_guard import fee_guard
            round_trip_cost = fee_guard.estimate_breakeven_move(
                notional_usd=float(notional_usd or 1000.0),
                is_maker=False,
                trade_nature="intraday",
                exchange=exchange,
            )
        except Exception as e:
            logger.debug(f"[ScalpEvGate] {symbol} 成本估算失败，用保守回退: {e}")
            round_trip_cost = 0.0021

        # 资金费率持仓成本（8h结算一次，scalp平均持仓0.5h）
        # [2026-07-30 修复] 原代码引用未定义的 market_data 变量（NameError 被 except 兜底，
        # funding_cost 从未生效→EV 偏高）。改为从参数接收 funding_rate。
        _fr = float(funding_rate or 0)
        _expected_hold_hours = 0.5
        funding_cost = abs(_fr) * _expected_hold_hours / 8  # 8h结算周期
        round_trip_cost += funding_cost

        eff_win = tp * tp_real
        eff_loss = sl * sl_real
        ev_pct = p_win * eff_win - (1.0 - p_win) * eff_loss - round_trip_cost

        # ── 冷启动数据积累豁免（2026-07-11）──
        # 只在 paper + 校准器还没攒够真实样本(p_src 仍是 cold_linear/fallback，不是
        # 已用真实成交拟合出的 calibrated)时，给门槛让出一点空间：让 RR 尚可、只是被
        # 保守折扣打到临界负的信号先跑起来，攒真实结果；一旦攒够样本自动切
        # calibrated，本豁免自动失效，不需要手动关。
        cold_start = p_src in ("cold_linear", "fallback_const")
        allowance = (
            float(self._cfg("SCALP_EV_COLD_START_ALLOWANCE_PCT", 0.0025) or 0.0)
            if (mode or "paper").lower() == "paper" and cold_start
            else 0.0
        )
        effective_ev_min = ev_min - allowance

        allowed = ev_pct >= effective_ev_min
        reason = (
            f"EV={ev_pct:+.4%} {'≥' if allowed else '<'} 门槛{effective_ev_min:+.4%}"
            f"{f'(基准{ev_min:+.4%}-冷启动豁免{allowance:.4%})' if allowance else ''} | "
            f"p_win={p_win:.3f}({p_src}) tp={tp:.3%}×{tp_real:.2f} "
            f"sl={sl:.3%}×{sl_real:.2f} 成本={round_trip_cost:.3%} strategy={strategy_tag}"
        )

        decision = EvDecision(
            allowed=allowed,
            ev_pct=round(ev_pct, 6),
            p_win=round(p_win, 4),
            tp_pct=tp,
            sl_pct=sl,
            round_trip_cost=round(round_trip_cost, 6),
            ev_min=effective_ev_min,
            p_win_source=p_src,
            reason=reason,
            breakdown={
                "eff_win": round(eff_win, 6),
                "eff_loss": round(eff_loss, 6),
                "p_win_source": p_src,
                "ev_min_base": ev_min,
                "cold_start_allowance": allowance,
            },
        )

        # 统计放行率（含影子模式，按"若启用是否会放行"计），并按策略标签分开累计。
        self._last_reason = reason
        _tag_counter = self._by_tag.setdefault(strategy_tag, {"pass": 0, "block": 0})
        if allowed:
            self._pass_count += 1
            _tag_counter["pass"] += 1
        else:
            self._block_count += 1
            _tag_counter["block"] += 1

        # flag 关闭时只记录不拦截（影子模式，方便对比放行率）
        if not enabled:
            if not allowed:
                logger.info(f"[ScalpEvGate] {symbol} [影子·未拦截] {reason}")
            decision.allowed = True
            return decision

        if not allowed:
            logger.info(f"[ScalpEvGate] {symbol} 期望值不足拦截: {reason}")
        return decision


# 全局单例
scalp_ev_gate = ScalpEvGate()
