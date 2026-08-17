"""
TradingDecisionInterface — 交易决策可扩展抽象层

full_auto_trading_service.py 的决策接缝点抽象。
DRL/Kelly/Evolution 通过此接口注入，不修改原有决策流程。

设计原则:
1. 所有方法都有默认实现（透传到原有逻辑），确保零风险接入
2. 通过 Feature Flag 控制每个扩展点的启用
3. SystemCoordinator 作为唯一外部依赖，降低耦合度
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
#  数据结构定义
# ══════════════════════════════════════════════════

@dataclass
class DecisionContext:
    """决策上下文 — 传递给所有决策方法的统一输入"""
    symbol: str = ""
    tier: str = "mid"                    # short / mid / long
    regime: str = "ranging"              # trending / ranging / volatile / crisis
    confidence: int = 50                 # 0-100
    volatility: float = 0.02            # 当前波动率
    open_position_count: int = 0         # 已有持仓数
    tier_budget_pct: float = 0.0         # tier预算占比
    equity: float = 0.0                  # 当前权益
    market_summary: Dict[str, Any] = field(default_factory=dict)
    analyst_reports: Dict[str, Any] = field(default_factory=dict)
    strategy_genome: Dict[str, Any] = field(default_factory=dict)
    strategy_id: str = ""                # 所属 AIStrategy.strategy_id（供策略护栏查询）
    trading_mode: str = "paper"          # paper / live — 锁仓强度分模式读取


@dataclass
class PositionAdvice:
    """仓位建议 — DRL/Kelly/Evolution 的综合输出"""
    position_pct: float = 0.20           # 建议仓位比例
    direction: str = "hold"              # long / short / hold
    confidence_weight: float = 1.0       # 置信度加权因子 (0~2)
    kelly_upper_bound: Optional[float] = None  # Kelly仓位上限
    source: str = "rule"                 # rule / drl / kelly / evolution / arbitrated
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskVerdict:
    """风控判定结果"""
    passed: bool = True
    risk_level: str = "moderate"         # conservative / moderate / aggressive
    reason_code: str = ""
    reason_text: str = ""
    portfolio_risk: float = 0.0          # 组合风险值
    forced_adjustments: List[str] = field(default_factory=list)


@dataclass
class ArbitratedDecision:
    """仲裁后的最终决策"""
    action: str = "hold"                 # buy / sell / close / reduce / hold
    side: str = ""                       # long / short
    position_pct: float = 0.20
    leverage: float = 10.0
    stop_loss_pct: float = 0.04
    take_profit_pct: float = 0.06
    min_confidence: int = 60
    # 来源追踪
    position_source: str = "rule"        # rule / kelly / drl / arbitrated
    direction_source: str = "rule"       # rule / drl / arbitrated
    params_source: str = "rule"          # rule / evolution / arbitrated


# ══════════════════════════════════════════════════
#  TradingDecisionInterface
# ══════════════════════════════════════════════════

class TradingDecisionInterface:
    """
    交易决策接口 — full_auto_trading_service 的可扩展抽象层

    接缝点:
    1. decide_position_pct  — 仓位比例决策（Kelly注入点）
    2. decide_direction     — 方向决策（DRL注入点）
    3. adapt_params         — 参数自适应（Evolution注入点）
    4. check_portfolio_risk — 组合风控（PortfolioRiskAggregator注入点）
    5. arbitrate            — 多系统冲突仲裁

    使用方式:
    - 默认实例: 透传到原有逻辑（零影响）
    - 整合实例: 通过 SystemCoordinator 注入DRL/Kelly/Evolution
    """

    def __init__(self, coordinator=None):
        """
        Args:
            coordinator: SystemCoordinator 实例（可选）
                         None 时所有方法退化为透传
        """
        self._coordinator = coordinator

    # ══════════════════════════════════════════════════
    #  接缝点1: 仓位比例决策
    # ══════════════════════════════════════════════════

    def decide_position_pct(
        self,
        base_pct: float,
        context: DecisionContext,
    ) -> PositionAdvice:
        """
        仓位比例决策 — Kelly注入点

        Args:
            base_pct: 原有规则计算的仓位比例（_ai_dynamic_position_pct的输出）
            context: 决策上下文

        Returns:
            PositionAdvice — 包含仓位比例、Kelly上限、来源标记

        逻辑:
        - ENABLE_KELLY_POSITION=False: 透传base_pct
        - ENABLE_KELLY_POSITION=True + KELLY_AS_UPPER_BOUND=True:
          Kelly值作为上限，min(base_pct, kelly)
        - ENABLE_KELLY_POSITION=True + KELLY_AS_UPPER_BOUND=False:
          Kelly值替代base_pct
        """
        from backend.config.settings import (
            ENABLE_KELLY_POSITION, KELLY_AS_UPPER_BOUND,
        )

        if not ENABLE_KELLY_POSITION or self._coordinator is None:
            return PositionAdvice(
                position_pct=base_pct,
                source="rule",
            )

        try:
            kelly_limit = self._coordinator.get_kelly_position_limit(
                context.symbol, context.equity
            )

            if KELLY_AS_UPPER_BOUND:
                # Kelly作为上限：不超过Kelly建议
                final_pct = min(base_pct, kelly_limit)
                source = "kelly" if kelly_limit < base_pct else "rule"
            else:
                # Kelly替代基础仓位
                final_pct = kelly_limit
                source = "kelly"

            # S5: RL 仓位管理器 — 作为 Kelly 的补充（当 ENABLE_RL_POSITION_SIZER=true 时）
            rl_adjustment = 1.0
            try:
                from backend.config.settings import ENABLE_RL_POSITION_SIZER
                if ENABLE_RL_POSITION_SIZER:
                    rl_sizer = self._coordinator._get_rl_position_sizer()
                    if rl_sizer is not None:
                        # [P1-6 集成修复] 原代码调用不存在的 _discretize_state +
                        # select_action 单参数传 StateTuple → 每次抛 AttributeError/TypeError，
                        # 且 RLActionResult 被当标量除 → RL 仓位建议从未生效（静默 no-op）。
                        # 现按真实接口调用：select_action(regime, vol_ratio, dd, losses, streak, greedy)。
                        rl_result = rl_sizer.select_action(
                            regime=str(getattr(context, "market_regime", "ranging") or "ranging"),
                            volatility_ratio=float(getattr(context, "volatility", 1.0) or 1.0),
                            drawdown_pct=0.0,
                            consecutive_losses=0,
                            win_streak=0,
                            use_greedy=True,  # 决策路径贪心；在线微调由 trainer 驱动
                        )
                        rl_pct = float(getattr(rl_result, "position_pct", 0.0) or 0.0)
                        if rl_pct > 0:
                            rl_adjustment = rl_pct / max(base_pct, 0.01)
                            rl_adjustment = max(0.3, min(1.5, rl_adjustment))
                            logger.info(
                                f"[TDI] RL仓位建议: pct={rl_pct:.3f} "
                                f"adjustment={rl_adjustment:.2f}x",
                            )
            except Exception as _rl_err:
                # [P1-6] 异常必须可见（升级 warning），否则集成再次静默断裂无人察觉
                logger.warning(f"[TDI] RL仓位调整失败（已跳过）: {_rl_err}")

            final_pct = final_pct * rl_adjustment
            return PositionAdvice(
                position_pct=final_pct,
                kelly_upper_bound=kelly_limit,
                source=source,
                metadata={
                    "base_pct": base_pct,
                    "kelly_limit": kelly_limit,
                    "rl_adjustment": rl_adjustment,
                },
            )
        except Exception as e:
            logger.warning(f"[TDI] Kelly仓位计算失败，降级为规则: {e}")
            return PositionAdvice(position_pct=base_pct, source="rule_fallback")

    # ══════════════════════════════════════════════════
    #  接缝点2: 方向决策
    # ══════════════════════════════════════════════════

    def decide_direction(
        self,
        base_direction: str,
        context: DecisionContext,
    ) -> PositionAdvice:
        """
        方向决策 — DRL注入点

        Args:
            base_direction: 原有决策方向（hold/long/short）
            context: 决策上下文

        Returns:
            PositionAdvice — 包含方向、置信度加权、来源标记

        逻辑:
        - ENABLE_DRL_INTEGRATION=False: 透传base_direction
        - DRL_SHADOW_MODE=True: 记录DRL建议但不影响决策
        - DRL_SHADOW_MODE=False: DRL建议作为置信度加权因子
        """
        from backend.config.settings import ENABLE_DRL_INTEGRATION, DRL_SHADOW_MODE

        # 只有在 integration 和 shadow 都关闭时才完全绕开；否则走到 get_drl_advice
        # 里采样影子数据（即使返回结果不影响决策）
        if (not ENABLE_DRL_INTEGRATION and not DRL_SHADOW_MODE) or self._coordinator is None:
            return PositionAdvice(
                direction=base_direction,
                confidence_weight=1.0,
                source="rule",
            )

        try:
            drl_advice = self._coordinator.get_drl_advice(
                context.symbol, context
            )

            if (
                drl_advice.source.startswith("shadow")
                or drl_advice.source == "disabled"
            ):
                # 影子模式或禁用：不影响决策，但依然把采样信息塞到 metadata 便于观察
                return PositionAdvice(
                    direction=base_direction,
                    confidence_weight=1.0,
                    source="rule",
                    metadata={
                        "drl_shadow_direction": drl_advice.direction,
                        "drl_shadow_size": drl_advice.size,
                        "drl_shadow_source": drl_advice.source,
                    },
                )

            # DRL建议生效：作为置信度加权因子
            # DRL与原决策方向一致时增强置信度，不一致时削弱
            if drl_advice.direction == base_direction and base_direction != "hold":
                weight = 1.0 + drl_advice.confidence * 0.3  # 1.0~1.3
            elif drl_advice.direction != "hold" and base_direction == "hold":
                # DRL有方向但原决策hold：轻微削弱（不轻易推翻hold）
                weight = 0.9
            else:
                # 方向冲突：削弱置信度
                weight = max(0.5, 1.0 - drl_advice.confidence * 0.3)

            return PositionAdvice(
                direction=base_direction,  # 方向不变，仅调整置信度
                confidence_weight=weight,
                source="drl_weighted",
                metadata={
                    "drl_direction": drl_advice.direction,
                    "drl_confidence": drl_advice.confidence,
                    "weight": weight,
                },
            )
        except Exception as e:
            logger.warning(f"[TDI] DRL方向建议失败，降级为规则: {e}")
            return PositionAdvice(
                direction=base_direction,
                confidence_weight=1.0,
                source="rule_fallback",
            )

    # ══════════════════════════════════════════════════
    #  接缝点3: 参数自适应
    # ══════════════════════════════════════════════════

    def adapt_params(
        self,
        base_params: Dict[str, Any],
        context: DecisionContext,
    ) -> Dict[str, Any]:
        """参数自适应（已废弃，2026-06-11 学习系统升级）。

        历史：本方法是 ENABLE_EVOLUTION_FEEDBACK 的唯一消费端，但主循环
        从未调用过它（死链路），且 get_evolved_params 只是透传 genome。
        进化反哺现统一走 data/v5_runtime_gates.json 通道
        （evolution_scheduler._sync_champion_to_v5_gates → UnifiedDecisionGate）。
        保留方法签名以兼容潜在外部调用，恒透传 base_params。
        """
        return base_params

    # ══════════════════════════════════════════════════
    #  接缝点4: 组合风控
    # ══════════════════════════════════════════════════

    def check_portfolio_risk(
        self,
        context: DecisionContext,
    ) -> RiskVerdict:
        """
        组合风控检查 — PortfolioRiskAggregator注入点

        Args:
            context: 决策上下文

        Returns:
            RiskVerdict — 包含是否通过、风险级别、组合风险值

        逻辑:
        - ENABLE_PORTFOLIO_RISK=False: 返回默认通过
        - ENABLE_PORTFOLIO_RISK=True: 调用PortfolioRiskAggregator
        """
        from backend.config.settings import ENABLE_PORTFOLIO_RISK

        # —— 策略护栏（P2-9）：低胜率自动软冷冻，先于组合风控检查 ——
        guard_verdict = self._strategy_guard_check(context)
        if guard_verdict is not None and not guard_verdict.passed:
            return guard_verdict

        if not ENABLE_PORTFOLIO_RISK or self._coordinator is None:
            return RiskVerdict(passed=True, risk_level="moderate")

        try:
            return self._coordinator.check_portfolio_risk(context)
        except Exception as e:
            logger.warning(f"[TDI] 组合风控检查失败，降级为通过: {e}")
            return RiskVerdict(passed=True, risk_level="moderate")

    def _strategy_guard_check(self, context) -> Optional["RiskVerdict"]:
        """策略护栏：评估窗口内样本足够且胜率过低的策略，自动进入冷却期。

        返回 None 表示不干预；否则返回 RiskVerdict(passed=False)。
        """
        try:
            from backend.config.settings import (
                STRATEGY_GUARD_WINDOW_HOURS,
                STRATEGY_GUARD_MIN_SAMPLES,
                STRATEGY_GUARD_MIN_WINRATE,
                STRATEGY_GUARD_COOLDOWN_HOURS,
            )
            from backend.services.lock_strength_service import get_lock_strength_service
            _mode = (getattr(context, "trading_mode", None) or "paper").strip().lower()
            if not get_lock_strength_service().get_profile(_mode).strategy_guard:
                return None
            strategy_id = getattr(context, "strategy_id", None)
            if not strategy_id:
                return None

            from backend.database.connection import SessionLocal
            from backend.database.models import StrategyTrade
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            from sqlalchemy import func as _func

            db = SessionLocal()
            try:
                since = _dt.now(_tz.utc) - _td(hours=STRATEGY_GUARD_WINDOW_HOURS)
                row = (
                    db.query(
                        _func.count(StrategyTrade.id).label("n"),
                        _func.sum(
                            _func.case(
                                (StrategyTrade.pnl > 0, 1), else_=0
                            )
                        ).label("wins"),
                        _func.max(StrategyTrade.closed_at).label("last_at"),
                    )
                    .filter(
                        StrategyTrade.strategy_id == strategy_id,
                        StrategyTrade.status == "closed",
                        StrategyTrade.closed_at >= since.replace(tzinfo=None),
                    )
                    .one()
                )
                n = int(row.n or 0)
                wins = int(row.wins or 0)
                if n < STRATEGY_GUARD_MIN_SAMPLES:
                    return None
                win_rate = wins / n if n > 0 else 0.0
                if win_rate >= STRATEGY_GUARD_MIN_WINRATE:
                    return None

                last_at = row.last_at
                if last_at is not None:
                    if getattr(last_at, "tzinfo", None) is None:
                        last_at = last_at.replace(tzinfo=_tz.utc)
                    cooldown_until = last_at + _td(hours=STRATEGY_GUARD_COOLDOWN_HOURS)
                    if _dt.now(_tz.utc) >= cooldown_until:
                        return None  # 冷却期已过，允许恢复

                logger.warning(
                    f"[TDI-Guard] 策略 {strategy_id} 进入冷却: "
                    f"n={n} wins={wins} wr={win_rate:.2%} "
                    f"< min={STRATEGY_GUARD_MIN_WINRATE:.0%} "
                    f"window={STRATEGY_GUARD_WINDOW_HOURS}h"
                )
                return RiskVerdict(
                    passed=False,
                    risk_level="high",
                    reason_code="strategy_guard_low_winrate",
                    reason_text=(
                        f"strategy_guard: 最近{STRATEGY_GUARD_WINDOW_HOURS}h "
                        f"{n}笔胜率{win_rate:.0%}低于{STRATEGY_GUARD_MIN_WINRATE:.0%}，"
                        f"冷却{STRATEGY_GUARD_COOLDOWN_HOURS}h"
                    ),
                )
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[TDI-Guard] 策略护栏异常(放行): {e}")
            return None

    # ══════════════════════════════════════════════════
    #  接缝点5: 多系统冲突仲裁
    # ══════════════════════════════════════════════════

    def arbitrate(
        self,
        base_decision: ArbitratedDecision,
        position_advice: PositionAdvice,
        direction_advice: PositionAdvice,
        risk_verdict: RiskVerdict,
    ) -> ArbitratedDecision:
        """
        仲裁多系统冲突

        优先级: 风控 > Kelly > DRL > 进化

        Args:
            base_decision: 原有决策（ArbitratedDecision 或 dict）
            position_advice: 仓位建议（来自decide_position_pct）
            direction_advice: 方向建议（来自decide_direction）
            risk_verdict: 风控判定（来自check_portfolio_risk）

        Returns:
            ArbitratedDecision — 仲裁后的最终决策
        """
        # 兼容dict输入
        if isinstance(base_decision, dict):
            base = ArbitratedDecision(
                action=base_decision.get("action", "hold"),
                side=base_decision.get("side", ""),
                position_pct=base_decision.get("position_pct", 0.20),
                leverage=base_decision.get("leverage", 10.0),
                stop_loss_pct=base_decision.get("stop_loss_pct", 0.04),
                take_profit_pct=base_decision.get("take_profit_pct", 0.06),
                min_confidence=base_decision.get("min_confidence", 50),
            )
        else:
            base = base_decision

        result = ArbitratedDecision(
            action=base.action,
            side=base.side,
            position_pct=position_advice.position_pct,
            leverage=base.leverage,
            stop_loss_pct=base.stop_loss_pct,
            take_profit_pct=base.take_profit_pct,
            min_confidence=base.min_confidence,
            position_source=position_advice.source,
            direction_source=direction_advice.source,
            params_source=base.params_source if hasattr(base, 'params_source') else "rule",
        )

        # 1. 风控否决：最高优先级
        if not risk_verdict.passed:
            result.action = "hold"
            result.position_pct = 0.0
            return result

        # 1.5 组合风控夹紧（P0-5）：SystemCoordinator.check_portfolio_risk 返回的
        # `symbol_adjusted_fraction` 代表当前币种在组合层面的建议上限（已含相关性惩罚）。
        # 当 PORTFOLIO_RISK_HARD_BLOCK=false（默认）时只夹紧不否决。
        try:
            sym_adj = getattr(risk_verdict, "symbol_adjusted_fraction", None)
            if sym_adj is not None and sym_adj > 0.0:
                result.position_pct = min(result.position_pct, float(sym_adj))
        except Exception:
            pass

        # 2. Kelly上限约束
        if position_advice.kelly_upper_bound is not None:
            result.position_pct = min(
                result.position_pct,
                position_advice.kelly_upper_bound,
            )

        # 3. DRL置信度加权
        if direction_advice.confidence_weight != 1.0:
            # 调整min_confidence（加权后需要更高/更低门槛）
            adjusted_conf = int(base_decision.min_confidence / direction_advice.confidence_weight)
            result.min_confidence = max(30, min(95, adjusted_conf))

        return result


# ══════════════════════════════════════════════════
#  全局单例（默认透传，coordinator注入后激活）
# ══════════════════════════════════════════════════

trading_decision_interface = TradingDecisionInterface(coordinator=None)


def inject_coordinator(coordinator):
    """注入SystemCoordinator，激活DRL/Kelly/Evolution整合"""
    global trading_decision_interface
    trading_decision_interface = TradingDecisionInterface(coordinator=coordinator)
    logger.info("[TDI] SystemCoordinator已注入，AI学习系统整合激活")
