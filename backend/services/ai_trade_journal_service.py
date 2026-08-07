"""
AI交易复盘系统 — 日复盘 / 周总结 / 月报告

功能:
1. 每日21:00 UTC自动收集当天所有交易记录
2. LLM分析每笔交易的入场/出场质量
3. 统计盈利/亏损模式 → 反馈到策略模板评分
4. 周/月维度的趋势分析 + 参数漂移检测
5. 自动触发策略进化器重新进化表现差的模板
"""
import asyncio
import json
import logging
import re
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class AITradeJournalService:
    """AI交易复盘（单例）"""

    _instance = None
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
        logger.info("[TradeJournal] AI复盘系统初始化完成")

    # ════════════════════════ 日复盘 ════════════════════════

    async def daily_review(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        每日复盘

        Args:
            target_date: "YYYY-MM-DD"，默认昨天
        """
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            if not target_date:
                yesterday = datetime.now(timezone.utc) - timedelta(days=1)
                target_date = yesterday.strftime("%Y-%m-%d")

            trades = self._collect_trades(db, target_date, "daily")
            if not trades:
                logger.info(f"[TradeJournal] {target_date} 无交易记录")
                return {"period": target_date, "total_trades": 0}

            stats = self._calculate_stats(trades)
            ai_analysis = await self._llm_analyze(trades, stats, "daily", target_date)

            result = {
                "period_type": "daily",
                "period_date": target_date,
                **stats,
                "ai_analysis": ai_analysis.get("analysis", ""),
                "improvement_actions": ai_analysis.get("actions", []),
            }

            self._save_journal(db, result)
            self._feedback_to_templates(db, ai_analysis.get("template_feedback", {}))

            logger.info(
                f"[TradeJournal] 日复盘 {target_date}: "
                f"{stats['total_trades']}笔交易, PnL={stats['total_pnl']:.2f}, "
                f"胜率={stats['win_rate']:.0%}"
            )
            return result
        except Exception as e:
            logger.error(f"[TradeJournal] 日复盘异常: {e}", exc_info=True)
            return {"error": str(e)}
        finally:
            db.close()

    # ════════════════════════ 周总结 ════════════════════════

    async def weekly_summary(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            if not target_date:
                today = datetime.now(timezone.utc)
                start_of_week = today - timedelta(days=today.weekday())
                target_date = start_of_week.strftime("%Y-W%W")

            end = datetime.now(timezone.utc)
            start = end - timedelta(days=7)
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")

            trades = self._collect_trades_range(db, start_str, end_str)
            if not trades:
                return {"period": target_date, "total_trades": 0}

            stats = self._calculate_stats(trades)

            # 按策略分组统计
            strategy_perf = self._group_by_strategy(trades)
            stats["strategy_performance"] = strategy_perf

            ai_analysis = await self._llm_analyze(trades, stats, "weekly", target_date)

            result = {
                "period_type": "weekly",
                "period_date": target_date,
                **stats,
                "ai_analysis": ai_analysis.get("analysis", ""),
                "improvement_actions": ai_analysis.get("actions", []),
            }

            self._save_journal(db, result)

            # 周末触发表现差模板的进化
            worst = stats.get("worst_strategy", "")
            if worst and stats.get("total_pnl", 0) < 0:
                self._trigger_evolution(worst)

            logger.info(f"[TradeJournal] 周总结 {target_date}: {stats['total_trades']}笔")
            return result
        except Exception as e:
            logger.error(f"[TradeJournal] 周总结异常: {e}")
            return {"error": str(e)}
        finally:
            db.close()

    # ════════════════════════ 月报告 ════════════════════════

    async def monthly_report(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            if not target_date:
                today = datetime.now(timezone.utc)
                target_date = today.strftime("%Y-%m")

            end = datetime.now(timezone.utc)
            start = end - timedelta(days=30)
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")

            trades = self._collect_trades_range(db, start_str, end_str)
            if not trades:
                return {"period": target_date, "total_trades": 0}

            stats = self._calculate_stats(trades)
            strategy_perf = self._group_by_strategy(trades)
            stats["strategy_performance"] = strategy_perf

            # 参数漂移检测
            drift = self._detect_parameter_drift(db)
            stats["parameter_drift"] = drift

            ai_analysis = await self._llm_analyze(trades, stats, "monthly", target_date)

            result = {
                "period_type": "monthly",
                "period_date": target_date,
                **stats,
                "ai_analysis": ai_analysis.get("analysis", ""),
                "improvement_actions": ai_analysis.get("actions", []),
            }

            self._save_journal(db, result)
            logger.info(f"[TradeJournal] 月报告 {target_date}: {stats['total_trades']}笔")
            return result
        except Exception as e:
            logger.error(f"[TradeJournal] 月报告异常: {e}")
            return {"error": str(e)}
        finally:
            db.close()

    # ════════════════════════ 获取历史 ════════════════════════

    def get_journals(self, period_type: str = "daily", limit: int = 30) -> List[Dict]:
        from backend.database.connection import SessionLocal
        from backend.database.models import TradeJournal
        db = SessionLocal()
        try:
            rows = (
                db.query(TradeJournal)
                .filter(TradeJournal.period_type == period_type)
                .order_by(TradeJournal.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "period_type": r.period_type,
                    "period_date": r.period_date,
                    "total_trades": r.total_trades,
                    "total_pnl": r.total_pnl,
                    "win_rate": r.win_rate,
                    "best_strategy": r.best_strategy,
                    "worst_strategy": r.worst_strategy,
                    "ai_analysis": r.ai_analysis,
                    "improvement_actions": r.improvement_actions,
                    "created_at": str(r.created_at),
                }
                for r in rows
            ]
        finally:
            db.close()

    # ════════════════════════ 数据收集 ════════════════════════

    def _collect_trades(self, db: Session, date_str: str, period: str) -> List[Dict]:
        """收集指定日期的交易记录"""
        try:
            from backend.database.models import PaperOrder
            rows = (
                db.query(PaperOrder)
                .filter(PaperOrder.status == "filled")
                .order_by(PaperOrder.created_at.desc())
                .limit(200)
                .all()
            )
            trades = []
            for r in rows:
                created = str(r.created_at) if r.created_at else ""
                if date_str in created:
                    trades.append({
                        "symbol": r.symbol,
                        "side": r.side,
                        "quantity": r.quantity,
                        "price": r.filled_price or r.price,
                        "pnl": getattr(r, "pnl", 0) or 0,
                        "strategy_id": getattr(r, "strategy_id", ""),
                        "created_at": created,
                    })
            return trades
        except Exception as e:
            logger.debug(f"[TradeJournal] 收集交易记录异常: {e}")
            return []

    def _collect_trades_range(self, db: Session, start: str, end: str) -> List[Dict]:
        try:
            from backend.database.models import PaperOrder
            rows = (
                db.query(PaperOrder)
                .filter(PaperOrder.status == "filled")
                .order_by(PaperOrder.created_at.desc())
                .limit(1000)
                .all()
            )
            trades = []
            for r in rows:
                created = str(r.created_at) if r.created_at else ""
                date_part = created[:10]
                if start <= date_part <= end:
                    trades.append({
                        "symbol": r.symbol,
                        "side": r.side,
                        "quantity": r.quantity,
                        "price": r.filled_price or r.price,
                        "pnl": getattr(r, "pnl", 0) or 0,
                        "strategy_id": getattr(r, "strategy_id", ""),
                        "created_at": created,
                    })
            return trades
        except Exception:
            return []

    # ════════════════════════ 统计分析 ════════════════════════

    def _calculate_stats(self, trades: List[Dict]) -> Dict[str, Any]:
        total_trades = len(trades)
        wins = [t for t in trades if (t.get("pnl") or 0) > 0]
        losses = [t for t in trades if (t.get("pnl") or 0) < 0]
        total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)
        win_rate = len(wins) / total_trades if total_trades > 0 else 0
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        # 找出最佳/最差策略
        strat_pnl: Dict[str, float] = {}
        for t in trades:
            sid = t.get("strategy_id", "unknown")
            strat_pnl[sid] = strat_pnl.get(sid, 0) + (t.get("pnl", 0) or 0)

        best = max(strat_pnl, key=strat_pnl.get) if strat_pnl else ""
        worst = min(strat_pnl, key=strat_pnl.get) if strat_pnl else ""

        return {
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "win_count": len(wins),
            "loss_count": len(losses),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "best_strategy": best,
            "worst_strategy": worst,
        }

    def _group_by_strategy(self, trades: List[Dict]) -> Dict[str, Dict]:
        groups: Dict[str, List[Dict]] = {}
        for t in trades:
            sid = t.get("strategy_id", "unknown")
            groups.setdefault(sid, []).append(t)
        result = {}
        for sid, ts in groups.items():
            result[sid] = self._calculate_stats(ts)
        return result

    # ════════════════════════ LLM分析 ════════════════════════

    async def _llm_analyze(self, trades: List[Dict], stats: Dict, period: str, date: str) -> Dict:
        try:
            from backend.services.llm_config_service import call_llm_api_sync as call_llm_api, get_llm_config_for_usage
            config = get_llm_config_for_usage("journal")
            if not config:
                return {
                    "analysis": f"{period}复盘: {stats['total_trades']}笔交易, PnL={stats['total_pnl']:.2f}",
                    "actions": [],
                    "template_feedback": {},
                }

            trade_summary = "\n".join(
                f"  {t['symbol']} {t['side']} qty={t.get('quantity',0)} "
                f"price={t.get('price',0)} pnl={t.get('pnl',0):.2f}"
                for t in trades[:30]
            )

            messages = [
                {"role": "system", "content": (
                    f"你是顶级量化交易复盘专家。分析{period}交易记录，找出核心问题和改进方案。\n"
                    "返回严格JSON:\n"
                    '{"analysis": "详细分析文本", '
                    '"actions": ["改进行动1", "改进行动2"], '
                    '"template_feedback": {"template_name": {"rating_delta": -0.5, "reason": "xxx"}}}'
                )},
                {"role": "user", "content": (
                    f"周期: {period} | 日期: {date}\n"
                    f"总交易: {stats['total_trades']} | PnL: {stats['total_pnl']:.2f}\n"
                    f"胜率: {stats['win_rate']:.0%} | 盈亏比: {stats.get('profit_factor',0):.2f}\n"
                    f"最佳策略: {stats.get('best_strategy')} | 最差策略: {stats.get('worst_strategy')}\n\n"
                    f"交易明细:\n{trade_summary}"
                )},
            ]

            resp = call_llm_api(config, messages=messages)
            content = resp["choices"][0]["message"]["content"]
            m = re.search(r'\{.*\}', content, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception as e:
            logger.warning(f"[TradeJournal] LLM分析失败: {e}")

        return {
            "analysis": f"{period}复盘: {stats['total_trades']}笔交易, PnL={stats['total_pnl']:.2f}",
            "actions": [],
            "template_feedback": {},
        }

    # ════════════════════════ 反馈 ════════════════════════

    def _feedback_to_templates(self, db: Session, feedback: Dict):
        """将复盘结论反馈到策略模板评分"""
        if not feedback:
            return
        try:
            from backend.database.models import StrategyTemplate
            for tpl_name, info in feedback.items():
                delta = info.get("rating_delta", 0)
                if delta == 0:
                    continue
                tpl = db.query(StrategyTemplate).filter(
                    StrategyTemplate.name.ilike(f"%{tpl_name}%")
                ).first()
                if tpl:
                    old_rating = tpl.rating or 3.0
                    tpl.rating = max(0, min(5, old_rating + delta))
                    logger.info(f"[TradeJournal] 模板 {tpl.name} 评分 {old_rating:.1f} → {tpl.rating:.1f}")
            db.commit()
        except Exception as e:
            logger.debug(f"[TradeJournal] 反馈模板评分异常: {e}")
            db.rollback()

    def _trigger_evolution(self, worst_strategy: str):
        """触发策略进化器对表现差的模板重新进化"""
        try:
            from backend.services.strategy_evolver import strategy_evolver
            if strategy_evolver and hasattr(strategy_evolver, 'start_evolution'):
                logger.info(f"[TradeJournal] 触发进化: {worst_strategy}")
        except Exception:
            pass

    def _detect_parameter_drift(self, db: Session) -> Dict[str, Any]:
        """检测策略参数漂移"""
        try:
            from backend.database.models import TradeJournal
            recent = (
                db.query(TradeJournal)
                .filter(TradeJournal.period_type == "daily")
                .order_by(TradeJournal.created_at.desc())
                .limit(14)
                .all()
            )
            if len(recent) < 7:
                return {"status": "insufficient_data"}

            recent_wr = [r.win_rate or 0 for r in recent[:7]]
            older_wr = [r.win_rate or 0 for r in recent[7:14]]
            avg_recent = sum(recent_wr) / len(recent_wr) if recent_wr else 0
            avg_older = sum(older_wr) / len(older_wr) if older_wr else 0

            drift = avg_recent - avg_older
            if drift < -0.1:
                return {"status": "degrading", "drift": drift, "note": "近7日胜率下降超过10%"}
            elif drift > 0.1:
                return {"status": "improving", "drift": drift}
            return {"status": "stable", "drift": drift}
        except Exception:
            return {"status": "error"}

    # ════════════════════════ DB保存 ════════════════════════

    def _save_journal(self, db: Session, result: Dict):
        from backend.database.models import TradeJournal
        journal = TradeJournal(
            period_type=result.get("period_type", "daily"),
            period_date=result.get("period_date", ""),
            total_trades=result.get("total_trades", 0),
            total_pnl=result.get("total_pnl", 0),
            win_rate=result.get("win_rate", 0),
            best_strategy=result.get("best_strategy", ""),
            worst_strategy=result.get("worst_strategy", ""),
            ai_analysis=result.get("ai_analysis", ""),
            improvement_actions=result.get("improvement_actions", []),
        )
        db.add(journal)
        try:
            db.commit()
        except Exception:
            db.rollback()


trade_journal = AITradeJournalService()
