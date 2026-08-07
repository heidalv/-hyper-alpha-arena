"""
AutoOptimizer facade — 桥接 strategy_health_service 到灰度优化系统

修复 strategy_health_service.py:298 的损坏 import:
    from backend.services.auto_optimizer import AutoOptimizer

该文件原本不存在（只有 learning_feedback_layer/auto_optimizer.py），
导致优化从未实际触发。

现在通过 QAA 进化桥接系统实现灰度发布优化。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AutoOptimizer:
    """Facade for strategy_health_service — 触发参数重优化

    使用方式（与 strategy_health_service.py:298-300 保持兼容）:
        optimizer = AutoOptimizer()
        optimizer.queue_optimization(strategy_id)
    """

    def __init__(self):
        pass

    def queue_optimization(self, strategy_id: str):
        """触发策略参数重优化 — 通过 QAA 灰度发布

        流程：
        1. 读取策略当前 genome
        2. 运行参数优化（从 learning_feedback_layer.auto_optimizer）
        3. 创建灰度发布计划（canary/control 划分）
        4. 观察期结束后自动确认/回滚
        """
        try:
            from backend.services.qaa_evolution_bridge import qaa_bridge
            if not qaa_bridge._enabled:
                logger.debug(f"[AutoOptimizer] QAA 未启用，跳过 {strategy_id}")
                return

            from backend.database.connection import SessionLocal
            from backend.database.models import AIStrategy
            import copy

            db = SessionLocal()
            try:
                strat = db.query(AIStrategy).filter(
                    AIStrategy.strategy_id == strategy_id
                ).first()
                if not strat:
                    logger.warning(f"[AutoOptimizer] 策略 {strategy_id} 不存在")
                    return

                old_genome = copy.deepcopy(strat.genome or {})
                new_genome = self._optimize_genome(old_genome)

                if new_genome == old_genome:
                    logger.info(f"[AutoOptimizer] {strategy_id} 无需优化")
                    return

                # 获取策略关联的所有 symbol
                all_symbols = self._get_strategy_symbols(strat, db)

                if len(all_symbols) >= 2:
                    # 有足够 symbol → 灰度发布
                    plan = qaa_bridge.create_grayscale_plan(
                        strategy_id=strategy_id,
                        old_genome=old_genome,
                        new_genome=new_genome,
                        all_symbols=all_symbols,
                    )
                    if plan:
                        # 在 genome 中持久化灰度标记（重启恢复用）
                        strat.genome = dict(new_genome)
                        strat.genome["__grayscale__"] = {
                            "plan_id": plan.plan_id,
                            "old_genome": old_genome,
                            "new_genome": new_genome,
                            "canary_symbols": plan.canary_symbols,
                            "control_symbols": plan.control_symbols,
                            "started_at": plan.observation_started_at,
                            "observation_seconds": plan.observation_seconds,
                        }
                        db.commit()
                        logger.info(
                            f"[AutoOptimizer] {strategy_id} 灰度计划已创建 "
                            f"(canary={plan.canary_symbols})"
                        )
                    else:
                        # symbol 不足，直接应用
                        strat.genome = new_genome
                        db.commit()
                        logger.info(f"[AutoOptimizer] {strategy_id} 参数已直接应用")
                else:
                    # 只有 1 个或无 symbol → 直接应用
                    strat.genome = new_genome
                    db.commit()
                    logger.info(f"[AutoOptimizer] {strategy_id} 参数已直接应用（无灰度）")

            finally:
                db.close()

        except Exception as e:
            logger.warning(f"[AutoOptimizer] queue_optimization {strategy_id} 失败: {e}")

    def _optimize_genome(self, genome: Dict[str, Any]) -> Dict[str, Any]:
        """基于保守策略调整 genome 参数

        调整策略（针对性能退化的策略）：
        1. 优先尝试 learning_feedback_layer 的贝叶斯优化（有历史评估函数时）
        2. 回退到保守参数调整（杠杆/仓位/止损/止盈 各缩减5-10%）
        """
        import copy
        new = copy.deepcopy(genome)

        # ── 尝试 learning_feedback_layer 贝叶斯优化 ──
        try:
            from backend.services.learning_feedback_layer.auto_optimizer import (
                AutoOptimizer as LFLAutoOptimizer,
                ParameterSpace,
                ParameterType,
                OptimizationMethod,
            )

            lfl = LFLAutoOptimizer()

            # 从当前 genome 的风控参数构建搜索空间
            param_spaces = []
            _range_defs = {
                "default_leverage": (1, 20, float(new.get("default_leverage", 10))),
                "max_position_size": (0.01, 0.3, float(new.get("max_position_size", 0.2))),
                "stop_loss_pct": (0.005, 0.1, float(new.get("stop_loss_pct", 0.05))),
                "take_profit_pct": (0.01, 0.3, float(new.get("take_profit_pct", 0.10))),
            }
            for pname, (lo, hi, default) in _range_defs.items():
                if pname in new:
                    param_spaces.append(ParameterSpace(
                        name=pname,
                        param_type=ParameterType.CONTINUOUS,
                        min_value=lo,
                        max_value=hi,
                        default=default,
                    ))

            if param_spaces:
                # 评估函数：基于保守启发式打分（无回测时用）
                def _heuristic_score(params: Dict[str, Any]) -> float:
                    score = 0.0
                    # 风险调整后的收益期望
                    sl = params.get("stop_loss_pct", 0.05)
                    tp = params.get("take_profit_pct", 0.10)
                    rr_ratio = tp / max(sl, 0.001)
                    score += min(rr_ratio, 5.0) / 5.0 * 40  # 盈亏比权重40%

                    lev = params.get("default_leverage", 10)
                    score += max(0, 20 - abs(lev - 8)) / 20 * 30  # 杠杆适中权重30%

                    pos = params.get("max_position_size", 0.2)
                    score += max(0, 0.15 - abs(pos - 0.1)) / 0.15 * 30  # 仓位适中权重30%
                    return score

                result = lfl.optimize(
                    evaluate_fn=_heuristic_score,
                    parameter_spaces=param_spaces,
                    method=OptimizationMethod.BAYESIAN,
                    maximize=True,
                )

                if result and result.best_params:
                    for k, v in result.best_params.items():
                        if k in new:
                            new[k] = type(new[k])(v) if isinstance(v, (int, float)) else v
                    logger.info(
                        f"[AutoOptimizer] 贝叶斯优化完成: "
                        f"score={result.best_score:.3f}"
                    )
                    return new

        except Exception as e:
            logger.debug(f"[AutoOptimizer] learning_feedback_layer 优化失败，回退保守模式: {e}")

        # ── 回退：保守参数调整 ──
        # 杠杆：降低 10%（保守）
        if "default_leverage" in new:
            new["default_leverage"] = max(1, int(new["default_leverage"] * 0.9))
        if "max_leverage" in new:
            new["max_leverage"] = max(2, int(new["max_leverage"] * 0.9))

        # 仓位：降低 10%
        if "max_position_size" in new:
            new["max_position_size"] = max(0.01, round(new["max_position_size"] * 0.9, 4))

        # 止损：收紧 5%（减少单笔亏损）
        if "stop_loss_pct" in new:
            new["stop_loss_pct"] = max(0.005, round(new["stop_loss_pct"] * 0.95, 4))

        # 止盈：降低 5%（更快锁利）
        if "take_profit_pct" in new:
            new["take_profit_pct"] = max(0.01, round(new["take_profit_pct"] * 0.95, 4))

        return new

    def _get_strategy_symbols(self, strategy, db) -> List[str]:
        """获取策略关联的所有交易 symbol"""
        symbols = []
        try:
            # 从 strategy 的 primary_symbol 和 session 获取
            primary = getattr(strategy, "primary_symbol", None)
            if primary:
                symbols.append(primary)

            # 尝试从 session 获取完整 symbol 列表
            from backend.database.models import TradingSession
            session_id = getattr(strategy, "session_id", None)
            if session_id:
                session = db.query(TradingSession).filter(
                    TradingSession.session_id == session_id
                ).first()
                if session and session.symbols:
                    symbols.extend(session.symbols)
        except Exception:
            pass

        # 去重
        seen = set()
        result = []
        for s in symbols:
            su = s.upper()
            if su not in seen:
                seen.add(su)
                result.append(s)
        return result
