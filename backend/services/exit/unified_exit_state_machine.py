"""
统一离场状态机 — 所有平仓/减仓的唯一决策出口。

═══════════════════════════════════════════════════════════════════════
  所有离场动作（close/reduce/tighten_sl）必须经此状态机仲裁。
  禁止任何模块绕开此处直调 paper_engine.close_position。
═══════════════════════════════════════════════════════════════════════

4 层仲裁结构（按优先级从高到低）：
    1. 硬事实层：SL/TP/爆仓/紧急回撤 → 直通执行（不可拦截）
    2. 保护层：min_hold 内的非紧急退出 → 拦截（防过早平仓）
    3. 动态离场层：trailing/分批TP/time_decay/bias反转 → 按 tier 独立计算
    4. AI 决策层：master reduce/close/hold_review → 最严门控（盈利门槛等）

解决的核心问题：
    - 5 条路径各自为政 → 统一 submit() + per-position 锁
    - 微盈利即减仓 → 保护层 min_profit_to_reduce
    - 持仓过短 → 保护层 min_hold + time_decay 自动管理
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from backend.services.exit.exit_types import (
    AI_SOURCES,
    HARD_FACT_SOURCES,
    ExitAction,
    ExitDecision,
    ExitRequest,
    ExitSource,
    ExitUrgency,
    PositionContext,
    make_hold,
)

logger = logging.getLogger(__name__)


# 各 tier 的保护层参数
@dataclass(frozen=True)
class TierProtectionConfig:
    """保护层参数（防过早平仓）。"""
    min_hold_sec: int = 3600          # 最短持仓时间（保护期内禁止非紧急退出）
    min_profit_to_reduce_pct: float = 1.5   # 盈利低于此值时禁止 reduce
    reentry_cooldown_sec: int = 7200  # 减仓后重仓冷却（防减了又开）
    reduce_cooldown_sec: int = 1800   # 两次减仓最小间隔
    max_reduce_count: int = 3         # 单仓最大减仓次数


TIER_PROTECTION: dict[str, TierProtectionConfig] = {
    "short": TierProtectionConfig(
        min_hold_sec=3600,             # 1h
        min_profit_to_reduce_pct=1.5,
        reentry_cooldown_sec=7200,     # 2h
        reduce_cooldown_sec=1800,      # 30min
        max_reduce_count=3,
    ),
    "mid": TierProtectionConfig(
        min_hold_sec=1800,             # 30min
        min_profit_to_reduce_pct=3.0,
        reentry_cooldown_sec=14400,    # 4h
        reduce_cooldown_sec=3600,      # 1h
        max_reduce_count=3,
    ),
    "long": TierProtectionConfig(
        min_hold_sec=7200,             # 2h
        min_profit_to_reduce_pct=5.0,
        reentry_cooldown_sec=28800,    # 8h
        reduce_cooldown_sec=7200,      # 2h
        max_reduce_count=2,
    ),
}


@dataclass
class PositionExitState:
    """状态机内部维护的 per-position 追踪状态。"""
    position_id: int
    reduce_count: int = 0
    last_reduce_ts: float = 0.0
    last_close_ts: float = 0.0
    tighten_sl_count: int = 0
    # ── S2-3 新增（lifecycle 状态追踪）──
    tp_level_reached: int = 0          # 已触发的 TP 档位（0/1/2/3）
    peak_pnl_pct: float = 0.0          # 历史最高浮盈%（持久化用）
    breakeven_active: bool = False     # 是否已触发 breakeven
    trailing_active: bool = False      # 是否已启动 trailing


class UnifiedExitStateMachine:
    """
    单一离场仲裁器（单例）。

    用法：
        from backend.services.exit.unified_exit_state_machine import exit_state_machine
        decision = exit_state_machine.submit(request, context)
        if decision.action == "close":
            paper_engine.close_position(...)
        elif decision.action == "reduce":
            paper_engine.close_position(..., quantity=qty*ratio)
        elif decision.action == "tighten_sl":
            paper_engine.update_position_tp_sl(sl_price=decision.new_sl_price)
    """

    _instance: Optional["UnifiedExitStateMachine"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._position_states: dict[int, PositionExitState] = {}
        self._position_locks: dict[int, threading.Lock] = {}
        self._global_lock = threading.Lock()
        # 仲裁窗口内 pending requests（同 position 短时间内多个请求合并）
        self._pending: dict[int, list[ExitRequest]] = {}
        self._last_arbitrate_ts: dict[int, float] = {}
        logger.info("[ExitStateMachine] 统一离场状态机初始化完成")

    def submit(self, req: ExitRequest, ctx: PositionContext) -> ExitDecision:
        """
        所有触发源调用此方法。返回仲裁后的 ExitDecision。

        内部按 position_id 加锁，保证同一持仓的决策串行化。
        """
        # 获取 per-position 锁
        pos_lock = self._get_position_lock(req.position_id)
        with pos_lock:
            return self._arbitrate(req, ctx)

    def _get_position_lock(self, position_id: int) -> threading.Lock:
        with self._global_lock:
            if position_id not in self._position_locks:
                self._position_locks[position_id] = threading.Lock()
            return self._position_locks[position_id]

    def _get_state(self, position_id: int) -> PositionExitState:
        if position_id not in self._position_states:
            self._position_states[position_id] = PositionExitState(position_id=position_id)
        return self._position_states[position_id]

    def _arbitrate(self, req: ExitRequest, ctx: PositionContext) -> ExitDecision:
        """4 层仲裁。"""
        now = time.time()
        ts_ns = req.ts_ns or int(now * 1e9)

        # ── Layer 1: 硬事实层（直通） ──
        if req.source in HARD_FACT_SOURCES:
            logger.info(f"[ExitSM] 硬事实直通: pos={req.position_id} source={req.source} action={req.proposed_action}")
            return ExitDecision(
                position_id=req.position_id,
                action=req.proposed_action,
                qty_ratio=req.proposed_qty_ratio,
                reason=f"hard_fact:{req.source} {req.reason_detail}",
                source=req.source,
                ts_ns=ts_ns,
            )

        # ── Layer 2: 保护层 ──
        protection = TIER_PROTECTION.get(req.tier, TIER_PROTECTION["short"])
        state = self._get_state(req.position_id)

        # 2a: min_hold 保护期内拦截非紧急退出
        if ctx.hold_seconds < protection.min_hold_sec and req.urgency != ExitUrgency.CRITICAL.value:
            logger.info(f"[ExitSM] 保护期拦截: pos={req.position_id} hold={ctx.hold_seconds}s < min={protection.min_hold_sec}s")
            return make_hold(req.position_id, f"保护期内(hold={ctx.hold_seconds}s<{protection.min_hold_sec}s)")

        # 2b: 微盈利禁止 reduce（核心修复：防"微盈利即减仓"）
        if req.proposed_action == ExitAction.REDUCE.value:
            if 0 <= ctx.unrealized_pnl_pct < protection.min_profit_to_reduce_pct:
                logger.info(
                    f"[ExitSM] 微盈利禁止减仓: pos={req.position_id} "
                    f"pnl={ctx.unrealized_pnl_pct:.2f}% < min={protection.min_profit_to_reduce_pct}%"
                )
                # 降级为 tighten_sl（不减仓，只收紧止损）
                return ExitDecision(
                    position_id=req.position_id,
                    action=ExitAction.TIGHTEN_SL.value,
                    qty_ratio=0.0,
                    reason=f"微盈利{ctx.unrealized_pnl_pct:.2f}%<{protection.min_profit_to_reduce_pct}%，减仓降级为收紧止损",
                    source=req.source,
                    ts_ns=ts_ns,
                )

            # 2c: 减仓冷却
            if now - state.last_reduce_ts < protection.reduce_cooldown_sec:
                logger.info(f"[ExitSM] 减仓冷却: pos={req.position_id} 距上次减仓{int(now-state.last_reduce_ts)}s")
                return make_hold(req.position_id, "减仓冷却中")

            # 2d: 最大减仓次数
            if state.reduce_count >= protection.max_reduce_count:
                logger.info(f"[ExitSM] 减仓次数上限: pos={req.position_id} count={state.reduce_count}")
                return make_hold(req.position_id, f"减仓次数已达上限{protection.max_reduce_count}")

        # ── Layer 3: 动态离场层（trailing/TP/time_decay/bias） ──
        # S2-2: 委托给 TierExitStrategy（在 tier_exit_strategies.py 实现）
        # 传入 PositionExitState 以追踪 tp_level_reached/breakeven_active/trailing_active
        dynamic_decision = self._evaluate_dynamic_exit(req, ctx, protection, state)
        if dynamic_decision and dynamic_decision.action != ExitAction.HOLD.value:
            self._update_state_on_action(state, dynamic_decision, now, ctx)
            return dynamic_decision

        # ── Layer 4: AI 决策层（最严门控） ──
        if req.source in AI_SOURCES:
            ai_decision = self._evaluate_ai_exit(req, ctx, protection, state)
            self._update_state_on_action(state, ai_decision, now, ctx)
            return ai_decision

        # 默认 hold
        return make_hold(req.position_id, "无触发条件")

    def _evaluate_dynamic_exit(
        self, req: ExitRequest, ctx: PositionContext, protection: TierProtectionConfig,
        state: PositionExitState,
    ) -> Optional[ExitDecision]:
        """动态离场层：委托给 TierExitStrategy（S2 激活死代码）+ 保留 profit_drawdown 兜底。

        S2 修复（对应 04 综合方案 §3.4 / 审计 R6）：
        原实现是内联的简化版（无 breakeven、无真正的分批 TP 状态追踪、无 LLM tp_stages 接入）。
        现在委托给 tier_exit_strategies.TIER_STRATEGIES[tier].evaluate，并传入 lifecycle 状态。
        """
        ts_ns = req.ts_ns or int(time.time() * 1e9)

        # ── S2-2：委托给 TierExitStrategy（激活死代码） ──
        try:
            from backend.services.exit.tier_exit_strategies import get_tier_strategy
            strategy = get_tier_strategy(req.tier)
            # 传入 lifecycle 状态：tp_level_reached / breakeven_active / trailing_active
            decision = strategy.evaluate(
                ctx,
                tp_level_reached=state.tp_level_reached,
                breakeven_active=state.breakeven_active,
                trailing_active=state.trailing_active,
            )
            if decision and decision.action != ExitAction.HOLD.value:
                return decision
        except Exception as _strat_err:
            logger.debug("[ExitSM] tier strategy 调用失败 pos=%s: %s", req.position_id, _strat_err)

        # ── 保留 profit_drawdown 兜底（TierExitStrategy 没覆盖时的安全网）──
        if ctx.peak_pnl_pct > 2.0 and ctx.unrealized_pnl_pct > 0:
            drawdown_pct = (ctx.peak_pnl_pct - ctx.unrealized_pnl_pct) / max(ctx.peak_pnl_pct, 0.01) * 100
            dd_threshold = {"short": 55, "mid": 50, "long": 40}.get(req.tier, 50)
            if drawdown_pct >= dd_threshold:
                return ExitDecision(
                    position_id=req.position_id,
                    action=ExitAction.REDUCE.value,
                    qty_ratio=0.5,
                    reason=f"盈利回撤{drawdown_pct:.0f}%≥{dd_threshold}%",
                    source=ExitSource.PROFIT_DRAWDOWN.value,
                    ts_ns=ts_ns,
                )

        # 中长线外部守卫（bias 反向 / 无进展）已在循环侧判定，此处尊重其提案
        if req.source in (
            ExitSource.BIAS_REVERSAL.value,
            ExitSource.NO_PROGRESS.value,
        ) and req.proposed_action in (
            ExitAction.CLOSE.value,
            ExitAction.REDUCE.value,
        ):
            return ExitDecision(
                position_id=req.position_id,
                action=req.proposed_action,
                qty_ratio=req.proposed_qty_ratio,
                reason=f"midlong:{req.source} {req.reason_detail}",
                source=req.source,
                ts_ns=ts_ns,
            )

        return None

    def _evaluate_ai_exit(
        self, req: ExitRequest, ctx: PositionContext,
        protection: TierProtectionConfig, state: PositionExitState,
    ) -> ExitDecision:
        """AI 决策层（最严门控）。"""
        ts_ns = req.ts_ns or int(time.time() * 1e9)

        # master_close 的盈利保护：小盈利禁止全平
        if req.source == ExitSource.MASTER_CLOSE.value:
            if 0 < ctx.unrealized_pnl_pct < 3.0:
                logger.info(f"[ExitSM] 小盈利禁止AI全平: pos={req.position_id} pnl={ctx.unrealized_pnl_pct:.2f}%")
                return ExitDecision(
                    position_id=req.position_id,
                    action=ExitAction.TIGHTEN_SL.value,
                    reason=f"小盈利{ctx.unrealized_pnl_pct:.2f}%，全平降级为收紧止损",
                    source=req.source,
                    ts_ns=ts_ns,
                )

        # master_reduce 已在 Layer 2 处理过盈利门槛，这里放行
        return ExitDecision(
            position_id=req.position_id,
            action=req.proposed_action,
            qty_ratio=req.proposed_qty_ratio,
            reason=f"ai:{req.source} {req.reason_detail}",
            source=req.source,
            ts_ns=ts_ns,
        )

    def _update_state_on_action(self, state: PositionExitState, decision: ExitDecision, now: float, ctx: PositionContext = None):
        """决策后更新内部状态（S2 扩展：追踪 tp_level/breakeven/trailing/peak）。"""
        if decision.action == ExitAction.REDUCE.value:
            state.reduce_count += 1
            state.last_reduce_ts = now
            # S2: 分批 TP 触发 → tp_level_reached +1
            if decision.source == ExitSource.STAGED_TP.value:
                state.tp_level_reached += 1
        elif decision.action == ExitAction.CLOSE.value:
            state.last_close_ts = now
        elif decision.action == ExitAction.TIGHTEN_SL.value:
            state.tighten_sl_count += 1
            # S2: breakeven 触发 → 标记
            if decision.source == ExitSource.BREAKEVEN.value:
                state.breakeven_active = True
        # S2: 更新 peak_pnl_pct（每 tick 更新）
        if ctx and ctx.peak_pnl_pct > state.peak_pnl_pct:
            state.peak_pnl_pct = ctx.peak_pnl_pct
        # S2: TP2 触发后标记 trailing_active
        if state.tp_level_reached >= 2:
            state.trailing_active = True

    def reset_position(self, position_id: int):
        """持仓关闭后清理状态。"""
        self._position_states.pop(position_id, None)
        with self._global_lock:
            self._position_locks.pop(position_id, None)

    def stats(self) -> dict:
        return {
            "tracked_positions": len(self._position_states),
            "tier_configs": {t: {"min_hold": c.min_hold_sec, "min_profit_reduce": c.min_profit_to_reduce_pct}
                             for t, c in TIER_PROTECTION.items()},
        }


# 全局单例
exit_state_machine = UnifiedExitStateMachine()
