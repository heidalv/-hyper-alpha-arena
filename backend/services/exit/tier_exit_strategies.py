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


class ShortTierExit(TierExitStrategy):
    """短线：快进快出，紧 trailing(1.0×ATR)，分批 TP。"""

    STAGED_TPS = [
        StagedTP(trigger_pnl_pct=4.0, reduce_ratio=0.35),
        StagedTP(trigger_pnl_pct=7.0, reduce_ratio=0.35),
    ]
    TRAILING_ATR_MULT = 1.0
    # [2026-07-30 crypto-native] 2% 浮盈推保本太早，加 0.5% offset 太紧，
    # 被 5m 正常波动击穿→breakeven_tp 100% 微利出场。提升阈值和 buffer。
    BREAKEVEN_TRIGGER_PCT = 3.0   # 3% 浮盈才推保本
    BREAKEVEN_OFFSET_PCT = 0.008  # SL→entry+0.8% (≥1×ATR)
    TRAILING_ACTIVATE_LEVEL = 2   # TP2 触发后启动 trailing

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

        # 3. trailing（S2 修复：typo drawback → drawdown，原 bug 导致永不触发）
        trail_decision = self._evaluate_trailing(ctx, tp_level_reached, trailing_active)
        if trail_decision:
            return trail_decision

        return None


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
