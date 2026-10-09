"""
信号反馈追踪器 - Signal Feedback Tracker
==========================================

论文依据: RLMF (U of Toronto) - 用市场自然反馈优化信号权重
核心思路: 信号活跃时的交易表现 vs 信号不活跃时的表现 = 信号增量价值

职责:
1. 开仓时记录当时活跃的信号快照 -> signal_trade_feedback 表
2. 每日计算每种信号的增量贡献度 -> 归一化为新权重
3. 平滑更新 intelligence_signal_engine 的 COMPONENT_WEIGHTS
4. 存储权重变更历史 -> signal_weight_history 表
5. [V3整合] 因子信号快照记录 + 因子贡献度分析 -> 反馈给 DynamicFactorWeighting
"""

import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session
from sqlalchemy import func, desc

logger = logging.getLogger(__name__)

# 平滑系数: new_weight = SMOOTH * old + (1-SMOOTH) * computed
SMOOTHING_FACTOR = 0.7
MIN_TRADES_FOR_UPDATE = 20
LOOKBACK_DAYS = 30

# 默认权重（与 intelligence_signal_engine.COMPONENT_WEIGHTS 保持同步）
DEFAULT_WEIGHTS = {
    "funding": 0.22,
    "oi": 0.22,
    "liquidation": 0.14,
    "whale": 0.10,
    "news": 0.08,
    "fear_greed": 0.06,
    "long_short": 0.10,
    "top_trader": 0.08,
}


