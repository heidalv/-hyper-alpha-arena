"""Hermes 自进化系统 — 总调度器

协调四层引擎的定时任务执行，管理进化度量指标。
通过 opencode_scheduler 注册的定时任务来驱动各层。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from backend.services.hermes_db import init_hermes_db, hermes_fetchall, hermes_fetchone

logger = logging.getLogger(__name__)


class HermesOrchestrator:
    """Hermes 自进化系统总调度器（单例）。

    每一层由一个独立引擎负责，Orchestrator 负责：
    1. 初始化（建库 + 各引擎预热）
    2. 定时任务编排
    3. 进化度量聚合
    """

    def __init__(self):
        from backend.services.hermes_proposal_wisdom_engine import ProposalWisdomEngine
        from backend.services.hermes_prompt_optimizer_engine import PromptOptimizerEngine
        from backend.services.hermes_architecture_evolution_engine import ArchitectureEvolutionEngine
        from backend.services.hermes_strategy_genesis_engine import StrategyGenesisEngine

        self.wisdom = ProposalWisdomEngine()
        self.prompt_opt = PromptOptimizerEngine()
        self.arch_evo = ArchitectureEvolutionEngine()
        self.strategy_gen = StrategyGenesisEngine()
        self._initialized = False

    def ensure_initialized(self) -> None:
        """幂等初始化：建库表 + L2 基线 prompt 快照。"""
        if self._initialized:
            return
        init_hermes_db()
        try:
            self.prompt_opt.ensure_baseline_versions()
        except Exception as e:
            logger.warning("[Hermes] L2 基线 prompt 快照失败: %s", e)
        try:
            rec = self.prompt_opt.recover_stuck_versions()
            if rec.get("count"):
                logger.info("[Hermes] L2 已恢复卡住 prompt: %s", rec.get("recovered"))
        except Exception as e:
            logger.warning("[Hermes] L2 recover_stuck 失败: %s", e)
        try:
            from backend.database.connection import SessionLocal
            # [2026-08-17 删除] prompt_training_system 已移除；卡住 A/B 记录由
            # prompt_opt.recover_stuck_versions 覆盖（上方已调用）。
            logger.debug("[Hermes] prompt_training_system removed; skip recover_stuck_ab_tests")
        except Exception as e:
            # [2026-08-05 v6 8.3 阶段1] 静默→告警：恢复失败必须可见
            logger.warning("[Hermes] PromptTraining recover_stuck: %s", e)
        try:
            l3_rec = self.arch_evo.auto_accept_pending_paper()
            if l3_rec.get("accepted"):
                logger.info(
                    "[Hermes] L3 auto_accept: %d accepted, %d remaining",
                    l3_rec.get("accepted"), l3_rec.get("remaining_pending"),
                )
        except Exception as e:
            # [2026-08-05 v6 8.3 阶段1] 静默→告警：L3 auto_accept 异常必须可见
            logger.warning("[Hermes] L3 auto_accept: %s", e)
        try:
            l3_impl = self.arch_evo.reconcile_implemented_paper(limit=200)
            if l3_impl.get("implemented"):
                logger.info(
                    "[Hermes] L3 reconcile_implemented: %d → implemented",
                    l3_impl.get("implemented"),
                )
        except Exception as e:
            # [2026-08-05 v6 8.3 阶段1] 静默→告警：reconcile_implemented 异常必须可见
            logger.warning("[Hermes] L3 reconcile_implemented: %s", e)
        self._initialized = True

    # ──── 定时任务入口（供 opencode_scheduler 注册）───

    def accumulate_wisdom(self) -> int:
        """L1: 1h — 积累提案智慧。返回新积累条数（>=0）；-1 表示执行失败。"""
        self.ensure_initialized()
        try:
            n = self.wisdom.accumulate_pending_wisdom()
            return n
        except Exception as e:
            logger.error("[Hermes] wisdom_accumulate error: %s", e, exc_info=True)
            return -1

    def run_meta_analysis(self) -> Dict[str, Any]:
        """L1: 6h — 更新模式库。"""
        self.ensure_initialized()
        try:
            updated = self.wisdom.update_pattern_library()
            patterns = self.wisdom.get_top_patterns(min_samples=2)
            return {"ok": True, "patterns_updated": updated, "top_patterns": len(patterns)}
        except Exception as e:
            logger.error("[Hermes] meta_analysis error: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    def run_prompt_optimization(self) -> Dict[str, Any]:
        """L2: 12h — Prompt 优化周期。"""
        self.ensure_initialized()
        try:
            result = self.prompt_opt.auto_optimize_cycle() or {}
            result["ok"] = True
            return result
        except Exception as e:
            logger.error("[Hermes] prompt_optimize error: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    def evaluate_ab_tests(self) -> int:
        """L2: 4h — 评估运行中的 A/B 测试。HERMES_L2_AB_ENABLED=false 时跳过。"""
        self.ensure_initialized()
        try:
            from backend.services.hermes_prompt_optimizer_engine import PromptOptimizerEngine
            if not PromptOptimizerEngine._ab_enabled():
                return 0
            return self.prompt_opt.evaluate_all_ab_tests()
        except Exception as e:
            logger.error("[Hermes] ab_test_eval error: %s", e, exc_info=True)
            return -1

    def run_architecture_evolution(self) -> Dict[str, Any]:
        """L3: 24h — 系统架构进化分析。

        成功判据不只看「没抛异常」：LLM 返回 error、或解析后 0 条提案且本轮
        本应有数据（param_churn 非空）时，报 ok=False，避免「status=ok 但产出为 0」
        的静默死链。
        """
        self.ensure_initialized()
        try:
            result = self.arch_evo.discover_architecture_gaps()
            stats = self.arch_evo.get_stats()
            new_proposals = len(result.get("proposals", []))
            llm_error = result.get("llm_error")
            # parsed_ok=False 表示 LLM 有返回但未能解析出提案结构（典型为思维链污染）
            parsed_ok = result.get("parsed_ok", True)
            ok = (not llm_error) and parsed_ok
            return {
                "ok": ok,
                "new_proposals": new_proposals,
                "stats": stats,
                "llm_error": llm_error,
                "parsed_ok": parsed_ok,
            }
        except Exception as e:
            logger.error("[Hermes] arch_evo error: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    def run_strategy_genesis(self) -> Dict[str, Any]:
        """L4: 24h — 策略创生。

        同 L3：LLM error 或解析失败时报 ok=False，杜绝静默零产出。
        """
        self.ensure_initialized()
        try:
            candidates = self.strategy_gen.generate_strategy_variants()
            llm_error = getattr(self.strategy_gen, "_last_llm_error", None)
            parsed_ok = getattr(self.strategy_gen, "_last_llm_parsed_ok", True)
            # 只有当真正生成候选时才部署孵化，避免无效 LLM 响应污染 paper 环境
            deployed = self.strategy_gen.incubate_candidates() if candidates else 0
            ok = (not llm_error) and parsed_ok
            return {
                "ok": ok,
                "candidates_generated": len(candidates),
                "deployed": deployed,
                "llm_error": llm_error,
                "parsed_ok": parsed_ok,
            }
        except Exception as e:
            logger.error("[Hermes] strategy_genesis error: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    def check_genesis_candidates(self) -> Dict[str, Any]:
        """L4: 6h — 检查孵化结果。"""
        self.ensure_initialized()
        try:
            result = self.strategy_gen.check_incubation_results()
            promo = self.strategy_gen.auto_propose_validated_promotions()
            stats = self.strategy_gen.get_stats()
            return {"ok": True, "checked": result, "governor_promote": promo, "stats": stats}
        except Exception as e:
            logger.error("[Hermes] genesis_check error: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}

    # ──── 进化度量 ────

    def compute_maturity_score(self) -> Dict[str, Any]:
        """计算 Hermes 成熟度评分 (0-100)。"""
        self.ensure_initialized()

        from backend.services.hermes_db import hermes_fetchall, hermes_fetchone

        # L1: 智慧质量 (25分)
        wisdom_total = len(hermes_fetchall("SELECT id FROM proposal_wisdom_records", ()))
        hi_conf_patterns = len(hermes_fetchall(
            "SELECT id FROM param_effect_patterns WHERE confidence_avg >= 0.4", ()
        ))
        l1_score = min(1.0, min(wisdom_total / 30, hi_conf_patterns / 5)) * 25

        # L2: Prompt 进化 (25分)
        prompt_versions = len(hermes_fetchall(
            "SELECT id FROM prompt_versions WHERE change_type IN ('auto_optimized','ab_test_winner')", ()
        ))
        latest_quality = hermes_fetchone(
            "SELECT avg_improved_rate FROM prompt_versions WHERE status='active' ORDER BY id DESC LIMIT 1"
        )
        l2_ir = float(latest_quality.get("avg_improved_rate", 0) or 0) if latest_quality else 0
        l2_score = min(1.0, prompt_versions / 3 + l2_ir * 0.5) * 25

        # L3: 架构进化 (25分)
        arch_stats = self.arch_evo.get_stats()
        l3_score = min(1.0, arch_stats.get("total", 0) / 5) * 25

        # L4: 策略创生 (25分)
        gen_stats = self.strategy_gen.get_stats()
        l4_score = min(1.0, (gen_stats.get("validated", 0) + gen_stats.get("promoted_live", 0) * 2) / 3) * 25

        total = round(l1_score + l2_score + l3_score + l4_score, 1)
        return {
            "maturity_score": total,
            "l1_wisdom": round(l1_score, 1),
            "l2_prompt": round(l2_score, 1),
            "l3_architecture": round(l3_score, 1),
            "l4_genesis": round(l4_score, 1),
            "details": {
                "wisdom_records": wisdom_total,
                "high_confidence_patterns": hi_conf_patterns,
                "prompt_versions": prompt_versions,
                "latest_prompt_improved_rate": round(l2_ir, 3),
                "architecture_proposals": arch_stats.get("total", 0),
                "genesis_total": gen_stats.get("total", 0),
                "genesis_validated": gen_stats.get("validated", 0),
                "genesis_promoted": gen_stats.get("promoted_live", 0),
            },
        }

    # ──── 完整巡检 ────

    def full_health_check(self) -> Dict[str, Any]:
        """返回 Hermes 全线健康状况。"""
        self.ensure_initialized()
        # 真实探测 DB：能跑通一次只读查询才算 ok，否则反映真实故障
        try:
            wisdom_rows = hermes_fetchall("SELECT COUNT(*) as cnt FROM proposal_wisdom_records")
            prompt_active = hermes_fetchall("SELECT id FROM prompt_versions WHERE status='active'")
            db_ok = True
            db_error: Optional[str] = None
        except Exception as e:
            logger.error("[Hermes] full_health_check DB 探测失败: %s", e, exc_info=True)
            wisdom_rows = []
            prompt_active = []
            db_ok = False
            db_error = str(e)
        sidecar_ok = False
        try:
            from backend.services.opencode_bridge import health_check
            sidecar_ok = bool(health_check())
        except Exception:
            sidecar_ok = False
        return {
            "db_ok": db_ok,
            "db_error": db_error,
            "sidecar_ok": sidecar_ok,
            "maturity": self.compute_maturity_score(),
            "l1": {
                "wisdom_total": wisdom_rows[0]["cnt"] if wisdom_rows else 0,
            },
            "l2": {
                "active_prompt": len(prompt_active),
            },
            "l3": self.arch_evo.get_stats(),
            "l4": self.strategy_gen.get_stats(),
        }


# 全局单例
hermes = HermesOrchestrator()
