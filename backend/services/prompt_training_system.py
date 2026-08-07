"""
Prompt Training System - 提示词训练与优化

Paper 默认「减门」：优化后直接切换策略 master_prompt，不走假 A/B。
PROMPT_TRAINING_AB_ENABLED=true 时仅延长对照记录窗口（B 版仍立即生效）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.database.models import (
    AIDecisionLog,
    AIStrategy,
    PromptTemplate,
    PromptTrainingRecord,
)

logger = logging.getLogger(__name__)


def _ab_enabled() -> bool:
    try:
        from backend.config.settings import PROMPT_TRAINING_AB_ENABLED
        return bool(PROMPT_TRAINING_AB_ENABLED)
    except Exception:
        return False


def _metrics(record: PromptTrainingRecord) -> Dict[str, Any]:
    m = record.training_metrics
    return dict(m) if isinstance(m, dict) else {}


def _set_metrics(record: PromptTrainingRecord, **updates: Any) -> None:
    m = _metrics(record)
    m.update(updates)
    record.training_metrics = m


class PromptTrainingResult:
    """提示词训练结果"""

    def __init__(
        self,
        training_id: int,
        original_prompt_id: int,
        optimized_prompt_id: Optional[int] = None,
        performance_improvement: float = 0.0,
        recommendations: Optional[List[str]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ):
        self.training_id = training_id
        self.original_prompt_id = original_prompt_id
        self.optimized_prompt_id = optimized_prompt_id
        self.performance_improvement = performance_improvement
        self.recommendations = recommendations or []
        self.success = success
        self.error_message = error_message


class PromptTrainingSystem:
    """提示词训练：历史分析 → 优化版本 → 直接激活（Paper 默认）。"""

    def __init__(self, db: Session):
        self.db = db

    def train_prompt_from_history(
        self,
        strategy_id: str,
        base_prompt_id: int,
        start_date: datetime,
        end_date: datetime,
        optimization_target: str = "sharpe",
    ) -> PromptTrainingResult:
        try:
            decisions = self._load_historical_decisions(strategy_id, start_date, end_date)
            if len(decisions) < 10:
                return PromptTrainingResult(
                    training_id=0,
                    original_prompt_id=base_prompt_id,
                    success=False,
                    error_message="历史数据不足，至少需要10条决策记录",
                )

            analysis = self._analyze_decision_quality(decisions, optimization_target)
            recommendations = self._generate_optimization_recommendations(base_prompt_id, analysis)

            training_record = PromptTrainingRecord(
                strategy_id=strategy_id,
                base_prompt_id=base_prompt_id,
                training_metrics={
                    "status": "completed",
                    "optimization_target": optimization_target,
                    "sample_count": len(decisions),
                    "baseline_metrics": {
                        "win_rate": analysis.get("win_rate", 0.0),
                        "avg_profit": analysis.get("avg_profit", 0.0),
                        "sharpe_ratio": analysis.get("sharpe_ratio", 0.0),
                    },
                    "optimization_suggestions": recommendations,
                    "training_data_start": start_date.isoformat(),
                    "training_data_end": end_date.isoformat(),
                },
            )
            self.db.add(training_record)
            self.db.commit()
            self.db.refresh(training_record)

            return PromptTrainingResult(
                training_id=training_record.id,
                original_prompt_id=base_prompt_id,
                recommendations=recommendations,
                success=True,
            )
        except Exception as e:
            logger.error("Prompt training failed: %s", e)
            self.db.rollback()
            return PromptTrainingResult(
                training_id=0,
                original_prompt_id=base_prompt_id,
                success=False,
                error_message=str(e),
            )

    def create_optimized_prompt_version(
        self,
        training_id: int,
        optimization_instructions: str,
    ) -> Optional[int]:
        try:
            training_record = self.db.query(PromptTrainingRecord).filter(
                PromptTrainingRecord.id == training_id
            ).first()
            if not training_record:
                return None

            original_prompt = self.db.query(PromptTemplate).filter(
                PromptTemplate.id == training_record.base_prompt_id
            ).first()
            if not original_prompt:
                return None

            from backend.services.ai_prompt_generation_service import AiPromptGenerationService

            ai_service = AiPromptGenerationService(self.db)
            m = _metrics(training_record)
            suggestions = m.get("optimization_suggestions") or []
            baseline = m.get("baseline_metrics") or {}

            optimization_context = f"""