class SignalFeedbackTracker:
    """信号 → 交易结果反馈追踪器"""

    def record_entry_signals(
        self,
        db: Session,
        account_id: int,
        trade_id: Optional[int],
        symbol: str,
        side: str,
        active_signals: Dict[str, Any],
        factor_values: Optional[Dict[str, float]] = None,
    ):
        """开仓时记录当时活跃的信号快照。

        Args:
            active_signals: {
                "funding": {"direction": "bearish", "value": 0.0008},
                "oi": {"direction": "bullish", "value": 0.03},
                ...
            }
            factor_values: [V3整合] 因子引擎计算结果 {
                "rsi_14": 0.65, "macd": -0.02, ...
            }  以 factor: 前缀存入 signal_type 字段
        """
        from backend.database.models import SignalTradeFeedback

        try:
            records = []
            for sig_type, sig_data in active_signals.items():
                if isinstance(sig_data, dict):
                    direction = sig_data.get("direction", "neutral")
                    value = float(sig_data.get("value", 0) or 0)
                else:
                    direction = str(sig_data) if sig_data else "neutral"
                    value = 0.0

                records.append(SignalTradeFeedback(
                    account_id=account_id,
                    trade_id=trade_id,
                    symbol=symbol,
                    signal_type=sig_type,
                    signal_value=value,
                    signal_direction=direction,
                    trade_side=side,
                ))

            # V3整合: 记录因子快照 (factor: 前缀区分)
            if factor_values:
                for factor_name, factor_val in factor_values.items():
                    try:
                        val = float(factor_val) if factor_val is not None else 0.0
                    except (TypeError, ValueError):
                        val = 0.0
                    direction = "bullish" if val > 0 else ("bearish" if val < 0 else "neutral")
                    # [fix 2026-08-29] 迁移 0008 已把 signal_type 扩到 VARCHAR(100)，
                    # 旧的 28 字符截断成了反向 bug：92 个 AI 因子中 52 个名字被截断
                    # (如 factor:ai_gen_liquidity_squeeze=31字符)，factor_ic_evaluator
                    # 按截断名写 data/factor_runtime_weights.json，而
                    # factor_evaluation_pipeline._compute_weights 按全名查
                    # runtime_weights.get(name) 永远 miss → 这些因子的学习权重
                    # 静默失效。现按列宽 100 截断（仅防御超长名，正常因子名不再截断）。
                    _stype = f"factor:{factor_name}"
                    if len(_stype) > 100:
                        _stype = _stype[:100]
                    records.append(SignalTradeFeedback(
                        account_id=account_id,
                        trade_id=trade_id,
                        symbol=symbol,
                        signal_type=_stype,
                        signal_value=val,
                        signal_direction=direction,
                        trade_side=side,
                    ))

            if records:
                db.bulk_save_objects(records)
                db.commit()
                logger.debug(
                    f"[SignalFeedback] 记录 {len(records)} 条信号快照: "
                    f"{symbol} {side} trade_id={trade_id}")
        except Exception as e:
            logger.warning(f"[SignalFeedback] 信号快照记录失败: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    def update_trade_pnl(
        self, db: Session, trade_id: int, pnl: float, pnl_pct: float
    ):
        """平仓后更新对应信号记录的交易结果。"""
        from backend.database.models import SignalTradeFeedback

        # [fix] P0-3: 先 rollback 清除可能的 InFailedSqlTransaction 状态
        # 上游代码若在同一个 session 上执行了失败的 SQL 而未 rollback，
        # 会导致此处的 query().update() 直接抛 InFailedSqlTransaction。
        try:
            db.rollback()
        except Exception:
            pass

        try:
            db.query(SignalTradeFeedback).filter(
                SignalTradeFeedback.trade_id == trade_id
            ).update({
                SignalTradeFeedback.trade_pnl: pnl,
                SignalTradeFeedback.trade_pnl_pct: pnl_pct,
            })
            db.commit()
        except Exception as e:
            logger.warning(f"[SignalFeedback] PnL更新失败 trade_id={trade_id}: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    def compute_signal_performance(
        self, db: Session, lookback_days: int = LOOKBACK_DAYS
    ) -> Optional[Dict[str, float]]:
        """计算每种信号的增量贡献度。

        方法: 对比"信号活跃时交易的平均PnL" vs "全局平均PnL"
        差值 > 0 说明该信号有正向贡献。
        """
        from backend.database.models import SignalTradeFeedback

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        try:
            # 只看有 PnL 结果的记录
            all_records = db.query(SignalTradeFeedback).filter(
                SignalTradeFeedback.created_at >= cutoff,
                SignalTradeFeedback.trade_pnl.isnot(None),
            ).all()

            if len(all_records) < MIN_TRADES_FOR_UPDATE:
                logger.info(
                    f"[SignalFeedback] 样本不足 ({len(all_records)}/{MIN_TRADES_FOR_UPDATE})，跳过权重更新")
                return None

            # [F329 2026-09-18 复查修复] 同 F327/F328：`trade_pnl_pct or 0` 把 NULL 当 PnL=0。
            # 本函数是**信号权重更新**的入口（增量价值 → 权重层），口径污染的后果比前两处更直接。
            _valid = [r for r in all_records if r.trade_pnl_pct is not None]
            if len(_valid) < MIN_TRADES_FOR_UPDATE:
                logger.info(
                    f"[SignalFeedback] 有效 pct 样本不足 ({len(_valid)}/{MIN_TRADES_FOR_UPDATE})，"
                    f"跳过权重更新（已排除 NULL {len(all_records) - len(_valid)} 行）")
                return None
            if len(_valid) != len(all_records):
                logger.info("[SignalFeedback] 权重更新排除 trade_pnl_pct 为空的行: %d/%d",
                            len(all_records) - len(_valid), len(all_records))

            # 全局平均 PnL（仅有效样本）
            global_avg_pnl = sum(float(r.trade_pnl_pct) for r in _valid) / len(_valid)

            # 按信号类型分组（仅有效样本）
            sig_groups: Dict[str, List[float]] = {}
            for r in _valid:
                sig_groups.setdefault(r.signal_type, []).append(float(r.trade_pnl_pct))

            # 计算每个信号的增量价值
            incremental_values: Dict[str, float] = {}
            for sig_type, pnl_list in sig_groups.items():
                if len(pnl_list) < 5:
                    continue
                sig_avg = sum(pnl_list) / len(pnl_list)
                incremental_values[sig_type] = sig_avg - global_avg_pnl

            if not incremental_values:
                return None

            # 将增量值转换为权重（softmax 归一化，确保非负且和为1）
            import math
            # 先平移到正数域
            min_val = min(incremental_values.values())
            shifted = {k: v - min_val + 0.01 for k, v in incremental_values.items()}

            total = sum(shifted.values())
            if total <= 0:
                return None

            new_weights = {k: v / total for k, v in shifted.items()}

            logger.info(
                f"[SignalFeedback] 计算完成: {len(all_records)}条样本, "
                f"全局avgPnL={global_avg_pnl:+.3%}, "
                f"增量值={json.dumps({k: f'{v:+.4f}' for k, v in incremental_values.items()})}")

            return new_weights

        except Exception as e:
            logger.error(f"[SignalFeedback] 信号表现计算失败: {e}")
            return None

    # ------------------------------------------------------------------
    # V3 整合: 因子贡献度分析
    # ------------------------------------------------------------------

    def analyze_factor_contribution(
        self,
        db: Session,
        strategy_id: Optional[str] = None,
        lookback_days: int = LOOKBACK_DAYS,
    ) -> Dict[str, float]:
        """V3 整合: 分析每个因子对交易结果的增量贡献度。

        方法: 对比"因子活跃时(值>0或<0)交易的平均PnL" vs "全局平均PnL"
        返回: { factor_name: contribution_score }  (正=正向贡献, 负=负向贡献)
        """
        from backend.database.models import SignalTradeFeedback

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        try:
            # 只查 factor: 前缀的记录
            query = db.query(SignalTradeFeedback).filter(
                SignalTradeFeedback.created_at >= cutoff,
                SignalTradeFeedback.trade_pnl.isnot(None),
                SignalTradeFeedback.signal_type.like("factor:%"),
            )

            all_records = query.all()

            # [F327 2026-09-18 复查修复] 原实现两处用 `r.trade_pnl_pct or 0` ⇒ 把 **NULL 当 PnL=0**
            # 计入均值（复查实测该表 6.2% / 38,374 行 trade_pnl_pct 为 NULL）⇒ 全局均值与各因子均值
            # 同时被系统性拉向 0，而"贡献 = 因子均值 − 全局均值"正是建立在这两个均值上 ⇒ 口径受污染。
            # 修法：只用**有 pct 值**的记录参与统计，并把被排除的行数显式写进日志（不再静默）。
            _valid = [r for r in all_records if r.trade_pnl_pct is not None]
            _skipped = len(all_records) - len(_valid)
            if len(_valid) < MIN_TRADES_FOR_UPDATE // 2:  # 因子样本放宽到10
                logger.info(
                    f"[FactorFeedback] 有效 pct 样本不足 ({len(_valid)}/{MIN_TRADES_FOR_UPDATE // 2})，"
                    f"跳过贡献度分析（已排除 NULL {_skipped} 行）")
                return {}
            if _skipped:
                logger.info("[FactorFeedback] 贡献度统计排除 trade_pnl_pct 为空的行: %d/%d",
                            _skipped, len(all_records))

            # 全局平均 PnL（仅有效样本）
            global_avg_pnl = sum(float(r.trade_pnl_pct) for r in _valid) / len(_valid)

            # 按 factor 分组（仅有效样本）
            factor_groups: Dict[str, List[float]] = {}
            for r in _valid:
                factor_name = r.signal_type.replace("factor:", "", 1)
                factor_groups.setdefault(factor_name, []).append(float(r.trade_pnl_pct))

            # 计算每个因子的增量贡献
            contributions: Dict[str, float] = {}
            for factor_name, pnl_list in factor_groups.items():
                if len(pnl_list) < 3:
                    continue
                factor_avg = sum(pnl_list) / len(pnl_list)
                contributions[factor_name] = round(factor_avg - global_avg_pnl, 6)

            if contributions:
                logger.info(
                    f"[FactorFeedback] 因子贡献度分析完成: {len(all_records)}条样本, "
                    f"全局avgPnL={global_avg_pnl:+.4%}, "
                    f"top-3={sorted(contributions.items(), key=lambda x: x[1], reverse=True)[:3]}")

            return contributions

        except Exception as e:
            logger.error(f"[FactorFeedback] 因子贡献度分析失败: {e}")
            return {}

    def analyze_all_signal_contribution(
        self,
        db: Session,
        strategy_id: Optional[str] = None,
        lookback_days: int = LOOKBACK_DAYS,
    ) -> Dict[str, float]:
        """[统一策略 2026-09-18] 全信号类型增量归因（不限 factor:%）。

        analyze_factor_contribution 只看 factor: 前缀——swing_agent_score /
        trend_agent_score（LLM 分析师分数）、whale、fear_greed 等被排除在战绩
        体系外。本方法同口径计算全类型增量（因子活跃期 avgPnL − 全局 avgPnL），
        供主控战绩块与周外循环评估 LLM 分析师的真实战绩。
        """
        from datetime import datetime, timedelta, timezone

        from backend.database.models import SignalTradeFeedback

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        try:
            records = (
                db.query(SignalTradeFeedback)
                .filter(
                    SignalTradeFeedback.created_at >= cutoff,
                    SignalTradeFeedback.trade_pnl.isnot(None),
                )
                .all()
            )
            if len(records) < 10:
                return {}
            # [F328 2026-09-18 复查修复] 同 F327：`trade_pnl_pct or 0` 把 NULL 当 PnL=0 计入
            # 均值。本函数是**主控 prompt"LLM 分析师/信号源战绩"块的数据源**
            # （trading_analysts.py → analyze_all_signal_contribution），
            # 该块显示的 swing/trend/whale 等数值因此被系统性稀释 ⇒ 必须同口径修掉。
            _valid = [r for r in records if r.trade_pnl_pct is not None]
            if len(_valid) < 10:
                logger.info("[FactorFeedback] analyze_all 有效 pct 样本不足 (%d/10)，"
                            "排除 NULL %d 行后返回空", len(_valid), len(records) - len(_valid))
                return {}
            if len(_valid) != len(records):
                logger.info("[FactorFeedback] analyze_all 排除 trade_pnl_pct 为空的行: %d/%d",
                            len(records) - len(_valid), len(records))
            global_avg = sum(float(r.trade_pnl_pct) for r in _valid) / len(_valid)
            groups: Dict[str, List[float]] = {}
            for r in _valid:
                groups.setdefault(str(r.signal_type or "?"), []).append(float(r.trade_pnl_pct))
            out: Dict[str, float] = {}
            for name, pnls in groups.items():
                if len(pnls) < 3:
                    continue
                out[name] = round(sum(pnls) / len(pnls) - global_avg, 6)
            return out
        except Exception as e:
            logger.error(f"[SignalFeedback] 全信号归因失败: {e}")
            return {}

    def apply_factor_feedback(
        self,
        db: Session,
        lookback_days: int = LOOKBACK_DAYS,
    ) -> bool:
        """V3 整合: 计算因子贡献度并反馈给 DynamicFactorWeighting。

        完整闭环: 因子贡献度 → apply_feedback_adjustments() → 更新 regime 权重
        """
        try:
            contributions = self.analyze_factor_contribution(db, lookback_days=lookback_days)
            if not contributions:
                return False

            from backend.services.factor_engine.factor_weighting import get_factor_weighting
            weighting = get_factor_weighting()

            adjusted = weighting.apply_feedback_adjustments(contributions)
            if not adjusted:
                logger.info("[FactorFeedback] 权重调整无变化")
                return False

            logger.info(
                f"[FactorFeedback] 因子权重已反馈调整: "
                f"{json.dumps({k: f'{v:.4f}' for k, v in adjusted.items()}, ensure_ascii=False)}")
            return True

        except Exception as e:
            logger.error(f"[FactorFeedback] 因子反馈调整失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 原有权重管理
    # ------------------------------------------------------------------

    def update_weights(self, db: Session) -> bool:
        """每日定时任务: 计算并平滑更新情报引擎权重。"""
        from backend.database.models import SignalWeightHistory

        computed = self.compute_signal_performance(db)
        if not computed:
            return False

        try:
            from backend.services.intelligence_signal_engine import IntelligenceSignalEngine
            engine = IntelligenceSignalEngine()
            old_weights = engine.get_weights()

            # 平滑更新
            smoothed: Dict[str, float] = {}
            for k in old_weights:
                old_v = old_weights.get(k, 0)
                new_v = computed.get(k, old_v)
                smoothed[k] = SMOOTHING_FACTOR * old_v + (1 - SMOOTHING_FACTOR) * new_v

            # 归一化确保总和为1
            total = sum(smoothed.values())
            if total > 0:
                smoothed = {k: v / total for k, v in smoothed.items()}

            engine.load_weights(smoothed)

            # 记录历史
            history = SignalWeightHistory(
                weights_json=smoothed,
                performance_json=computed,
                update_reason=f"自适应更新(smooth={SMOOTHING_FACTOR})",
            )
            db.add(history)
            db.commit()

            logger.info(
                f"[SignalFeedback] 权重已更新: "
                f"{json.dumps({k: f'{v:.3f}' for k, v in smoothed.items()})}")
            return True

        except Exception as e:
            logger.error(f"[SignalFeedback] 权重更新失败: {e}")
            try:
                db.rollback()
            except Exception:
                pass
            return False

    def load_latest_weights(self, db: Session) -> bool:
        """启动时从数据库加载最新的自适应权重。"""
        from backend.database.models import SignalWeightHistory

        try:
            latest = db.query(SignalWeightHistory).order_by(
                desc(SignalWeightHistory.computed_at)
            ).first()

            if not latest or not latest.weights_json:
                logger.info("[SignalFeedback] 无历史权重记录，使用默认权重")
                return False

            weights = latest.weights_json
            if isinstance(weights, str):
                weights = json.loads(weights)

            from backend.services.intelligence_signal_engine import IntelligenceSignalEngine
            engine = IntelligenceSignalEngine()
            engine.load_weights(weights)

            logger.info(
                f"[SignalFeedback] 已加载历史权重 "
                f"(computed_at={latest.computed_at})")
            return True

        except Exception as e:
            logger.debug(f"[SignalFeedback] 加载历史权重失败(非致命): {e}")
            return False


# 全局单例
signal_feedback_tracker = SignalFeedbackTracker()
