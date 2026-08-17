"""策略自学习服务 — 已合并入统一学习体系（unified_learning_service）

Phase 2 说明：
- 此服务保留核心学习逻辑（run_periodic_review：提示词进化、参数适应、记忆更新）。
- 调度入口统一由 UnifiedLearningService 触发，不再对外单独使用。
- 如需直接触发学习，请通过 unified_learning_service 或 evolution_scheduler。
"""

import logging
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict

from sqlalchemy.orm import Session
from sqlalchemy import func

# 注意：本文件查询的 AIStrategy / StrategyTrade / StrategyMemory /
# PromptTrainingRecord / PromptTemplate 均为主库 Base 模型，
# 必须用 SessionLocal；误用 AnalyticsSessionLocal 在 PG 三库部署下
# 会因 analytics 库无这些表而 UndefinedTable，导致每周复盘整体失效
from backend.database.connection import SessionLocal
from backend.database.models import (
    AIStrategy, StrategyMemory, StrategyTrade,
    AIDecisionLog, PromptTrainingRecord, StrategyTemplate,
    AccountPromptBinding,
)

logger = logging.getLogger(__name__)


def _exclude_legacy_dirty(query):
    """过滤 strategy_trades 中标记为 legacy_dirty=true 的历史污染数据。

    深挖第 3 轮 (2026-05-08)：158 条历史 strategy_trades 因 Bug C
    （opened_at = closed_at - 1s 兜底）被标记 decision_context.legacy_dirty=true，
    所有学习/复盘查询都应排除它们。
    """
    from sqlalchemy import Text, cast
    decision_context_text = cast(StrategyTrade.decision_context, Text)
    return query.filter(
        (StrategyTrade.decision_context.is_(None))
        | (~decision_context_text.like('%"legacy_dirty": true%'))
    )


@dataclass
class LearningReport:
    """学习报告"""
    strategy_id: str
    period_days: int = 7
    total_trades_analyzed: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    best_market_regime: str = ""
    worst_market_regime: str = ""
    patterns_found: int = 0
    lessons_extracted: int = 0
    prompt_evolved: bool = False
    parameters_adapted: bool = False
    report_time: str = ""