基于以下训练结果优化提示词：

原始提示词：
{original_prompt.template_text}

训练数据分析：
- 样本数量：{m.get('sample_count', 0)}
- 基线胜率：{baseline.get('win_rate', 0.0):.2%}
- 基线夏普比率：{baseline.get('sharpe_ratio', 0.0):.2f}

优化建议：
{chr(10).join(f"- {rec}" for rec in suggestions)}

用户指令：
{optimization_instructions}

请生成优化后的提示词文本。
"""
            optimized_text = ai_service.generate_with_conversation(
                messages=[{"role": "user", "content": optimization_context}],
                account_id=None,
            )
            if isinstance(optimized_text, dict):
                optimized_text = optimized_text.get("content") or optimized_text.get("text") or ""
            optimized_text = str(optimized_text or "").strip()
            if not optimized_text:
                logger.error("AI 未返回有效优化 prompt")
                return None

            ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
            new_prompt = PromptTemplate(
                key=f"{original_prompt.key}_opt_{ts}",
                name=f"{original_prompt.name} (优化版 {ts})",
                description=f"prompt_training 优化自 {original_prompt.id}",
                template_text=optimized_text,
                system_template_text=original_prompt.system_template_text,
                is_system="false",
                created_by="prompt_training_system",
            )
            self.db.add(new_prompt)
            self.db.flush()

            training_record.optimized_prompt_id = new_prompt.id
            _set_metrics(training_record, status="optimized")
            self.db.commit()
            logger.info("Created optimized prompt: %s", new_prompt.id)
            return new_prompt.id
        except Exception as e:
            logger.error("Failed to create optimized prompt: %s", e)
            self.db.rollback()
            return None

    def _activate_prompt_on_strategy(self, strategy_id: str, prompt_id: int) -> int:
        """将策略及其账户绑定切换到指定 prompt。"""
        strategy = self.db.query(AIStrategy).filter(
            AIStrategy.strategy_id == strategy_id
        ).first()
        if not strategy:
            logger.warning("[PromptTraining] 策略不存在: %s", strategy_id)
            return 0

        old_id = strategy.master_prompt_template_id
        strategy.master_prompt_template_id = prompt_id
        synced = 0
        try:
            from backend.database.models import AccountPromptBinding

            if strategy.account_id:
                binding = self.db.query(AccountPromptBinding).filter(
                    AccountPromptBinding.account_id == strategy.account_id
                ).first()
                if binding and binding.prompt_template_id != prompt_id:
                    binding.prompt_template_id = prompt_id
                    binding.updated_by = "prompt_training_system"
                    synced += 1
        except Exception as err:
            logger.warning("[PromptTraining] AccountPromptBinding 同步失败: %s", err)

        self.db.commit()
        logger.info(
            "[PromptTraining] 策略 %s prompt %s→%s (synced %d bindings)",
            strategy_id, old_id, prompt_id, synced,
        )
        return synced

    def start_ab_test(
        self,
        strategy_id: str,
        prompt_a_id: int,
        prompt_b_id: int,
        test_duration_days: int = 7,
    ) -> Dict[str, Any]:
        """启动 prompt 对照：Paper 默认直接激活 B 版（非假 A/B）。"""
        try:
            now = datetime.now(timezone.utc)
            end = now + timedelta(days=test_duration_days)
            ab_mode = _ab_enabled()
            synced = self._activate_prompt_on_strategy(strategy_id, prompt_b_id)

            status = "ab_testing" if ab_mode else "activated"
            mode = "ab_tracking" if ab_mode else "direct_active"

            training_record = PromptTrainingRecord(
                strategy_id=strategy_id,
                base_prompt_id=prompt_a_id,
                optimized_prompt_id=prompt_b_id,
                training_metrics={
                    "status": status,
                    "mode": mode,
                    "prompt_a_id": prompt_a_id,
                    "prompt_b_id": prompt_b_id,
                    "training_data_start": now.isoformat(),
                    "training_data_end": end.isoformat(),
                    "activated_at": now.isoformat(),
                    "synced_accounts": synced,
                    "optimization_suggestions": [
                        "B 版已直接绑定策略" if not ab_mode else "B 版已激活，对照窗口用于指标跟踪",
                    ],
                },
            )
            self.db.add(training_record)
            self.db.commit()
            self.db.refresh(training_record)

            logger.info(
                "[PromptTraining] %s strategy=%s A=%s B=%s synced=%d",
                mode, strategy_id, prompt_a_id, prompt_b_id, synced,
            )
            return {
                "ok": True,
                "training_id": training_record.id,
                "mode": mode,
                "status": status,
                "synced_accounts": synced,
                "message": (
                    "B 版已直接生效（Paper 减门）"
                    if not ab_mode
                    else "B 版已生效，对照记录用于后续指标对比"
                ),
            }
        except Exception as e:
            logger.error("Failed to start prompt activation: %s", e)
            self.db.rollback()
            return {"ok": False, "error": str(e)}

    def recover_stuck_ab_tests(self) -> Dict[str, Any]:
        """修复 status=ab_testing 但未绑定 B 版的训练记录。"""
        recovered: List[int] = []
        rows = self.db.query(PromptTrainingRecord).all()
        for rec in rows:
            m = _metrics(rec)
            if m.get("status") != "ab_testing":
                continue
            b_id = rec.optimized_prompt_id
            if not b_id or not rec.strategy_id:
                continue
            strat = self.db.query(AIStrategy).filter(
                AIStrategy.strategy_id == rec.strategy_id
            ).first()
            if strat and strat.master_prompt_template_id == b_id:
                _set_metrics(rec, status="activated", mode="already_active")
                recovered.append(rec.id)
                continue
            synced = self._activate_prompt_on_strategy(rec.strategy_id, b_id)
            _set_metrics(
                rec,
                status="activated",
                mode="recover_stuck",
                activated_at=datetime.now(timezone.utc).isoformat(),
                synced_accounts=synced,
            )
            recovered.append(rec.id)
        if recovered:
            self.db.commit()
        return {"recovered": len(recovered), "ids": recovered}

    def get_ab_test_results(self, training_id: int) -> Optional[Dict[str, Any]]:
        try:
            training_record = self.db.query(PromptTrainingRecord).filter(
                PromptTrainingRecord.id == training_id
            ).first()
            if not training_record:
                return None

            m = _metrics(training_record)
            status = m.get("status", "unknown")
            if status not in ("ab_testing", "activated", "optimized"):
                return None

            start_s = m.get("training_data_start")
            end_s = m.get("training_data_end")
            start_time = datetime.fromisoformat(start_s.replace("Z", "+00:00")) if start_s else training_record.created_at
            end_time = datetime.fromisoformat(end_s.replace("Z", "+00:00")) if end_s else datetime.now(timezone.utc)

            decisions_a = self._load_decisions_by_prompt(
                training_record.strategy_id,
                training_record.base_prompt_id,
                start_time,
                end_time,
            )
            decisions_b = self._load_decisions_by_prompt(
                training_record.strategy_id,
                training_record.optimized_prompt_id,
                start_time,
                end_time,
            )
            metrics_a = self._calculate_metrics(decisions_a)
            metrics_b = self._calculate_metrics(decisions_b)

            return {
                "test_id": training_id,
                "status": status,
                "mode": m.get("mode"),
                "start_time": start_time.isoformat() if start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
                "prompt_a": {
                    "id": training_record.base_prompt_id,
                    "decisions_count": len(decisions_a),
                    "metrics": metrics_a,
                },
                "prompt_b": {
                    "id": training_record.optimized_prompt_id,
                    "decisions_count": len(decisions_b),
                    "metrics": metrics_b,
                },
                "winner": self._determine_winner(metrics_a, metrics_b),
                "active_prompt_id": training_record.optimized_prompt_id,
            }
        except Exception as e:
            logger.error("Failed to get A/B test results: %s", e)
            return None

    def _load_historical_decisions(
        self,
        strategy_id: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[AIDecisionLog]:
        return (
            self.db.query(AIDecisionLog)
            .filter(
                AIDecisionLog.ai_strategy_id == strategy_id,
                AIDecisionLog.timestamp >= start_date,
                AIDecisionLog.timestamp <= end_date,
            )
            .order_by(AIDecisionLog.timestamp)
            .all()
        )

    def _load_decisions_by_prompt(
        self,
        strategy_id: str,
        prompt_id: int,
        start_date: datetime,
        end_date: datetime,
    ) -> List[AIDecisionLog]:
        if not prompt_id:
            return []
        return (
            self.db.query(AIDecisionLog)
            .filter(
                AIDecisionLog.ai_strategy_id == strategy_id,
                AIDecisionLog.prompt_template_id == prompt_id,
                AIDecisionLog.timestamp >= start_date,
                AIDecisionLog.timestamp <= end_date,
            )
            .all()
        )

    def _analyze_decision_quality(
        self,
        decisions: List[AIDecisionLog],
        optimization_target: str,
    ) -> Dict[str, Any]:
        if not decisions:
            return {}
        quality_scores = [
            d.decision_quality_score for d in decisions
            if d.decision_quality_score is not None
        ]
        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
        wins = sum(1 for score in quality_scores if score > 0.5)
        win_rate = wins / len(quality_scores) if quality_scores else 0.0
        return {
            "total_decisions": len(decisions),
            "avg_quality_score": avg_quality,
            "win_rate": win_rate,
            "sharpe_ratio": 0.0,
            "avg_profit": 0.0,
        }

    def _generate_optimization_recommendations(
        self,
        prompt_id: int,
        analysis: Dict[str, Any],
    ) -> List[str]:
        recommendations: List[str] = []
        avg_quality = analysis.get("avg_quality_score", 0.0)
        win_rate = analysis.get("win_rate", 0.0)
        if avg_quality < 0.5:
            recommendations.append("决策质量偏低，建议增强风险意识和市场判断")
        if win_rate < 0.5:
            recommendations.append("胜率低于50%，建议优化入场时机判断")
        if avg_quality < 0.7:
            recommendations.append("建议添加更多市场状态判断逻辑")
        if not recommendations:
            recommendations.append("当前提示词表现良好，建议进行微调优化")
        return recommendations

    def _calculate_metrics(self, decisions: List[AIDecisionLog]) -> Dict[str, float]:
        if not decisions:
            return {"win_rate": 0.0, "avg_quality": 0.0, "total_decisions": 0}
        quality_scores = [
            d.decision_quality_score for d in decisions
            if d.decision_quality_score is not None
        ]
        wins = sum(1 for score in quality_scores if score > 0.5)
        return {
            "win_rate": wins / len(quality_scores) if quality_scores else 0.0,
            "avg_quality": sum(quality_scores) / len(quality_scores) if quality_scores else 0.0,
            "total_decisions": len(decisions),
        }

    def _determine_winner(
        self,
        metrics_a: Dict[str, float],
        metrics_b: Dict[str, float],
    ) -> str:
        if metrics_b["win_rate"] > metrics_a["win_rate"]:
            return "prompt_b"
        if metrics_a["win_rate"] > metrics_b["win_rate"]:
            return "prompt_a"
        return "tie"
