"""
三周期独立离场策略（S2 激活版，2026-07-19）。

每个 tier 有独立的 trailing/分批TP/time_decay/bias 参数，
在 UnifiedExitStateMachine 的动态离场层被调用。

═══════════════════════════════════════════════════════════════════════
S2 修复（对应 04 综合方案 §3.4 / 审计 R6）：
  1. 修 typo: ShortTierExit.evaluate 的 `drawback` → `drawdown`（原 bug 导致 trailing 永不触发）
  2. 加状态追踪: 用 ctx.tp_level_reached 跳过已触发的 TP 档位（原注释说"实际应检查"但没实现）
  3. 接入 LLM exit_plan: 若 ctx.tp_stages 非空，用 LLM 的分档覆盖默认 STAGED_TPS
  4. breakeven 触发后只推一次（用 breakeven_active 标记，避免重复推）
  5. trailing 激活门槛与 tp_level_reached 联动（TP2 触发后才启动 trailing）
═══════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from backend.services.exit.exit_types import (
    ExitAction,
    ExitDecision,
    ExitSource,
    PositionContext,
)


@dataclass(frozen=True)
class StagedTP:
    """单档分批止盈配置。"""
    trigger_pnl_pct: float    # 浮盈达此值触发
    reduce_ratio: float       # 减仓比例（占总仓）


class TierExitStrategy(ABC):
    """每周期独立的离场策略基类。"""

    # 默认 STAGED_TPS（子类覆盖）；若 ctx.tp_stages 非空则用 LLM 的分档
    STAGED_TPS: list[StagedTP] = []
    TRAILING_ATR_MULT: float = 1.5
    BREAKEVEN_TRIGGER_PCT: float = 3.0
    BREAKEVEN_OFFSET_PCT: float = 0.01   # SL 推到 entry + 此百分比
    TRAILING_ACTIVATE_LEVEL: int = 2     # 触发到第几档 TP 后启动 trailing

    @abstractmethod
    def evaluate(self, ctx: PositionContext, *, tp_level_reached: int = 0,
                 breakeven_active: bool = False, trailing_active: bool = False,
                 ) -> Optional[ExitDecision]:
        """评估持仓是否应动态离场。返回 None = 无触发。

        Args:
            tp_level_reached: 已触发的 TP 档位（0/1/2/3）
            breakeven_active: 是否已推过 breakeven
            trailing_active: 是否已启动 trailing
        """
        ...

    def _get_stages(self, ctx: PositionContext) -> list[StagedTP]:
        """获取分档 TP 配置：优先用 LLM 的 tp_stages，否则用默认 STAGED_TPS。"""
        if ctx.tp_stages:
            # LLM 的 tp_stages: [{"pct": 0.06, "close_ratio": 0.30}, ...]
            stages = []
            for s in ctx.tp_stages:
                if isinstance(s, dict):
                    try:
                        pct = float(s.get("pct") or 0) * 100  # LLM 给的是小数(0.06)，转 %
                        ratio = float(s.get("close_ratio") or 0.3)
                        if pct > 0 and ratio > 0:
                            stages.append(StagedTP(trigger_pnl_pct=pct, reduce_ratio=ratio))
                    except Exception:
                        continue
            if stages:
                return stages
        return self.STAGED_TPS

    def _evaluate_staged_tp(self, ctx: PositionContext, tp_level_reached: int,
                            ) -> Optional[ExitDecision]:
        """通用分档 TP 评估（跳过已触发的档位）。"""
        ts = int(time.time() * 1e9)
        stages = self._get_stages(ctx)
        for i, tp in enumerate(stages):
            stage_num = i + 1
            if stage_num <= tp_level_reached:
                continue  # 已触发，跳过
            if ctx.unrealized_pnl_pct >= tp.trigger_pnl_pct:
                return ExitDecision(
                    position_id=ctx.position_id,
                    action=ExitAction.REDUCE.value,
                    qty_ratio=tp.reduce_ratio,
                    reason=f"分批TP#{stage_num} 浮盈{ctx.unrealized_pnl_pct:.1f}%≥{tp.trigger_pnl_pct}%",
                    source=ExitSource.STAGED_TP.value,
                    ts_ns=ts,
                )
        return None

    def _evaluate_breakeven(self, ctx: PositionContext, breakeven_active: bool,
                            ) -> Optional[ExitDecision]:
        """通用 breakeven 评估（已推过则跳过）。"""
        if breakeven_active:
            return None
        ts = int(time.time() * 1e9)
        if ctx.unrealized_pnl_pct >= self.BREAKEVEN_TRIGGER_PCT and ctx.sl_price and ctx.entry_price:
            side_mult = 1 if ctx.side == "long" else -1
            breakeven_sl = ctx.entry_price * (1 + side_mult * self.BREAKEVEN_OFFSET_PCT)
            need_push = (side_mult == 1 and breakeven_sl > ctx.sl_price) or \
                        (side_mult == -1 and breakeven_sl < ctx.sl_price)
            if need_push:
                return ExitDecision(
                    position_id=ctx.position_id,
                    action=ExitAction.TIGHTEN_SL.value,
                    reason=f"保本推进 SL→entry+{self.BREAKEVEN_OFFSET_PCT:.0%}",
                    source=ExitSource.BREAKEVEN.value,
                    new_sl_price=breakeven_sl,
                    ts_ns=ts,
                )
        return None

    def _evaluate_trailing(self, ctx: PositionContext, tp_level_reached: int,
                           trailing_active: bool) -> Optional[ExitDecision]:
        """通用 trailing 评估（需先触发到 TRAILING_ACTIVATE_LEVEL 档）。"""
        ts = int(time.time() * 1e9)
        # 只有触发到指定档位后才启动 trailing
        if tp_level_reached < self.TRAILING_ACTIVATE_LEVEL:
            return None
        if ctx.peak_pnl_pct <= 0 or ctx.atr_pct <= 0:
            return None
        trailing_dist = max(0.5, ctx.atr_pct * self.TRAILING_ATR_MULT)
        drawdown = ctx.peak_pnl_pct - ctx.unrealized_pnl_pct
        if drawdown >= trailing_dist and ctx.unrealized_pnl_pct > 0:
            return ExitDecision(
                position_id=ctx.position_id,
                action=ExitAction.CLOSE.value,
                qty_ratio=1.0,
                reason=f"trailing 回撤{drawdown:.1f}%≥{trailing_dist:.1f}%",
                source=ExitSource.TRAILING.value,
                ts_ns=ts,
            )
        return None


def _atr_as_pct(atr: float) -> float:
    """ctx.atr_pct 有时是小数(0.012)、有时是百分数(1.2)。统一成百分数。"""
    a = float(atr or 0)
    if a <= 0:
        return 0.8
    return a * 100.0 if a < 0.2 else a


class ShortTierExit(TierExitStrategy):
    """短线三阶段：认错 → 保本 → ATR 追踪。分批按 ATR，不再写死 4%/7%。"""

    STAGED_TPS = [
        StagedTP(trigger_pnl_pct=0.8, reduce_ratio=0.25),
        StagedTP(trigger_pnl_pct=1.5, reduce_ratio=0.25),
    ]
    TRAILING_ATR_MULT = 1.0
    BREAKEVEN_TRIGGER_PCT = 0.3
    BREAKEVEN_OFFSET_PCT = 0.0002
    TRAILING_ACTIVATE_LEVEL = 1   # 保本或已分批后才追踪，避免微浮盈来回打脸

    def _get_stages(self, ctx: PositionContext) -> list[StagedTP]:
        if ctx.tp_stages:
            return super()._get_stages(ctx)
        atr = _atr_as_pct(ctx.atr_pct)
        tp1 = max(0.50, min(1.20, 0.8 * atr))
        tp2 = max(0.80, min(2.20, 1.5 * atr))
        if tp2 <= tp1:
            tp2 = min(2.20, tp1 + 0.40)
        return [
            StagedTP(trigger_pnl_pct=tp1, reduce_ratio=0.25),
            StagedTP(trigger_pnl_pct=tp2, reduce_ratio=0.25),
        ]

    def evaluate(self, ctx: PositionContext, *, tp_level_reached: int = 0,
                 breakeven_active: bool = False, trailing_active: bool = False,
                 ) -> Optional[ExitDecision]:
        try:
            if os.getenv("SCALP_EXIT_FAST_CUT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on"):
                _fc_min = float(os.getenv("SCALP_EXIT_FAST_CUT_MIN", "15") or 15)
                # [2026-09-01 短线质量校准] 峰值门槛可调：修复后 44 笔实证
                # fast_cut(time_decay) 占平仓 64%、全小亏(buy avg -0.32/sell -0.12)，
                # 而逃过 15 分钟窗口进入 trailing 的仓位 avg +0.50——15 分钟在
                # ATR 0.6% 的震荡市里把本可发育的仓位过早砍掉。默认放宽到 30 分钟
                # (.env SCALP_EXIT_FAST_CUT_MIN=30)，峰值门槛保持 0.3% 但可调。
                _fc_peak = float(os.getenv("SCALP_EXIT_FAST_CUT_PEAK_PCT", "0.3") or 0.3)
                # [2026-09-02 P2.1] 追加"确实走坏"条件。
                #
                # 原条件只要求 peak<0.3% 且 unrealized<0.3%，于是"微盈微亏来回
                # 震荡、还没发育"的仓位与"方向确实错了"的仓位被一起砍掉。实测
                # 近 30 天 801 笔时间类出场中，453 笔（57%）曾浮盈 >0.2%、272 笔
                # （34%）曾 >0.5% —— 它们本来是赚钱的，被时间赶出场时已回吐。
                #
                # 离线回放（做多+pwin>=0.55，2542 条，分段稳健性 robust）显示，
                # 把有效持仓从 1800s 放到 3600s 净收益 +15.14bp → +33.21bp；而
                # 同批数据里 timeout 出场本身净 +20.87bp 为正 —— 说明"到时平仓"
                # 是在锁利，问题出在**砍得太早太宽**，不在时间出场本身。
                #
                # 故只对明确走坏的仓位认错：unrealized 跌破 -SCALP_EXIT_FAST_CUT_LOSS_PCT。
                # 设为 0 可回到旧行为（无浮盈即砍）。
                _fc_loss = float(os.getenv("SCALP_EXIT_FAST_CUT_LOSS_PCT", "0.2") or 0)
                _no_upside = (
                    (ctx.hold_seconds or 0) >= _fc_min * 60
                    and (ctx.peak_pnl_pct or 0) < _fc_peak
                    and (ctx.unrealized_pnl_pct or 0) < _fc_peak
                )
                # 浮盈口径是百分数：0.3 = 0.3%（旧代码误写成 0.003）
                _gone_bad = (ctx.unrealized_pnl_pct or 0) <= -abs(_fc_loss)
                if _no_upside and (_fc_loss <= 0 or _gone_bad):
                    return ExitDecision(
                        position_id=ctx.position_id,
                        action=ExitAction.CLOSE.value, qty_ratio=1.0,
                        reason="fast_cut: %.0fmin无浮盈且已走坏认错出清"
                               "(peak=%.3f%% upnl=%.3f%%)" % (
                                   _fc_min, ctx.peak_pnl_pct or 0,
                                   ctx.unrealized_pnl_pct or 0,
                               ),
                        source=ExitSource.TIME_DECAY.value,
                        ts_ns=int(time.time() * 1e9),
                    )
        except Exception:
            pass

        flip = self._evaluate_regime_flip(ctx, breakeven_active)
        if flip:
            return flip

        # 先锁本金，再谈分批——PEO 在 v2 开启时会忽略 REDUCE，保本必须先落地。
        be_decision = self._evaluate_breakeven(ctx, breakeven_active)
        if be_decision:
            return be_decision

        tp_decision = self._evaluate_staged_tp(ctx, tp_level_reached)
        if tp_decision:
            return tp_decision

        if breakeven_active or tp_level_reached >= 1:
            trail_decision = self._evaluate_trailing(
                ctx, max(tp_level_reached, 1), trailing_active,
            )
            if trail_decision:
                return trail_decision

        return None

    def _evaluate_regime_flip(self, ctx: PositionContext, breakeven_active: bool,
                              ) -> Optional[ExitDecision]:
        """趋势变震荡/极端：收紧止损，只上移不放宽。"""
        try:
            from backend.services.exit.market_aware_tpsl import hold_dynamic_enabled
            if not hold_dynamic_enabled():
                return None
        except Exception:
            pass
        regime = str(getattr(ctx, "regime", "") or "").lower()
        if regime not in ("ranging", "extreme"):
            return None
        if regime == "extreme" and (ctx.unrealized_pnl_pct or 0) <= 0:
            return None
        if regime == "ranging" and (ctx.peak_pnl_pct or 0) < 0.3 and not breakeven_active:
            return None
        if not ctx.entry_price or ctx.entry_price <= 0:
            return None
        atr = _atr_as_pct(ctx.atr_pct)
        side_mult = 1 if ctx.side == "long" else -1
        # 极端：锁到成本+垫；震荡：峰值回撤 0.7×ATR
        if regime == "extreme":
            new_sl = ctx.entry_price * (1 + side_mult * max(self.BREAKEVEN_OFFSET_PCT, 0.0002))
            why = "行情极端，止损收到成本附近锁利"
        else:
            peak_frac = max(ctx.peak_pnl_pct or 0, ctx.unrealized_pnl_pct or 0) / 100.0
            trail = max(0.004, (atr / 100.0) * 0.7)
            new_sl = ctx.entry_price * (1 + side_mult * max(peak_frac - trail, self.BREAKEVEN_OFFSET_PCT))
            why = "趋势转震荡，收紧追踪"
        if ctx.sl_price:
            if side_mult == 1 and new_sl <= ctx.sl_price:
                return None
            if side_mult == -1 and new_sl >= ctx.sl_price:
                return None
        return ExitDecision(
            position_id=ctx.position_id,
            action=ExitAction.TIGHTEN_SL.value,
            reason=why,
            source=ExitSource.REGIME_FLIP.value,
            new_sl_price=new_sl,
            ts_ns=int(time.time() * 1e9),
        )


class MidTierExit(TierExitStrategy):
    """中线：让利润跑，宽 trailing(1.8×ATR)，多档 TP。"""

    STAGED_TPS = [
        StagedTP(trigger_pnl_pct=6.0, reduce_ratio=0.30),
        StagedTP(trigger_pnl_pct=10.0, reduce_ratio=0.30),
    ]
    TRAILING_ATR_MULT = 1.8
    BREAKEVEN_TRIGGER_PCT = 3.0
    BREAKEVEN_OFFSET_PCT = 0.01   # entry + 1%
    TRAILING_ACTIVATE_LEVEL = 2

    def evaluate(self, ctx: PositionContext, *, tp_level_reached: int = 0,
                 breakeven_active: bool = False, trailing_active: bool = False,
                 ) -> Optional[ExitDecision]:
        # 1. 分批 TP
        tp_decision = self._evaluate_staged_tp(ctx, tp_level_reached)
        if tp_decision:
            return tp_decision

        # 2. breakeven
        be_decision = self._evaluate_breakeven(ctx, breakeven_active)
        if be_decision:
            return be_decision

        # 3. bias 反向退出（4h+1d 双反向 → 减仓 50%）
        if not ctx.trend_4h_aligned and not ctx.trend_1d_aligned:
            ts = int(time.time() * 1e9)
            return ExitDecision(
                position_id=ctx.position_id,
                action=ExitAction.REDUCE.value,
                qty_ratio=0.50,
                reason="中线 bias 反向(4h+1d 双反)，减仓50%",
                source=ExitSource.BIAS_REVERSAL.value,
                ts_ns=ts,
            )

        # 4. trailing
        trail_decision = self._evaluate_trailing(ctx, tp_level_reached, trailing_active)
        if trail_decision:
            return trail_decision

        return None


class LongTierExit(TierExitStrategy):
    """长线：最大化趋势利润，最宽容，3 档战略 TP。"""

    STAGED_TPS = [
        StagedTP(trigger_pnl_pct=8.0, reduce_ratio=0.25),
        StagedTP(trigger_pnl_pct=15.0, reduce_ratio=0.35),
        StagedTP(trigger_pnl_pct=25.0, reduce_ratio=0.40),
    ]
    TRAILING_ATR_MULT = 2.0
    BREAKEVEN_TRIGGER_PCT = 5.0
    BREAKEVEN_OFFSET_PCT = 0.02   # entry + 2%
    TRAILING_ACTIVATE_LEVEL = 2   # TP2 触发后启动 trailing

    def evaluate(self, ctx: PositionContext, *, tp_level_reached: int = 0,
                 breakeven_active: bool = False, trailing_active: bool = False,
                 ) -> Optional[ExitDecision]:
        # 1. 分档战略 TP
        tp_decision = self._evaluate_staged_tp(ctx, tp_level_reached)
        if tp_decision:
            return tp_decision

        # 2. breakeven push
        be_decision = self._evaluate_breakeven(ctx, breakeven_active)
        if be_decision:
            return be_decision

        # 3. invalidation 退出（LLM 的论点失效条件）
        # 若 ctx.invalidation_condition 非空且 4h+1d 双反向 → 论点失效，全平
        if ctx.invalidation_condition and not ctx.trend_4h_aligned and not ctx.trend_1d_aligned:
            ts = int(time.time() * 1e9)
            return ExitDecision(
                position_id=ctx.position_id,
                action=ExitAction.CLOSE.value,
                qty_ratio=1.0,
                reason=f"长线 invalidation 触发：{ctx.invalidation_condition[:60]}（4h+1d 双反向）",
                source=ExitSource.BIAS_REVERSAL.value,
                ts_ns=ts,
            )

        # 4. bias 反向（4h+1d 双反但无 invalidation → 减仓 50%，不全平给恢复机会）
        if not ctx.trend_4h_aligned and not ctx.trend_1d_aligned:
            ts = int(time.time() * 1e9)
            return ExitDecision(
                position_id=ctx.position_id,
                action=ExitAction.REDUCE.value,
                qty_ratio=0.50,
                reason="长线 bias 反向(4h+1d 双反)，减仓50%",
                source=ExitSource.BIAS_REVERSAL.value,
                ts_ns=ts,
            )

        # 5. trailing
        trail_decision = self._evaluate_trailing(ctx, tp_level_reached, trailing_active)
        if trail_decision:
            return trail_decision

        return None


# 策略注册表
TIER_STRATEGIES: dict[str, TierExitStrategy] = {
    "short": ShortTierExit(),
    "mid": MidTierExit(),
    "long": LongTierExit(),
}


def get_tier_strategy(tier: str) -> TierExitStrategy:
    return TIER_STRATEGIES.get(tier, TIER_STRATEGIES["short"])