class StrategyLearningService:
    """策略自学习服务"""

    def run_periodic_review(self, strategy_id: str, days: int = 7) -> Dict[str, Any]:
        """定期复盘：分析近期交易，提取教训，进化提示词"""
        db = SessionLocal()
        try:
            strategy = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == strategy_id
            ).first()
            if not strategy:
                return {"error": "策略不存在"}

            report = LearningReport(
                strategy_id=strategy_id,
                period_days=days,
                report_time=datetime.now(timezone.utc).isoformat(),
            )

            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            trades = _exclude_legacy_dirty(db.query(StrategyTrade).filter(
                StrategyTrade.strategy_id == strategy_id,
                StrategyTrade.opened_at >= cutoff,
            )).order_by(StrategyTrade.opened_at.desc()).all()

            report.total_trades_analyzed = len(trades)

            if not trades:
                logger.info(f"[Learning] {strategy_id} 近 {days} 天无交易记录")
                return asdict(report)

            trade_analysis = self._analyze_trades(trades)
            report.winning_trades = trade_analysis["wins"]
            report.losing_trades = trade_analysis["losses"]
            report.win_rate = trade_analysis["win_rate"]
            report.avg_win_pct = trade_analysis["avg_win"]
            report.avg_loss_pct = trade_analysis["avg_loss"]

            patterns = self._extract_patterns(trades)
            report.patterns_found = len(patterns.get("success_patterns", [])) + len(patterns.get("failure_patterns", []))

            lessons = self._extract_lessons(trades, trade_analysis)
            report.lessons_extracted = len(lessons)

            regime_perf = self._analyze_by_regime(trades)
            if regime_perf:
                best = max(regime_perf.items(), key=lambda x: x[1].get("win_rate", 0))
                worst = min(regime_perf.items(), key=lambda x: x[1].get("win_rate", 1))
                report.best_market_regime = best[0]
                report.worst_market_regime = worst[0]

            self._update_strategy_memory(
                db, strategy_id, trade_analysis, patterns, lessons, regime_perf
            )

            prompt_updated = self._evolve_prompt(db, strategy, lessons, patterns)
            report.prompt_evolved = prompt_updated

            params_updated = self._adapt_parameters(db, strategy, trade_analysis, regime_perf)
            report.parameters_adapted = params_updated

            # V3 整合: 因子贡献度复盘 → 更新策略因子权重
            factor_updated = self._review_factor_weights(db, strategy_id, trade_analysis)

            logger.info(
                f"[Learning] {strategy_id} 复盘完成: "
                f"trades={report.total_trades_analyzed}, wr={report.win_rate:.1%}, "
                f"patterns={report.patterns_found}, lessons={report.lessons_extracted}, "
                f"prompt_evolved={report.prompt_evolved}, factor_updated={factor_updated}"
            )
            return asdict(report)

        except Exception as e:
            logger.error(f"[Learning] 复盘异常 {strategy_id}: {e}", exc_info=True)
            return {"error": str(e)}
        finally:
            db.close()

    def run_all_reviews(self, days: int = 7) -> List[Dict]:
        """对所有 active 策略执行复盘，并检查是否有策略可以晋升为模板"""
        db = SessionLocal()
        try:
            strategies = db.query(AIStrategy).filter(
                AIStrategy.status.in_(["active", "paused"])
            ).all()
            results = []
            for s in strategies:
                report = self.run_periodic_review(s.strategy_id, days)
                results.append(report)

            # 复盘后检查是否有策略达到晋升条件
            try:
                promoted = self._check_and_promote_strategies(db)
                if promoted:
                    logger.info(f"[Learning] 本轮复盘晋升了 {len(promoted)} 个策略为模板")
                    self._notify_evolution_new_templates(promoted)
            except Exception as e:
                logger.warning(f"[Learning] 策略晋升检查失败: {e}")

            return results
        finally:
            db.close()

    def _check_and_promote_strategies(self, db: Session) -> List[str]:
        """
        检查所有策略的 StrategyMemory，达到晋升条件则自动提升为模板。
        多指标综合评估：
        - total_trades >= 15
        - win_rate >= 0.50
        - real_sharpe >= 0.5 OR (win_rate >= 0.55 AND max_drawdown <= 0.15)
        """
        import uuid
        promoted = []
        try:
            memories = db.query(StrategyMemory).filter(
                StrategyMemory.total_trades >= 15,
                StrategyMemory.win_rate >= 0.50,
            ).all()

            for memory in memories:
                # [P0-3 口径修复] 用交易级真实年化 Sharpe 替代盈亏符号 EMA
                # （mem.sharpe_ratio 值域 [-1,1]，与 0.5 阈值量纲不符）。
                _real_sharpe = 0.0
                try:
                    from backend.services.training_live_promote_service import compute_real_trade_metrics
                    _real_sharpe = float(
                        compute_real_trade_metrics(db, memory.strategy_id).get("real_sharpe") or 0.0
                    )
                except Exception:
                    _real_sharpe = 0.0
                sharpe_ok = _real_sharpe >= 0.5
                alt_ok = (memory.win_rate or 0) >= 0.55 and (memory.max_drawdown or 1) <= 0.15
                if not sharpe_ok and not alt_ok:
                    continue

                # 去重检查：查找该 strategy_id 是否已晋升过模板
                # 先查策略名，以便 name 精确匹配
                strategy = db.query(AIStrategy).filter(
                    AIStrategy.strategy_id == memory.strategy_id
                ).first()
                if not strategy:
                    continue

                # [2026-08-14 F4 整改] 中长线晋升守卫：
                # 历史问题——早期样本胜率=1.0 / sharpe=0.5 的退化指标直接晋升激活
                # （「[实战验证]」模板 backtest_win_rate=1.0 占位数据实锤）。
                # 中长线要求更严：样本>=30、胜率落在 (0.05,0.95) 区间、回撤<=25%。
                # 短线维持原阈值（不触碰短期策略逻辑）。
                _tier = str(getattr(strategy, "timeframe_tier", "") or "").strip().lower()
                if _tier in ("mid", "long"):
                    _wr = float(memory.win_rate or 0)
                    _dd = float(memory.max_drawdown or 1)
                    if int(memory.total_trades or 0) < 30 or _wr <= 0.05 or _wr >= 0.95 or _dd > 0.25:
                        logger.info(
                            "[Promote] %s 中长线晋升被 F4 守卫拒绝: n=%s wr=%.2f dd=%.2f",
                            memory.strategy_id, memory.total_trades, _wr, _dd,
                        )
                        continue

                expected_name = f"[实战验证] {strategy.name}"
                already = db.query(StrategyTemplate).filter(
                    StrategyTemplate.source == "promoted",
                    StrategyTemplate.name == expected_name,
                ).first()
                # 兜底：也检查 description 中是否包含 strategy_id（兼容旧数据）
                if not already:
                    already = db.query(StrategyTemplate).filter(
                        StrategyTemplate.source == "promoted",
                        StrategyTemplate.description.contains(memory.strategy_id),
                    ).first()
                if already:
                    continue

                trading_style = (strategy.prompt_variables or {}).get("trading_style", "trend")
                tpl_id = f"tpl_pro_{uuid.uuid4().hex[:8]}"

                from backend.services.strategy_library import build_promoted_strategy_config
                _promoted_cfg = build_promoted_strategy_config(strategy, memory)
                # StrategyMemory 没有 avg_pnl_pct 字段；用已有盈亏均值估算平均单笔收益，
                # 防止晋升评分阶段抛异常导致整批候选回滚。
                avg_trade_pnl = (
                    (memory.win_rate or 0) * (memory.avg_profit or 0)
                    + (1 - (memory.win_rate or 0)) * (memory.avg_loss or 0)
                )

                tpl = StrategyTemplate(
                    template_id=tpl_id,
                    name=f"[实战验证] {strategy.name}",
                    description=f"从实战策略 {strategy.strategy_id} 自动晋升。胜率{memory.win_rate*100:.0f}% / 夏普{memory.sharpe_ratio:.2f} / {memory.total_trades}笔交易。{strategy.description or ''}",
                    category=trading_style,
                    market_regime="all",
                    risk_level="moderate",
                    timeframe=strategy.timeframe or "15m",
                    tier=getattr(strategy, "timeframe_tier", None) or "mid",
                    strategy_config=_promoted_cfg,
                    source="promoted",
                    author="auto_learning",
                    version="1.0",
                    backtest_win_rate=memory.win_rate,
                    backtest_sharpe=memory.sharpe_ratio,
                    backtest_max_drawdown=memory.max_drawdown,
                    backtest_total_trades=memory.total_trades,
                    is_active=True,
                    rating=min(5.0, 3.0 + memory.win_rate * 0.15 + max((memory.sharpe_ratio or 0), 0) * 1.0 + min(max(avg_trade_pnl, 0) * 20, 1.0)),
                    tags=["实战验证", "自动晋升", "promoted_live", trading_style],
                )
                db.add(tpl)
                promoted.append(memory.strategy_id)
                logger.info(f"[Promote] 策略 {memory.strategy_id} 自动晋升为模板 {tpl_id} (胜率{memory.win_rate*100:.0f}%)")

            if promoted:
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[Promote] 策略晋升失败: {e}")

        return promoted

    def try_promote_single_strategy(self, db: Session, strategy_id: str) -> Optional[str]:
        """单笔 outcome 后尝试晋升单个策略（auto_* 无 template_id 时回灌模板库）。"""
        if not strategy_id:
            return None
        try:
            memory = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id,
                StrategyMemory.total_trades >= 15,
                StrategyMemory.win_rate >= 0.50,
            ).first()
            if not memory:
                return None
            sharpe_ok = (memory.sharpe_ratio or 0) >= 0.5
            alt_ok = (memory.win_rate or 0) >= 0.55 and (memory.max_drawdown or 1) <= 0.15
            if not sharpe_ok and not alt_ok:
                return None
            promoted = self._check_and_promote_strategies(db)
            if strategy_id in promoted:
                return strategy_id
        except Exception as exc:
            logger.debug(f"[Promote] 单策略晋升跳过 {strategy_id}: {exc}")
        return None

    @staticmethod
    def _notify_evolution_new_templates(promoted_strategy_ids: List[str]):
        """实盘策略晋升为模板后，通知进化系统有新的可进化模板。

        闭环流程：实盘好成绩 → 晋升为模板 → 回测进化可以优化这些模板参数
        """
        try:
            from backend.services.evolution_scheduler import evolution_scheduler
            for sid in promoted_strategy_ids:
                logger.info(
                    f"[Learning→Evolver] 实盘策略 {sid} 已晋升为模板，"
                    f"下次进化周期将自动包含"
                )
            if len(promoted_strategy_ids) >= 3:
                logger.info(
                    f"[Learning→Evolver] 大量晋升 ({len(promoted_strategy_ids)} 个)，"
                    f"触发紧急进化以快速优化新模板"
                )
                evolution_scheduler.trigger_emergency_evolution(
                    template_id="all_new",
                    reason=f"批量晋升 {len(promoted_strategy_ids)} 个新模板",
                )
        except Exception as e:
            logger.warning(f"[Learning→Evolver] 通知进化系统失败: {e}")

    def get_learning_dashboard(self, strategy_id: str) -> Dict[str, Any]:
        """获取策略学习仪表盘数据"""
        db = SessionLocal()
        try:
            memory = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()

            training_records = db.query(PromptTrainingRecord).filter(
                PromptTrainingRecord.strategy_id == strategy_id
            ).order_by(PromptTrainingRecord.created_at.desc()).limit(10).all()

            recent_trades = _exclude_legacy_dirty(db.query(StrategyTrade).filter(
                StrategyTrade.strategy_id == strategy_id,
            )).order_by(StrategyTrade.opened_at.desc()).limit(20).all()

            return {
                "memory": {
                    "total_trades": memory.total_trades if memory else 0,
                    "win_rate": memory.win_rate if memory else 0,
                    "sharpe_ratio": memory.sharpe_ratio if memory else 0,
                    "max_drawdown": memory.max_drawdown if memory else 0,
                    "performance_by_regime": (getattr(memory, 'performance_by_regime', None) or {}) if memory else {},
                    "successful_patterns": (getattr(memory, 'successful_patterns', None) or []) if memory else [],
                    "failed_patterns": (getattr(memory, 'failed_patterns', None) or []) if memory else [],
                    "key_lessons": memory.key_lessons if memory else [],
                } if memory else None,
                "prompt_evolution": [
                    {
                        "id": r.id,
                        "training_metrics": r.training_metrics,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in training_records
                ],
                "recent_trades": [
                    {
                        "id": t.id,
                        "symbol": t.symbol,
                        "side": t.side,
                        "entry_price": t.entry_price,
                        "exit_price": t.exit_price,
                        "pnl_pct": t.pnl_pct,
                        "status": t.status,
                        "decision_quality_score": t.decision_quality_score,
                        "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                        "execution_type": self._get_execution_type(t),
                    }
                    for t in recent_trades
                ],
            }
        finally:
            db.close()

    @staticmethod
    def _get_execution_type(trade: StrategyTrade) -> str:
        """从 decision_context JSON 提取 execution_type"""
        try:
            import json
            ctx = trade.decision_context
            if isinstance(ctx, str):
                ctx = json.loads(ctx)
            return ctx.get("execution_type", "live") if ctx else "live"
        except Exception:
            return "live"

    # =========================================================================
    # 分析方法
    # =========================================================================

    def _analyze_trades(self, trades: List[StrategyTrade]) -> Dict[str, Any]:
        """分析交易结果"""
        wins = [t for t in trades if t.pnl_pct and t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct and t.pnl_pct <= 0]
        total = len(trades)

        return {
            "total": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / total if total > 0 else 0,
            "avg_win": sum(t.pnl_pct for t in wins) / len(wins) if wins else 0,
            "avg_loss": sum(t.pnl_pct for t in losses) / len(losses) if losses else 0,
            "total_pnl": sum(t.pnl_pct or 0 for t in trades),
            "best_trade": max((t.pnl_pct or 0 for t in trades), default=0),
            "worst_trade": min((t.pnl_pct or 0 for t in trades), default=0),
        }

    def _extract_patterns(self, trades: List[StrategyTrade]) -> Dict[str, List]:
        """提取成功/失败模式"""
        success_patterns = []
        failure_patterns = []

        for t in trades:
            context = {}
            if t.decision_context:
                try:
                    context = json.loads(t.decision_context) if isinstance(t.decision_context, str) else t.decision_context
                except Exception:
                    pass

            pattern = {
                "symbol": t.symbol,
                "side": t.side,
                "pnl_pct": t.pnl_pct or 0,
                "market_regime": context.get("market_environment", {}).get("cycle", "unknown"),
                "volatility": context.get("market_environment", {}).get("volatility", "unknown"),
            }

            if t.pnl_pct and t.pnl_pct > 0.02:
                success_patterns.append(pattern)
            elif t.pnl_pct and t.pnl_pct < -0.02:
                failure_patterns.append(pattern)

        return {
            "success_patterns": success_patterns[:10],
            "failure_patterns": failure_patterns[:10],
        }

    def _extract_lessons(self, trades: List[StrategyTrade], analysis: Dict) -> List[Dict]:
        """从交易中提取关键教训"""
        lessons = []

        if analysis["win_rate"] < 0.4:
            lessons.append({
                "type": "low_win_rate",
                "severity": "high",
                "message": f"胜率偏低({analysis['win_rate']:.0%})，考虑增加EMA滤波或提高入场门槛",
            })

        if analysis["avg_loss"] and abs(analysis["avg_loss"]) > abs(analysis.get("avg_win", 0)) * 1.5:
            lessons.append({
                "type": "bad_risk_reward",
                "severity": "high",
                "message": f"平均亏损({analysis['avg_loss']:.1%})远大于平均盈利({analysis['avg_win']:.1%})，需收紧止损",
            })

        losing_streaks = 0
        max_streak = 0
        for t in trades:
            if t.pnl_pct and t.pnl_pct <= 0:
                losing_streaks += 1
                max_streak = max(max_streak, losing_streaks)
            else:
                losing_streaks = 0

        if max_streak >= 3:
            lessons.append({
                "type": "losing_streak",
                "severity": "medium",
                "message": f"出现连续 {max_streak} 笔亏损，建议设置连亏熔断机制",
            })

        return lessons

    def _analyze_by_regime(self, trades: List[StrategyTrade]) -> Dict[str, Dict]:
        """按市场状态分类分析表现"""
        regime_map: Dict[str, Dict] = {}

        for t in trades:
            regime = "unknown"
            if t.decision_context:
                try:
                    ctx = json.loads(t.decision_context) if isinstance(t.decision_context, str) else t.decision_context
                    regime = ctx.get("market_environment", {}).get("cycle", "unknown")
                except Exception:
                    pass

            if regime not in regime_map:
                regime_map[regime] = {"trades": 0, "wins": 0, "total_pnl": 0}

            regime_map[regime]["trades"] += 1
            if t.pnl_pct and t.pnl_pct > 0:
                regime_map[regime]["wins"] += 1
            regime_map[regime]["total_pnl"] += t.pnl_pct or 0

        for regime, data in regime_map.items():
            data["win_rate"] = data["wins"] / data["trades"] if data["trades"] > 0 else 0

        return regime_map

    # =========================================================================
    # V3 整合: 因子复盘与权重调整
    # =========================================================================

    def _review_factor_weights(
        self,
        db: Session,
        strategy_id: str,
        trade_analysis: Dict,
    ) -> bool:
        """V3 整合: 基于因子贡献度分析，更新策略的因子权重配置。

        闭环流程:
        1. 调用 signal_feedback_tracker.analyze_factor_contribution() 获取因子贡献度
        2. 调用 factor_weighting.apply_feedback_adjustments() 计算调整后权重
        3. 更新 AIStrategy.factor_weights 字段
        """
        try:
            # 1. 获取因子贡献度
            from backend.services.signal_feedback_tracker import signal_feedback_tracker
            contributions = signal_feedback_tracker.analyze_factor_contribution(
                db, strategy_id=strategy_id, lookback_days=30
            )
            if not contributions:
                return False

            # 2. 通过 DynamicFactorWeighting 计算调整后的权重
            from backend.services.factor_engine.factor_weighting import get_factor_weighting
            weighting = get_factor_weighting()
            adjusted = weighting.apply_feedback_adjustments(contributions)
            if not adjusted:
                return False

            # 3. 更新 AIStrategy.factor_weights
            strategy = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == strategy_id
            ).first()
            if not strategy:
                return False

            current_weights = strategy.factor_weights or {}
            if isinstance(current_weights, str):
                import json as _json
                current_weights = _json.loads(current_weights)

            # 平滑合并: 旧权重 70% + 新权重 30%
            merged = {}
            all_factors = set(list(current_weights.keys()) + list(adjusted.keys()))
            for f in all_factors:
                old_w = current_weights.get(f, 0)
                new_w = adjusted.get(f, 0)
                merged[f] = round(0.7 * old_w + 0.3 * new_w, 4)

            # 归一化
            total = sum(merged.values())
            if total > 0:
                merged = {k: round(v / total, 4) for k, v in merged.items()}

            strategy.factor_weights = merged
            db.commit()

            # 记录 top-3 调整
            top3 = sorted(adjusted.items(), key=lambda x: x[1], reverse=True)[:3]
            logger.info(
                f"[Learning] {strategy_id} 因子权重已更新: "
                f"top-3={top3}, 共 {len(merged)} 个因子"
            )
            return True

        except Exception as e:
            logger.error(f"[Learning] 因子复盘失败 {strategy_id}: {e}")
            try:
                db.rollback()
            except Exception:
                pass
            return False

    # =========================================================================
    # 进化与适应
    # =========================================================================

    def _update_strategy_memory(
        self,
        db: Session,
        strategy_id: str,
        analysis: Dict,
        patterns: Dict,
        lessons: List,
        regime_perf: Dict,
    ):
        """增量更新策略记忆 — 委托给 unified_learning 统一入口，避免双写冲突。

        统计字段（total_trades/win_rate/sharpe 等）由 unified_learning 的增量公式维护。
        模式/教训等定性字段仍在此处更新（unified_learning 不负责这些）。
        """
        try:
            memory = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()

            if not memory:
                # 首次创建：直接用当前窗口数据初始化
                memory = StrategyMemory(
                    strategy_id=strategy_id,
                    total_trades=analysis["total"],
                    win_rate=analysis["win_rate"],
                    avg_profit=analysis["avg_win"],
                    avg_loss=analysis["avg_loss"],
                )
                db.add(memory)
                db.flush()
            else:
                # 已有记忆：用统一的增量公式更新，而非自行加权平均
                # 这样与 unified_learning_service._update_strategy_memory 保持一致
                self._incremental_update_stats(memory, analysis)

            # Sharpe 统一计算（基于最近100笔交易的 pnl_pct 分布）
            self._recompute_sharpe(memory, db, strategy_id)

            # max_drawdown 更新
            pnl_pcts = self._get_recent_pnl_pcts(db, strategy_id, 100)
            neg_pnls = [abs(p) for p in pnl_pcts if p < 0]
            if neg_pnls:
                memory.max_drawdown = max(memory.max_drawdown or 0, max(neg_pnls))

            # 定性字段：merge 而非整表覆盖（避免空窗口抹掉在线 patterns）
            try:
                memory.performance_by_regime = self._merge_regime_perf(
                    getattr(memory, 'performance_by_regime', None) or {}, regime_perf or {}
                )
            except Exception:
                pass
            try:
                memory.successful_patterns = self._merge_pattern_lists(
                    getattr(memory, 'successful_patterns', None) or [],
                    patterns.get("success_patterns", []) or [],
                    max_items=20,
                )
            except Exception:
                pass
            try:
                memory.failed_patterns = self._merge_pattern_lists(
                    getattr(memory, 'failed_patterns', None) or [],
                    patterns.get("failure_patterns", []) or [],
                    max_items=20,
                )
            except Exception:
                pass
            try:
                memory.key_lessons = self._merge_lesson_lists(
                    getattr(memory, 'key_lessons', None) or [], lessons or [], max_items=15,
                )
            except Exception:
                pass
            memory.updated_at = datetime.now(timezone.utc)

            db.commit()
        except Exception as e:
            logger.error(f"[Learning] 更新策略记忆失败: {e}")
            db.rollback()

    @staticmethod
    def _pattern_key(item) -> str:
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, dict):
            return str(item.get("pattern") or item.get("description") or item.get("id") or item)
        return str(item)

    @classmethod
    def _merge_pattern_lists(cls, existing: list, new_items: list, max_items: int = 20) -> list:
        if not new_items:
            return existing
        merged = list(existing or [])
        seen = {cls._pattern_key(x) for x in merged}
        for item in new_items:
            key = cls._pattern_key(item)
            if not key or key in seen:
                continue
            merged.append(item)
            seen.add(key)
        return merged[-max_items:]

    @staticmethod
    def _merge_lesson_lists(existing: list, new_items: list, max_items: int = 15) -> list:
        if not new_items:
            return existing or []
        merged = list(existing or [])
        seen = set()
        for item in merged + list(new_items):
            key = item if isinstance(item, str) else str(
                item.get("message") or item.get("lesson") or item
            )
            if not key.strip() or key in seen:
                continue
            merged.append(item)
            seen.add(key)
        return merged[-max_items:]

    @staticmethod
    def _merge_regime_perf(existing: dict, new_perf: dict) -> dict:
        if not new_perf:
            return existing or {}
        out = dict(existing or {})
        for rk, rv in new_perf.items():
            if not isinstance(rv, dict):
                continue
            old = out.get(rk, {}) if isinstance(out.get(rk), dict) else {}
            nt = int(rv.get("trades", 0) or 0)
            ot = int(old.get("trades", 0) or 0)
            if nt <= 0 and ot <= 0:
                continue
            nw = int(rv.get("wins", 0) or 0)
            ow = int(old.get("wins", 0) or 0)
            out[rk] = {
                "trades": ot + nt,
                "wins": ow + nw,
                "total_pnl": float(old.get("total_pnl", 0) or 0) + float(rv.get("total_pnl", 0) or 0),
            }
        return out

    @staticmethod
    def _incremental_update_stats(memory: StrategyMemory, analysis: Dict):
        """增量更新统计字段 — 使用与 unified_learning_service 一致的增量平均公式。

        公式: new_value = old_value + (window_value - old_value) / n
        其中 n = max(existing_total, 1) + window_total
        """
        window_total = analysis.get("total", 0)
        if window_total <= 0:
            return

        existing_total = memory.total_trades or 0
        n = existing_total + window_total

        if existing_total > 0:
            # 增量平均：等价于 unified_learning 的 old + (new - old) / n
            memory.win_rate = (memory.win_rate or 0) + (analysis["win_rate"] - (memory.win_rate or 0)) * window_total / n
            memory.avg_profit = (memory.avg_profit or 0) + (analysis["avg_win"] - (memory.avg_profit or 0)) * window_total / n
            memory.avg_loss = (memory.avg_loss or 0) + (analysis["avg_loss"] - (memory.avg_loss or 0)) * window_total / n
        else:
            memory.win_rate = analysis["win_rate"]
            memory.avg_profit = analysis["avg_win"]
            memory.avg_loss = analysis["avg_loss"]

        memory.total_trades = n

    @staticmethod
    def _recompute_sharpe(memory: StrategyMemory, db: Session, strategy_id: str):
        """统一 Sharpe 计算 — 使用年化因子，与 strategy_coordinator 保持一致。

        公式: sharpe = mean(pnl_pct) / std(pnl_pct) * sqrt(365*24/bar_interval)
        对加密货币 7x24h 市场，使用 sqrt(365*24)=sqrt(8760)≈93.5 作为日频年化。
        由于 pnl_pct 基于交易级别（非日频），使用简化因子 sqrt(252) 与股票对齐。
        """
        pnl_pcts = StrategyLearningService._get_recent_pnl_pcts(db, strategy_id, 100)
        if len(pnl_pcts) >= 5:
            import statistics
            mean_pnl = statistics.mean(pnl_pcts)
            std_pnl = statistics.stdev(pnl_pcts) if len(pnl_pcts) > 1 else 0.001
            # 使用 sqrt(252) 年化因子，与 strategy_coordinator.py 统一
            import math
            memory.sharpe_ratio = round(mean_pnl / max(std_pnl, 0.0001) * (252 ** 0.5), 4)

    @staticmethod
    def _get_recent_pnl_pcts(db: Session, strategy_id: str, limit: int = 100) -> List[float]:
        """获取策略最近 N 笔交易的 pnl_pct 列表"""
        return [t.pnl_pct for t in _exclude_legacy_dirty(db.query(StrategyTrade).filter(
            StrategyTrade.strategy_id == strategy_id,
            StrategyTrade.pnl_pct.isnot(None),
        )).order_by(StrategyTrade.opened_at.desc()).limit(limit).all() if t.pnl_pct is not None]

    def _evolve_prompt(
        self,
        db: Session,
        strategy: AIStrategy,
        lessons: List[Dict],
        patterns: Dict,
    ) -> bool:
        """基于学习结果进化策略提示词（实际修改提示词文本）"""
        # 2026-06-11：Prompt 自动进化默认禁用（历史 36/36 失败），
        # 反馈改走 v5_runtime_gates 运行时门槛闭环。需要时设 PROMPT_EVOLUTION_ENABLED=true。
        try:
            from backend.config.settings import PROMPT_EVOLUTION_ENABLED
            if not PROMPT_EVOLUTION_ENABLED:
                logger.debug(f"[Learning] {strategy.strategy_id} Prompt 进化已禁用（PROMPT_EVOLUTION_ENABLED=false）")
                return False
        except ImportError:
            pass

        if not lessons and not patterns.get("failure_patterns"):
            return False

        try:
            prompt_tpl = None
            if strategy.master_prompt_template_id:
                from backend.database.models import PromptTemplate
                prompt_tpl = db.query(PromptTemplate).filter(
                    PromptTemplate.id == strategy.master_prompt_template_id
                ).first()

            if not prompt_tpl:
                logger.info(f"[Learning] {strategy.strategy_id} 无绑定提示词模板，跳过进化")
                return False

            lesson_text = "\n".join(
                f"- [{l['severity']}] {l['message']}" for l in lessons[:5]
            )
            failure_text = ""
            for p in patterns.get("failure_patterns", [])[:3]:
                failure_text += f"\n- {p['symbol']} {p['side']} 在{p['market_regime']}环境下亏损{p['pnl_pct']*100:.1f}%"
            success_text = ""
            for p in patterns.get("success_patterns", [])[:3]:
                success_text += f"\n- {p['symbol']} {p['side']} 在{p['market_regime']}环境下盈利{p['pnl_pct']*100:.1f}%"

            evolution_instruction = (
                f"你是一个加密货币交易提示词优化专家。\n"
                f"以下是当前策略提示词的交易表现分析：\n\n"
                f"关键教训:\n{lesson_text}\n\n"
                f"成功模式:{success_text or '暂无'}\n\n"
                f"失败模式:{failure_text or '暂无'}\n\n"
                f"请基于以上分析，对当前提示词进行微调优化。\n"
                f"规则：\n"
                f"1. 保留提示词的整体结构和所有变量占位符（如 {{current_time}} 等）\n"
                f"2. 在风险控制、入场条件、市况判断等相关段落中注入上述教训\n"
                f"3. 不要删除任何现有的有效内容，只做增量优化\n"
                f"4. 用中文输出\n\n"
                f"当前提示词:\n{prompt_tpl.template_text[:3000]}"
            )

            # ── 整改#15：DSPy metric 驱动编译（替代盲目模板突变，修 36/36 失败根因）──
            try:
                import os as _os
                if _os.getenv("DSPY_COMPILE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
                    from backend.services.ai.prompt_compiler import TradingPromptCompiler, Signature
                    _train = [{"lesson": l.get("message", ""), "severity": l.get("severity", "")} for l in lessons[:10]]
                    _candidates = [
                        "分析市场结构与流动性，明确止损位与风险回报比，再给方向与置信度。",
                        "基于历史教训优化风控纪律，震荡市减仓、趋势市顺势，禁止追涨杀跌。",
                        evolution_instruction[:300],
                    ]

                    def _dspy_metric(instr, demos):
                        s = 0.0
                        for kw in ("止损", "风险", "回撤", "纪律", "结构", "流动性"):
                            if kw in (instr or ""):
                                s += 0.12
                        s += min(len(demos), 4) * 0.05
                        return s

                    _sig = Signature(
                        name=f"strategy_{strategy.strategy_id}",
                        base_instruction=prompt_tpl.template_text[:500],
                    )
                    _compiled = TradingPromptCompiler(_dspy_metric).compile(
                        _sig, _train, instruction_candidates=_candidates, max_trials=40,
                    )
                    if _compiled.backend != "noop" and _compiled.optimized_instruction:
                        evolution_instruction = (
                            f"{_compiled.optimized_instruction}\n\n"
                            f"【编译得分 {_compiled.compile_metric_score:.2f} / trials {_compiled.trial_count}】\n"
                            f"{evolution_instruction}"
                        )
                        logger.info(
                            "[Learning][DSPy#15] %s 编译完成 score=%.2f trials=%d",
                            strategy.strategy_id, _compiled.compile_metric_score, _compiled.trial_count,
                        )
            except Exception as _dspy_err:
                logger.debug("[Learning][DSPy#15] 编译跳过: %s", _dspy_err)

            # 谎言 3 修复（2026-05-08）：
            #   原代码只能拿到 Optional[str]，失败时不知道为什么；现在返回 (text, debug)
            #   tuple，把 raw_response_type / raw_preview / error_class / error_message 都
            #   写进 training_metrics，让进化失败可定位。
            optimized_text, _evo_debug = self._call_llm_for_prompt_evolution_v2(
                evolution_instruction,
                account_id=getattr(strategy, "account_id", None),
            )

            _min_len = 120
            _resp_len = len(optimized_text) if optimized_text else 0
            if not optimized_text or _resp_len < _min_len:
                _fail_reason = "llm_returned_none" if optimized_text is None else (
                    "response_too_short" if _resp_len < _min_len else "unknown")
                logger.warning(
                    f"[Learning] prompt 进化失败 strategy={strategy.strategy_id} "
                    f"reason={_fail_reason} len={_resp_len} debug={_evo_debug}"
                )
                record = PromptTrainingRecord(
                    strategy_id=strategy.strategy_id,
                    base_prompt_id=strategy.master_prompt_template_id or 0,
                    training_metrics=json.dumps({
                        "lessons": lessons,
                        "failure_patterns": patterns.get("failure_patterns", []),
                        "status": "llm_failed",
                        "fail_reason": _fail_reason,
                        "min_len_threshold": _min_len,
                        "response_len": _resp_len,
                        "raw_response_type": _evo_debug.get("raw_response_type"),
                        "raw_preview": _evo_debug.get("raw_preview"),
                        "error_class": _evo_debug.get("error_class"),
                        "error_message": _evo_debug.get("error_message"),
                        "account_id": _evo_debug.get("account_id"),
                        "duration_ms": _evo_debug.get("duration_ms"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, ensure_ascii=False),
                )
                db.add(record)
                db.commit()
                return False

            from backend.database.models import PromptTemplate as PT
            old_version = strategy.prompt_version or 1
            new_version = old_version + 1

            new_prompt = PT(
                key=f"{prompt_tpl.key}_v{new_version}",
                name=f"{prompt_tpl.name} (进化v{new_version})",
                description=f"基于 {len(lessons)} 条教训自动进化。原版本: {prompt_tpl.id}",
                template_text=optimized_text,
                system_template_text=prompt_tpl.system_template_text,
                is_system="false",
                created_by="prompt_evolution",
            )
            db.add(new_prompt)
            db.flush()

            strategy.master_prompt_template_id = new_prompt.id
            strategy.prompt_version = new_version

            # ── 同步到 AccountPromptBinding，确保进化后的 Prompt 真正被交易决策使用 ──
            synced_accounts = 0
            try:
                from backend.database.models import Account as _Acct
                # 找到所有使用该策略的账户
                bindings_to_update = []
                if strategy.account_id:
                    # 该策略直接关联的账户
                    binding = db.query(AccountPromptBinding).filter(
                        AccountPromptBinding.account_id == strategy.account_id,
                    ).first()
                    if binding:
                        bindings_to_update.append(binding)

                for binding in bindings_to_update:
                    old_tpl_id = binding.prompt_template_id
                    if old_tpl_id != new_prompt.id:
                        binding.prompt_template_id = new_prompt.id
                        binding.updated_by = "prompt_evolution"
                        synced_accounts += 1
                        logger.info(
                            f"[Learning] AccountPromptBinding 同步: "
                            f"account={binding.account_id} prompt {old_tpl_id}→{new_prompt.id}"
                        )
            except Exception as sync_err:
                logger.warning(f"[Learning] AccountPromptBinding 同步失败（不影响策略更新）: {sync_err}")

            record = PromptTrainingRecord(
                strategy_id=strategy.strategy_id,
                base_prompt_id=prompt_tpl.id,
                optimized_prompt_id=new_prompt.id,
                training_metrics=json.dumps({
                    "lessons": lessons,
                    "failure_patterns": patterns.get("failure_patterns", []),
                    "success_patterns": patterns.get("success_patterns", []),
                    "old_version": old_version,
                    "new_version": new_version,
                    "synced_accounts": synced_accounts,
                    "status": "evolved",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }),
            )
            db.add(record)
            db.commit()

            # ── PromptTemplate 版本清理：只保留最近 MAX_PROMPT_VERSIONS 个版本 ──
            MAX_PROMPT_VERSIONS = 5
            try:
                base_key = prompt_tpl.key.split("_v")[0] if "_v" in prompt_tpl.key else prompt_tpl.key
                old_prompts = (
                    db.query(PT)
                    .filter(PT.key.like(f"{base_key}_v%"), PT.created_by == "prompt_evolution")
                    .order_by(PT.created_at.desc())
                    .all()
                )
                if len(old_prompts) > MAX_PROMPT_VERSIONS:
                    for obsolete in old_prompts[MAX_PROMPT_VERSIONS:]:
                        # 确认没有任何策略正在使用该模板后再删除
                        in_use = db.query(AIStrategy).filter(
                            AIStrategy.master_prompt_template_id == obsolete.id
                        ).first()
                        if not in_use:
                            logger.info(f"[Learning] 清理旧版 PromptTemplate: key={obsolete.key} id={obsolete.id}")
                            db.delete(obsolete)
                    db.commit()
            except Exception as cleanup_err:
                logger.warning(f"[Learning] PromptTemplate 旧版本清理失败（不影响进化结果）: {cleanup_err}")

            logger.info(
                f"[Learning] 策略 {strategy.strategy_id} 提示词已进化至 v{new_version} "
                f"(新模板ID: {new_prompt.id}, 同步{synced_accounts}个账户绑定)"
            )
            return True

        except Exception as e:
            logger.error(f"[Learning] 提示词进化失败: {e}")
            db.rollback()
            return False

    def _call_llm_for_prompt_evolution(
        self, instruction: str, account_id: Optional[int] = None,
    ) -> Optional[str]:
        """[向后兼容] 仅返回字符串；新代码请用 _call_llm_for_prompt_evolution_v2。"""
        text, _ = self._call_llm_for_prompt_evolution_v2(instruction, account_id)
        return text

    def _call_llm_for_prompt_evolution_v2(
        self, instruction: str, account_id: Optional[int] = None,
    ) -> tuple:
        """调用 LLM 生成优化后的提示词，返回 (text_or_None, debug_dict)。

        谎言 3 修复（2026-05-08）：把失败原因暴露出来。
        debug_dict 字段：
        - raw_response_type: 实际返回类型名（dict/list/str/NoneType）
        - raw_preview: 实际返回的前 200 字符
        - error_class / error_message: 异常时的类名和消息
        - account_id / duration_ms
        """
        import time as _time
        debug: dict = {
            "account_id": account_id,
            "raw_response_type": None,
            "raw_preview": None,
            "error_class": None,
            "error_message": None,
            "duration_ms": None,
        }
        _t0 = _time.time()
        try:
            from backend.services.ai_prompt_generation_service import AiPromptGenerationService
            db = SessionLocal()
            try:
                service = AiPromptGenerationService(db)
                result = service.generate_with_conversation(
                    messages=[{"role": "user", "content": instruction}],
                    account_id=account_id,
                )
                debug["duration_ms"] = int((_time.time() - _t0) * 1000)
                debug["raw_response_type"] = type(result).__name__
                try:
                    debug["raw_preview"] = (str(result)[:200]) if result is not None else None
                except Exception:
                    debug["raw_preview"] = "<unprintable>"

                # 兼容多种返回结构：str / {"content": "..."} / {"text": "..."}
                if isinstance(result, str):
                    return result, debug
                if isinstance(result, dict):
                    for _k in ("content", "text", "message", "output"):
                        _v = result.get(_k)
                        if isinstance(_v, str) and _v:
                            return _v, debug
                # 不是字符串也不是已知 dict 结构 → 失败
                return None, debug
            finally:
                db.close()
        except Exception as e:
            debug["duration_ms"] = int((_time.time() - _t0) * 1000)
            debug["error_class"] = type(e).__name__
            debug["error_message"] = str(e)[:500]
            logger.warning(
                f"[Learning] LLM 调用失败 acct={account_id} err={debug['error_class']}: {e}",
                exc_info=True,
            )
            return None, debug

    def evolve_prompt_from_backtest(self, template_id: str) -> bool:
        """从回测冠军的经验中进化策略提示词

        将回测发现的规律编入绑定该模板的策略提示词中。
        """
        db = SessionLocal()
        try:
            from backend.services.backtest_insight_compiler import insight_compiler
            from backend.database.models import PromptTemplate

            wisdom = insight_compiler.extract_wisdom(db, template_id)
            meta = wisdom.get("meta", {})
            if meta.get("runs_analyzed", 0) < 3:
                logger.info(f"[Learning] 模板 {template_id} 回测数据不足，跳过提示词进化")
                return False

            prompt_fragment = insight_compiler.compile_to_prompt_fragment(wisdom)
            if not prompt_fragment:
                return False

            strategies = db.query(AIStrategy).filter(
                AIStrategy.status.in_(["active", "paused"]),
                AIStrategy.master_prompt_template_id != None,
            ).all()

            evolved_count = 0
            for strategy in strategies:
                tpl = db.query(PromptTemplate).filter(
                    PromptTemplate.id == strategy.master_prompt_template_id,
                ).first()
                if not tpl:
                    continue

                if "{strategy_wisdom}" in (tpl.template_text or ""):
                    evolved_count += 1
                    continue

                # 将 {strategy_wisdom} 占位符和当前智慧写入模板
                tpl.template_text = (tpl.template_text or "") + "\n\n{strategy_wisdom}"
                if not hasattr(tpl, "wisdom_cache"):
                    pass
                # 同时把当前 fragment 存到模板的 prompt_variables 备用
                try:
                    pv = json.loads(tpl.prompt_variables or "{}") if hasattr(tpl, "prompt_variables") and tpl.prompt_variables else {}
                    pv["strategy_wisdom"] = prompt_fragment
                    tpl.prompt_variables = json.dumps(pv, ensure_ascii=False)
                except Exception:
                    pass

                record = PromptTrainingRecord(
                    strategy_id=strategy.strategy_id,
                    base_prompt_id=tpl.id,
                    training_metrics=json.dumps({
                        "source": "backtest_evolution",
                        "template_id": template_id,
                        "wisdom_meta": meta,
                        "status": "wisdom_injected",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }),
                )
                db.add(record)
                evolved_count += 1
                logger.info(f"[Learning] 模板 {tpl.id} 已注入回测智慧 ({len(prompt_fragment)} chars)")

            if evolved_count:
                insight_compiler.save_wisdom_to_db(db, template_id, wisdom)
                db.commit()
                logger.info(
                    f"[Learning] 回测模板 {template_id} 智慧已注入 {evolved_count} 个策略"
                )

            return evolved_count > 0

        except Exception as e:
            logger.error(f"[Learning] 回测提示词进化失败: {e}", exc_info=True)
            return False
        finally:
            db.close()

    def _adapt_parameters(
        self,
        db: Session,
        strategy: AIStrategy,
        analysis: Dict,
        regime_perf: Dict,
    ) -> bool:
        """根据表现自适应调整参数"""
        changed = False
        try:
            if analysis["win_rate"] < 0.35 and analysis["total"] >= 5:
                old_sl = strategy.stop_loss_pct or 0.05
                strategy.stop_loss_pct = max(old_sl * 0.85, 0.02)
                changed = True
                logger.info(f"[Learning] {strategy.strategy_id} 胜率低→收紧止损: {old_sl:.1%} → {strategy.stop_loss_pct:.1%}")

            if analysis["avg_win"] and analysis["avg_loss"]:
                if abs(analysis["avg_loss"]) > analysis["avg_win"] * 2:
                    old_tp = strategy.take_profit_pct or 0.10
                    strategy.take_profit_pct = min(old_tp * 1.2, 0.20)
                    changed = True
                    logger.info(f"[Learning] {strategy.strategy_id} 盈亏比差→扩大止盈: {old_tp:.1%} → {strategy.take_profit_pct:.1%}")

            if changed:
                db.commit()

            return changed

        except Exception as e:
            logger.error(f"[Learning] 参数适应失败: {e}")
            db.rollback()
            return False

    # ══════════════════════════════════════════════════════
    #  Phase 3: 日终复盘轻量版 + 因子信任分数更新
    # ══════════════════════════════════════════════════════

    def run_daily_review(self, strategy_id: str) -> Dict[str, Any]:
        """
        日终复盘轻量版：仅在当天有 ≥3 笔交易时触发。
        快速提取当日最佳/最差决策，更新 StrategyMemory.key_lessons。
        不做提示词进化（提示词进化仍保持一周一次）。
        """
        db = SessionLocal()
        try:
            from datetime import timedelta

            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            trades = _exclude_legacy_dirty(db.query(StrategyTrade).filter(
                StrategyTrade.strategy_id == strategy_id,
                StrategyTrade.closed_at >= today,
                StrategyTrade.status == "closed",
            )).order_by(StrategyTrade.closed_at.asc()).all()

            if len(trades) < 3:
                logger.debug(f"[Learning] {strategy_id} 今日仅 {len(trades)} 笔，跳过日终复盘")
                return {"reviewed": False, "reason": f"仅{len(trades)}笔交易", "strategy_id": strategy_id}

            wins = [t for t in trades if (t.pnl or 0) > 0]
            losses = [t for t in trades if (t.pnl or 0) < 0]
            total_pnl = sum(t.pnl or 0 for t in trades)
            win_rate = len(wins) / len(trades) if trades else 0

            # 提取最佳/最差决策
            best = max(trades, key=lambda t: t.pnl or 0) if wins else None
            worst = min(trades, key=lambda t: t.pnl or 0) if losses else None

            lessons = []
            if best:
                lessons.append(
                    f"今日最佳: {best.symbol} {best.side} PnL${best.pnl:+.2f} "
                    f"— 复盘入场理由以复用成功模式"
                )
            if worst:
                ctx = worst.decision_context if isinstance(worst.decision_context, dict) else {}
                reason = ctx.get("close_reason", "?")[:30]
                lessons.append(
                    f"今日最差: {worst.symbol} {worst.side} PnL${worst.pnl:+.2f} "
                    f"(平仓原因: {reason}) — 避免重蹈覆辙"
                )
            if losses and win_rate < 0.4:
                lessons.append(
                    f"⚠️ 今日胜率仅{win_rate:.0%}({len(wins)}/{len(trades)})，"
                    f"连亏趋势需警惕"
                )

            # 更新 StrategyMemory
            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()
            if mem:
                existing = list(mem.key_lessons or [])
                existing.append({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": "daily_review",
                    "lessons": lessons,
                    "win_rate": win_rate,
                    "total_pnl": total_pnl,
                })
                mem.key_lessons = existing[-20:]  # 保留最近20条
                db.commit()

            logger.info(
                f"[Learning] {strategy_id} 日终复盘: {len(trades)}笔 "
                f"胜率={win_rate:.0%} PnL=${total_pnl:+.2f}"
            )
            return {
                "reviewed": True,
                "strategy_id": strategy_id,
                "trades": len(trades),
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "lessons": lessons,
            }
        except Exception as e:
            logger.error(f"[Learning] 日终复盘异常 {strategy_id}: {e}")
            db.rollback()
            return {"reviewed": False, "error": str(e), "strategy_id": strategy_id}
        finally:
            db.close()

    # ══════════════════════════════════════════════════════
    #  P1: 多频率周期复评 (M-7)
    # ══════════════════════════════════════════════════════

    def run_periodic_review_by_freq(
        self, strategy_id: str, freq: str = "1h", days: int = 7
    ) -> Dict[str, Any]:
        """
        按频率执行周期复评 (L-2 周期学习多频率解耦版)。

        与 run_periodic_review 的关键区别:
        - 按频率独立分析，不同频率使用不同评价标准
        - 15m: 关注 win_rate + trade_freq + 单笔盈亏比
        - 1h:  关注 sharpe + max_drawdown + 趋势一致性
        - 4h:  关注 大方向正确率 + 回撤恢复时间
        - 将频率维度写入 memory.performance_by_freq
        """
        db = SessionLocal()
        try:
            strategy = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == strategy_id
            ).first()
            if not strategy:
                return {"error": "策略不存在"}

            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            trades = _exclude_legacy_dirty(db.query(StrategyTrade).filter(
                StrategyTrade.strategy_id == strategy_id,
                StrategyTrade.opened_at >= cutoff,
            )).order_by(StrategyTrade.opened_at.desc()).all()

            if not trades:
                logger.info(f"[Learning] {strategy_id} freq={freq} 近 {days} 天无交易")
                return {"strategy_id": strategy_id, "freq": freq, "trades": 0, "reviewed": False}

            trade_analysis = self._analyze_trades(trades)

            # 频率差异化评价
            freq_metrics = {
                "freq": freq,
                "trades": trade_analysis["total"],
                "win_rate": trade_analysis["win_rate"],
                "avg_win": trade_analysis["avg_win"],
                "avg_loss": trade_analysis["avg_loss"],
                "total_pnl": trade_analysis["total_pnl"],
            }

            if freq == "15m":
                # 短线: 高频 + 胜率优先
                trade_freq_score = min(1.0, trade_analysis["total"] / max(days, 1) / 3)  # 日均>=3笔满分
                wr_score = trade_analysis["win_rate"]
                rr_score = min(1.0, abs(trade_analysis["avg_win"]) / max(abs(trade_analysis["avg_loss"]), 0.001))
                freq_metrics["score"] = round(0.4 * wr_score + 0.35 * rr_score + 0.25 * trade_freq_score, 4)
                freq_metrics["grade"] = "A" if freq_metrics["score"] >= 0.7 else ("B" if freq_metrics["score"] >= 0.5 else "C")

            elif freq == "1h":
                # 波段: Sharpe + 回撤控制
                sharpe = self._compute_sharpe_for_trades(trades)
                max_dd = self._compute_max_drawdown_for_trades(trades)
                sharpe_score = min(1.0, max(0, sharpe) / 1.5)
                dd_score = max(0, 1.0 - max_dd / 0.15)
                freq_metrics["sharpe"] = round(sharpe, 4)
                freq_metrics["max_drawdown"] = round(max_dd, 4)
                freq_metrics["score"] = round(0.5 * sharpe_score + 0.3 * dd_score + 0.2 * trade_analysis["win_rate"], 4)
                freq_metrics["grade"] = "A" if freq_metrics["score"] >= 0.7 else ("B" if freq_metrics["score"] >= 0.5 else "C")

            elif freq == "4h":
                # 趋势: 大方向正确率 + 回撤恢复
                direction_correct = sum(1 for t in trades if (t.pnl or 0) > 0) / max(len(trades), 1)
                max_dd = self._compute_max_drawdown_for_trades(trades)
                dd_score = max(0, 1.0 - max_dd / 0.20)
                freq_metrics["direction_accuracy"] = round(direction_correct, 4)
                freq_metrics["max_drawdown"] = round(max_dd, 4)
                freq_metrics["score"] = round(0.5 * direction_correct + 0.3 * dd_score + 0.2 * min(1.0, trade_analysis["total_pnl"] / 0.05), 4)
                freq_metrics["grade"] = "A" if freq_metrics["score"] >= 0.65 else ("B" if freq_metrics["score"] >= 0.45 else "C")

            else:
                freq_metrics["score"] = trade_analysis["win_rate"]
                freq_metrics["grade"] = "B"

            # 写入 memory.performance_by_freq
            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()
            if mem:
                perf_by_freq = mem.performance_by_freq or {}
                if isinstance(perf_by_freq, str):
                    import json as _j
                    try:
                        perf_by_freq = _j.loads(perf_by_freq)
                    except Exception:
                        perf_by_freq = {}
                perf_by_freq[freq] = freq_metrics
                mem.performance_by_freq = perf_by_freq
                db.commit()

            logger.info(
                f"[Learning] {strategy_id} freq={freq} 复评: "
                f"score={freq_metrics['score']:.3f} grade={freq_metrics['grade']}"
            )
            return {"strategy_id": strategy_id, "reviewed": True, **freq_metrics}

        except Exception as e:
            logger.error(f"[Learning] 多频率复评异常 {strategy_id} freq={freq}: {e}")
            return {"error": str(e)}
        finally:
            db.close()

    # ══════════════════════════════════════════════════════
    #  P1: 概念漂移检测 (M-8)
    # ══════════════════════════════════════════════════════

    def _detect_concept_drift(
        self,
        db: Session,
        strategy_id: str,
        recent_trades: List[StrategyTrade],
        historical_trades: Optional[List[StrategyTrade]] = None,
    ) -> Dict[str, Any]:
        """
        概念漂移检测: KS检验 + MMD (Maximum Mean Discrepancy)。

        检测内容:
        1. 收益分布漂移 (KS test on pnl_pct)
        2. 胜率漂移 (recent vs historical win_rate 对比)
        3. 持仓时长漂移 (hold duration distribution shift)

        Returns:
            {
                "drift_detected": bool,
                "drift_severity": "none"|"low"|"medium"|"high",
                "ks_statistic": float,
                "ks_pvalue": float,
                "win_rate_delta": float,
                "recommended_action": str,
            }
        """
        result = {
            "drift_detected": False,
            "drift_severity": "none",
            "ks_statistic": 0.0,
            "ks_pvalue": 1.0,
            "win_rate_delta": 0.0,
            "hold_duration_delta": 0.0,
            "recommended_action": "none",
            "details": {},
        }

        try:
            import numpy as np
            from scipy import stats as scipy_stats

            if not recent_trades or len(recent_trades) < 10:
                result["details"]["reason"] = "recent_trades < 10"
                return result

            # 获取历史交易作为基线 (默认最近100笔中的前50笔)
            if historical_trades is None:
                all_trades = _exclude_legacy_dirty(db.query(StrategyTrade).filter(
                    StrategyTrade.strategy_id == strategy_id,
                    StrategyTrade.pnl_pct.isnot(None),
                )).order_by(StrategyTrade.opened_at.desc()).limit(100).all()
                mid = len(all_trades) // 2
                historical_trades = all_trades[mid:] if len(all_trades) >= 20 else all_trades

            if not historical_trades or len(historical_trades) < 10:
                result["details"]["reason"] = "historical_trades < 10"
                return result

            # --- 1. KS检验: 收益分布漂移 ---
            recent_pnls = [t.pnl_pct for t in recent_trades if t.pnl_pct is not None]
            hist_pnls = [t.pnl_pct for t in historical_trades if t.pnl_pct is not None]

            if len(recent_pnls) >= 10 and len(hist_pnls) >= 10:
                ks_stat, ks_pval = scipy_stats.ks_2samp(recent_pnls, hist_pnls)
                result["ks_statistic"] = round(float(ks_stat), 4)
                result["ks_pvalue"] = round(float(ks_pval), 4)

            # --- 2. 胜率漂移 ---
            recent_wr = sum(1 for p in recent_pnls if p > 0) / max(len(recent_pnls), 1)
            hist_wr = sum(1 for p in hist_pnls if p > 0) / max(len(hist_pnls), 1)
            result["win_rate_delta"] = round(recent_wr - hist_wr, 4)

            # --- 3. 持仓时长漂移 ---
            recent_holds = []
            hist_holds = []
            for t in recent_trades:
                if t.opened_at and t.closed_at:
                    recent_holds.append((t.closed_at - t.opened_at).total_seconds() / 3600)
            for t in historical_trades:
                if t.opened_at and t.closed_at:
                    hist_holds.append((t.closed_at - t.opened_at).total_seconds() / 3600)

            if len(recent_holds) >= 5 and len(hist_holds) >= 5:
                recent_avg_hold = np.mean(recent_holds)
                hist_avg_hold = np.mean(hist_holds)
                result["hold_duration_delta"] = round(
                    (recent_avg_hold - hist_avg_hold) / max(hist_avg_hold, 0.01), 4)

            # --- 判定漂移严重程度 ---
            drift_signals = 0
            if result["ks_pvalue"] < 0.05 and result["ks_statistic"] > 0.3:
                drift_signals += 2  # KS显著 → 分布确实变了
            elif result["ks_pvalue"] < 0.10:
                drift_signals += 1  # KS弱显著

            wr_drop = -result["win_rate_delta"]
            if wr_drop > 0.15:
                drift_signals += 2  # 胜率暴跌
            elif wr_drop > 0.08:
                drift_signals += 1  # 胜率下降

            hold_change = abs(result["hold_duration_delta"])
            if hold_change > 0.5:
                drift_signals += 1  # 持仓时长变化大

            if drift_signals >= 4:
                result["drift_detected"] = True
                result["drift_severity"] = "high"
                result["recommended_action"] = "retrain_or_pause"
            elif drift_signals >= 2:
                result["drift_detected"] = True
                result["drift_severity"] = "medium"
                result["recommended_action"] = "incremental_retrain"
            elif drift_signals >= 1:
                result["drift_detected"] = True
                result["drift_severity"] = "low"
                result["recommended_action"] = "monitor_closely"

            # 存入 StrategyMemory 供后续查询
            try:
                mem = db.query(StrategyMemory).filter(
                    StrategyMemory.strategy_id == strategy_id
                ).first()
                if mem:
                    drift_history = mem.drift_history or []
                    if isinstance(drift_history, str):
                        import json as _j
                        try:
                            drift_history = _j.loads(drift_history)
                        except Exception:
                            drift_history = []
                    drift_history.append({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "severity": result["drift_severity"],
                        "ks_statistic": result["ks_statistic"],
                        "ks_pvalue": result["ks_pvalue"],
                        "win_rate_delta": result["win_rate_delta"],
                        "action": result["recommended_action"],
                    })
                    mem.drift_history = drift_history[-20:]
                    db.commit()
            except Exception:
                pass

            if result["drift_detected"]:
                logger.warning(
                    f"[Learning] {strategy_id} 概念漂移检测: "
                    f"severity={result['drift_severity']} "
                    f"KS={result['ks_statistic']:.3f} p={result['ks_pvalue']:.3f} "
                    f"WR_delta={result['win_rate_delta']:.1%} "
                    f"action={result['recommended_action']}"
                )

            return result

        except ImportError:
            logger.warning("[Learning] scipy 不可用，概念漂移检测降级为纯胜率对比")
            # 降级方案: 仅胜率对比
            recent_pnls = [t.pnl_pct for t in recent_trades if t.pnl_pct is not None]
            recent_wr = sum(1 for p in recent_pnls if p > 0) / max(len(recent_pnls), 1)
            if historical_trades:
                hist_pnls = [t.pnl_pct for t in historical_trades if t.pnl_pct is not None]
                hist_wr = sum(1 for p in hist_pnls if p > 0) / max(len(hist_pnls), 1)
                wr_drop = hist_wr - recent_wr
                if wr_drop > 0.15:
                    result["drift_detected"] = True
                    result["drift_severity"] = "high"
                    result["recommended_action"] = "retrain_or_pause"
                elif wr_drop > 0.08:
                    result["drift_detected"] = True
                    result["drift_severity"] = "medium"
                    result["recommended_action"] = "incremental_retrain"
            return result
        except Exception as e:
            logger.error(f"[Learning] 漂移检测异常: {e}")
            return result

    @staticmethod
    def _compute_sharpe_for_trades(trades: List[StrategyTrade]) -> float:
        """为交易列表计算非年化 Sharpe（标准差归一化收益）"""
        import statistics
        pnls = [t.pnl_pct for t in trades if t.pnl_pct is not None]
        if len(pnls) < 3:
            return 0.0
        mean_pnl = statistics.mean(pnls)
        std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0.001
        return mean_pnl / max(std_pnl, 0.0001)

    @staticmethod
    def _compute_max_drawdown_for_trades(trades: List[StrategyTrade]) -> float:
        """为交易列表计算最大回撤（基于累积PnL）"""
        pnls = [t.pnl_pct or 0 for t in trades]
        if not pnls:
            return 0.0
        cumsum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cumsum += p
            peak = max(peak, cumsum)
            dd = peak - cumsum
            max_dd = max(max_dd, dd)
        return round(max_dd, 4)

    def _update_factor_trust_scores(self, db, strategy_id: str, trades: list) -> None:
        """
        P0增强: 多维度因子信任评分 (L-1 逐笔学习强化版)

        分析每笔交易中因子信号与AI决策的一致性，更新因子信任分数。
        新增维度:
        - 时间衰减: 越近的交易权重越高 (EMA with recency bias)
        - 市场体制对齐: 因子在特定市场状态下表现不同
        - 因子-AI一致性: 原始逻辑保留下
        - 高阶K线特征验证: 量价关系是否支持因子方向

        - 如果因子-AI一致且盈利: trust += 0.02 (EMA)
        - 如果因子-AI矛盾且AI正确: trust -= 0.05
        - 如果有高阶特征支持: trust += 0.01 bonus
        - 存储到 StrategyMemory 的 factor_trust_scores 字段
        """
        try:
            import math
            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()
            if not mem:
                return

            current_scores = {}
            if isinstance(mem.factor_trust_scores, dict):
                current_scores = dict(mem.factor_trust_scores)

            now_ts = datetime.now(timezone.utc).timestamp()

            for idx, t in enumerate(trades):
                if not t.pnl:
                    continue
                ctx = t.decision_context if isinstance(t.decision_context, dict) else {}
                if isinstance(ctx, str):
                    try:
                        import json as _j
                        ctx = _j.loads(ctx)
                    except Exception:
                        ctx = {}
                factor_dir = ctx.get("factor_direction", "")
                ai_dir = t.side or ""

                # 时间衰减权重: 越近的交易权重越高
                trade_ts = t.closed_at.timestamp() if hasattr(t, 'closed_at') and t.closed_at else now_ts
                recency = math.exp(-(now_ts - trade_ts) / (7 * 86400))  # 7天半衰期
                recency = max(0.1, min(1.0, recency))

                # 判断因子-AI是否一致
                aligned = (factor_dir == "long" and ai_dir == "buy") or \
                          (factor_dir == "short" and ai_dir == "sell")

                # 交易结果
                profitable = t.pnl > 0

                # 高阶K线特征验证
                body_ratio = ctx.get("body_ratio", 0.5)
                volume_climax = ctx.get("volume_climax", 1.0)
                trend_efficiency = ctx.get("trend_efficiency", 0.5)

                # 特征支持判断: 如果因子看多且实体占比高+放量+趋势效率高，则特征支持
                feature_support = False
                if factor_dir == "long" and body_ratio > 0.5 and volume_climax > 1.2 and trend_efficiency > 0.5:
                    feature_support = True
                elif factor_dir == "short" and body_ratio > 0.5 and volume_climax > 1.2 and trend_efficiency > 0.5:
                    feature_support = True

                # 市场体制
                market_regime = ctx.get("market_environment", {}).get("cycle", "unknown") if isinstance(
                    ctx.get("market_environment"), dict) else "unknown"

                # 使用 EMA 更新信任分数 (引入 recency 和时间衰减)
                for factor_name in ["factor_v3", factor_dir] if factor_dir else ["factor_v3"]:
                    if not factor_name or factor_name == "neutral":
                        continue

                    # 每个体制独立维护一个分数
                    regime_key = f"{factor_name}_{market_regime}" if market_regime != "unknown" else factor_name
                    old = current_scores.get(regime_key, 0.5)

                    # 基础更新
                    if aligned and profitable:
                        delta = 0.02 * (1.0 - old) * recency
                        current_scores[regime_key] = old + delta
                    elif not aligned and profitable:
                        delta = -0.05 * recency
                        current_scores[regime_key] = max(0.1, old + delta)
                    elif aligned and not profitable:
                        delta = -0.01 * recency
                        current_scores[regime_key] = max(0.1, old + delta)
                    else:
                        # AI不听因子且亏损: 因子可能是对的
                        delta = -0.03 * recency
                        current_scores[regime_key] = max(0.1, old + delta)

                    # 高阶特征bonus
                    if feature_support and profitable:
                        current_scores[regime_key] = min(0.95, current_scores.get(regime_key, 0.5) + 0.01 * recency)

                # 同时维护全局分数 (非regime特异)
                for factor_name in ["factor_v3", factor_dir] if factor_dir else ["factor_v3"]:
                    if not factor_name or factor_name == "neutral":
                        continue
                    old_global = current_scores.get(factor_name, 0.5)
                    if aligned and profitable:
                        current_scores[factor_name] = old_global + 0.02 * (1.0 - old_global) * recency
                    elif not aligned and profitable:
                        current_scores[factor_name] = max(0.1, old_global - 0.05 * recency)
                    elif aligned and not profitable:
                        current_scores[factor_name] = max(0.1, old_global - 0.01 * recency)

            mem.factor_trust_scores = current_scores
            db.commit()
            logger.debug(
                f"[Learning] {strategy_id} 因子信任更新(L-1增强): "
                f"{len(current_scores)} 个因子维度, {len(trades)} 笔交易"
            )
        except Exception as e:
            logger.debug(f"[Learning] 因子信任更新失败: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    # === L-3 跨周期战略学习 (M-16) ===

    def run_strategic_review(
        self,
        db: Optional[Session] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """L-3 每周战略回顾: 跨频率对齐验证 + 硬约束有效性审计 + 策略晋升/降级/冻结

        触发条件:
        - force=True 强制运行
        - or 距上次运行 >= 7天

        Returns:
            {
                "strategies_reviewed": int,
                "promotions": [strategy_id, ...],
                "demotions": [strategy_id, ...],
                "frozen": [strategy_id, ...],
                "constraint_audit": {...},
                "alignment_report": {...},
                "summary": str,
            }
        """
        _own_db = db is None
        if _own_db:
            db = SessionLocal()
        try:
            result = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "strategies_reviewed": 0,
                "promotions": [],
                "demotions": [],
                "frozen": [],
                "constraint_audit": {},
                "alignment_report": {},
                "summary": "",
            }

            # 获取所有非退役策略
            strategies = db.query(AIStrategy).filter(
                AIStrategy.status.in_([
                    "active", "graduated", "golden",
                ])
            ).all()

            if not strategies:
                result["summary"] = "无活跃策略"
                return result

            result["strategies_reviewed"] = len(strategies)

            # ── 1. 策略晋升/降级/冻结评估 ──
            now = datetime.now(timezone.utc)
            for strategy in strategies:
                sid = strategy.strategy_id
                status = strategy.status or "active"
                total_trades = strategy.total_trades or 0
                win_rate = strategy.win_rate or 0
                sharpe = strategy.sharpe_ratio or 0
                max_dd = strategy.max_drawdown or 0
                last_trade_ts = strategy.last_trade_at

                # 晋升规则 (按设计文档 §3.3 策略晋升管道)
                should_promote = False
                should_demote = False
                should_freeze = False

                # → golden: total>=50, wr>=0.55, sharpe>=0.8, dd<=0.10
                if status == "graduated" and total_trades >= 50 and win_rate >= 0.55 and sharpe >= 0.8 and max_dd <= 0.10:
                    should_promote = True
                    strategy.status = "golden"
                # → graduated: total>=15, wr>=0.50, (sharpe>=0.5 or (wr>=0.55 and dd<=0.15))
                elif status == "active" and total_trades >= 15 and win_rate >= 0.50:
                    if sharpe >= 0.5 or (win_rate >= 0.55 and max_dd <= 0.15):
                        should_promote = True
                        strategy.status = "graduated"

                # 降级规则: 连续亏损或长时间无交易
                if total_trades >= 20 and win_rate < 0.30:
                    should_demote = True
                    if status == "golden":
                        strategy.status = "graduated"
                    elif status == "graduated":
                        strategy.status = "active"
                    else:
                        should_freeze = True
                        strategy.status = "frozen"

                # 冻结规则: 60天无交易
                if last_trade_ts:
                    days_since = (now - last_trade_ts).days if isinstance(last_trade_ts, datetime) else 0
                    if days_since > 60 and status != "frozen":
                        should_freeze = True
                        strategy.status = "frozen"

                if should_promote:
                    result["promotions"].append(sid)
                    logger.info(
                        f"[L-3] {sid}: {status} → {strategy.status} "
                        f"(trades={total_trades}, wr={win_rate:.1%}, sharpe={sharpe:.2f})"
                    )
                elif should_demote:
                    result["demotions"].append(sid)
                    logger.info(f"[L-3] {sid}: 降级至 {strategy.status} (wr={win_rate:.1%})")
                elif should_freeze:
                    result["frozen"].append(sid)
                    logger.info(f"[L-3] {sid}: 冻结 (wr={win_rate:.1%}, 原因={'长期无交易' if last_trade_ts else '连续亏损'})")

            # ── 2. 硬约束有效性审计 ──
            result["constraint_audit"] = self._audit_constraint_effectiveness(db)

            # ── 3. 跨频率对齐报告 ──
            result["alignment_report"] = self._audit_cross_freq_alignment(db)

            # 保存结果到策略内存
            db.commit()

            # ── 4. 生成总结 ──
            parts = []
            if result["promotions"]:
                parts.append(f"晋升{len(result['promotions'])}个策略")
            if result["demotions"]:
                parts.append(f"降级{len(result['demotions'])}个策略")
            if result["frozen"]:
                parts.append(f"冻结{len(result['frozen'])}个策略")
            result["summary"] = ", ".join(parts) if parts else "策略状态无变化"

            logger.info(
                f"[L-3] 战略回顾完成: 检查{len(strategies)}策略, "
                f"{result['summary']}"
            )

            return result

        except Exception as e:
            logger.error(f"[L-3] 战略回顾失败: {e}", exc_info=True)
            if _own_db:
                try:
                    db.rollback()
                except Exception:
                    pass
            return {"error": str(e)}
        finally:
            if _own_db:
                db.close()

    def _audit_constraint_effectiveness(self, db: Session) -> Dict[str, Any]:
        """审计多频率硬约束的有效性"""
        audit = {
            "total_constraints_enforced": 0,
            "violations_prevented": 0,
            "recommendation": "constraints_healthy",
        }
        try:
            # 查询近期被约束拦截后手动开仓的情况
            from sqlalchemy import Text, cast
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            constraint_logs = db.query(AIDecisionLog).filter(
                AIDecisionLog.created_at >= cutoff,
                cast(AIDecisionLog.decision_context, Text).like('%constraint_violated%'),
            ).all()
            audit["total_constraints_enforced"] = len(constraint_logs)

            # 简单启发式: 约束拦截超过50次/月 = 正常运作
            if len(constraint_logs) > 50:
                audit["recommendation"] = "constraints_active_effective"
            elif len(constraint_logs) < 5:
                audit["recommendation"] = "constraints_underutilized—review_thresholds"
        except Exception as e:
            audit["error"] = str(e)
        return audit

    def _audit_cross_freq_alignment(self, db: Session) -> Dict[str, Any]:
        """审计跨频率对齐质量"""
        alignment = {
            "aligned_decisions_pct": 0.0,
            "conflicting_decisions_pct": 0.0,
            "recommendation": "insufficient_data",
        }
        try:
            from sqlalchemy import Text, cast
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            decisions = db.query(AIDecisionLog).filter(
                AIDecisionLog.created_at >= cutoff,
                AIDecisionLog.decision_context.isnot(None),
            ).all()

            if not decisions or len(decisions) < 10:
                return alignment

            aligned = 0
            conflicting = 0
            for d in decisions:
                ctx = d.decision_context or {}
                if isinstance(ctx, str):
                    try:
                        import json as _j
                        ctx = _j.loads(ctx)
                    except Exception:
                        continue
                mf_align = ctx.get("multi_freq_alignment", "unknown") if isinstance(ctx, dict) else "unknown"
                if mf_align == "aligned":
                    aligned += 1
                elif mf_align == "conflicting":
                    conflicting += 1

            total = aligned + conflicting if (aligned + conflicting) > 0 else len(decisions)
            alignment["aligned_decisions_pct"] = round(aligned / max(total, 1), 3)
            alignment["conflicting_decisions_pct"] = round(conflicting / max(total, 1), 3)

            if alignment["aligned_decisions_pct"] > 0.6:
                alignment["recommendation"] = "good_alignment"
            elif alignment["conflicting_decisions_pct"] > 0.3:
                alignment["recommendation"] = "high_conflict—review_orchestrator_params"
            else:
                alignment["recommendation"] = "moderate_alignment"

        except Exception as e:
            alignment["error"] = str(e)
        return alignment


strategy_learning = StrategyLearningService()
