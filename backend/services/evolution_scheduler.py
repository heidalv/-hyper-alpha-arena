"""
进化调度器 — 自动化回测进化 + 智慧刷新 + 提示词复盘

定时任务：
1. 每周自动进化（使用最新数据重跑进化）
2. 每天刷新交易智慧（从回测结果重新编译）
3. 每周复盘所有策略并触发提示词进化
4. 连续亏损时紧急触发重新进化
"""

import logging
import os as _os
import threading
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from backend.database.connection import AnalyticsSessionLocal, SessionLocal

logger = logging.getLogger(__name__)


def _get_full_param_ranges() -> Dict[str, Tuple[float, float]]:
    """完整的 GA 搜索参数范围（覆盖信号权重 + 风控 + 编排器阈值）

    阶段2(S2-10b)：在基础域之上叠加参数域扩展 —— Hermes L1 实盘归因的
    高置信模式（param_effect_patterns outcome=improved）会把搜索域向被
    验证有效的一侧动态扩展（increase→上界×1.2 / decrease→下界×0.8，
    总封顶 1.5 倍）。无模式或异常时原样返回基础域。
    """
    try:
        from backend.services.strategy_params_registry import PIPELINE_PARAM_RANGES
        base = {k: (float(lo), float(hi)) for k, (lo, hi) in PIPELINE_PARAM_RANGES.items()}
    except Exception:
        base = {
            "stop_loss_pct": (0.01, 0.08),
            "take_profit_pct": (0.02, 0.20),
            "default_leverage": (5, 20),
            "weight_funding": (0.05, 0.40),
            "weight_oi": (0.05, 0.40),
            "weight_liquidation": (0.05, 0.30),
            "weight_whale": (0.02, 0.25),
            "weight_fear_greed": (0.02, 0.15),
            "mid_rsi_bull": (50, 70),
            "mid_rsi_bear": (30, 50),
            "finalize_mid_weight": (0.3, 0.6),
            "confirmation_min_dims": (1, 3),
        }

    try:
        from backend.services.param_domain_expander import apply_domain_expansion
        expanded, changes = apply_domain_expansion(base)
        if changes:
            summary = ", ".join(
                f"{c['param_key']}->{c['new']}" for c in changes[:8]
            )
            logger.info(
                "[EvolutionScheduler] 参数域扩展 %d 项: %s", len(changes), summary
            )
        return expanded
    except Exception as e:
        logger.warning("[EvolutionScheduler] 参数域扩展失败，用基础域: %s", e)
        return base


class EvolutionScheduler:
    """回测进化自动调度"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._running_evolution = False
        self._lock = threading.Lock()
        logger.info("[EvoScheduler] 进化调度器初始化完成")

    def weekly_evolution(self):
        """每3天自动进化：对所有活跃模板用最新数据重跑进化。

        2026-06-11 学习系统升级：单目标 GA → NSGA-II 多目标进化。
        目标对齐 V5 经济学：profit_factor(净利润因子,最大化) /
        max_drawdown(最小化) / sharpe(最大化)。
        冠军参数除落库晋升外，还把派生的盈亏比门槛同步到
        data/v5_runtime_gates.json（统一下发通道，决策核心 60s 生效）。
        """
        try:
            from backend.config.settings import NSGA2_ENABLED
            if not NSGA2_ENABLED:
                logger.info("[EvoScheduler] NSGA2_ENABLED=false，跳过 weekly evolution")
                return
        except Exception:
            pass

        if self._running_evolution:
            logger.info("[EvoScheduler] 进化正在运行中，跳过")
            return

        # [2026-08-14 P1-H1 修复] TOCTOU 竞态：此前锁外检查 _running_evolution、
        # 锁内置 True 后立即释放锁——两个线程可同时看到 False 并各自置位后
        # 并发跑完整进化。检查+置位必须同处临界区。
        with self._lock:
            if self._running_evolution:
                logger.info("[EvoScheduler] 进化正在运行中，跳过")
                return
            self._running_evolution = True

        # strategy_templates / backtest_runs 都在主库（曾误用 Analytics 会话，
        # PG 双库下 UndefinedTable 导致进化自 5/21 起静默停摆）
        db = SessionLocal()
        try:
            from backend.database.models import StrategyTemplate
            from backend.services.genetic_optimizer import (
                NSGAIIOptimizer, MultiObjectiveIndividual,
            )
            from backend.services.strategy_evolver import StrategyEvolver

            evolver = StrategyEvolver()
            optimizer = NSGAIIOptimizer()
            # V5 经济学多目标（实例属性覆盖类默认 sharpe/mdd/win_rate）
            optimizer.OBJECTIVE_NAMES = ['profit_factor', 'max_drawdown', 'sharpe']
            optimizer.MAXIMIZE = {'profit_factor': True, 'max_drawdown': False, 'sharpe': True}

            templates = db.query(StrategyTemplate).filter(
                StrategyTemplate.is_active == True
            ).order_by(StrategyTemplate.rating.desc()).limit(8).all()

            logger.info(f"[EvoScheduler] 开始自动进化（NSGA-II 多目标）: {len(templates)} 个模板（按评分 Top8）")

            best_champion_genome = None
            best_champion_fitness = -1.0
            evolved_count = 0
            promoted_count = 0
            best_evo_fitness = 0.0
            best_evo_sharpe = 0.0
            best_evo_pf = 0.0
            best_evo_mdd = 0.0

            for tpl in templates:
                try:
                    # [2026-08-11 修复] 上一模板失败后 session 可能处于 failed 事务，
                    # 不 rollback 会导致后续所有模板报
                    # “This Session's transaction has been rolled back...”。
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    logger.info(f"[EvoScheduler] 进化模板 [{evolved_count+1}/{len(templates)}]: {tpl.name or tpl.template_id} (rating={tpl.rating})")
                    # 构建多目标 fitness_fn：包装 evolver 的单次回测
                    def _make_fitness_fn(template, _evolver, _db):
                        def fitness_fn(genome: dict) -> MultiObjectiveIndividual:
                            try:
                                result = _evolver._run_single_backtest_for_genome(
                                    template=template,
                                    genome=genome,
                                    db=_db,
                                )
                                if result:
                                    score = StrategyEvolver.composite_fitness(result)
                                    # profit_factor 上限截断，防止无亏损样本的极端值主导排序
                                    pf = min(float(result.get("profit_factor", 0) or 0), 10.0)
                                    return MultiObjectiveIndividual(
                                        genome=genome,
                                        fitness=score,
                                        sharpe=float(result.get("sharpe", 0)),
                                        max_drawdown=float(result.get("max_drawdown", 1)),
                                        total_trades=int(result.get("total_trades", 0)),
                                        objectives={
                                            "profit_factor": pf,
                                            "max_drawdown": float(result.get("max_drawdown", 1)),
                                            "sharpe": float(result.get("sharpe", 0)),
                                        },
                                    )
                            except Exception as e:
                                logger.debug(f"[EvoScheduler] fitness_fn 单次回测异常: {e}")
                            return MultiObjectiveIndividual(
                                genome=genome, fitness=0, sharpe=-1,
                                max_drawdown=1, total_trades=0,
                                objectives={"profit_factor": 0.0, "max_drawdown": 1.0, "sharpe": -1.0},
                            )
                        return fitness_fn

                    # 完整管线参数搜索范围（与 PIPELINE_PARAM_RANGES 对齐）
                    param_ranges = _get_full_param_ranges()

                    front = optimizer.evolve_multi_objective(
                        template_id=str(tpl.template_id),
                        param_ranges=param_ranges,
                        fitness_fn=_make_fitness_fn(tpl, evolver, db),
                        generations=20,
                        population_size=24,
                    )
                    best = front.get_best_compromise() if front else None

                    if best is not None and best.fitness > 0:
                        # GA 成功分支落库 + 晋升 + 写 last_evolution_at
                        champion = evolver.persist_genetic_result(
                            db=db,
                            tpl=tpl,
                            best_genome=best.genome,
                            generations_run=front.generation,
                            parent_run_id=None,
                        )
                        logger.info(
                            f"[EvoScheduler] NSGA-II 模板 {tpl.template_id} 进化完成 "
                            f"fitness={best.fitness:.3f} Sharpe={best.sharpe:.2f} "
                            f"PF={best.objectives.get('profit_factor', 0):.2f} "
                            f"MDD={best.objectives.get('max_drawdown', 0):.2%} "
                            f"front_size={len(front.individuals)} "
                            f"promoted={bool(champion and champion.get('promoted'))}"
                        )

                        evolved_count += 1
                        if best.fitness > best_evo_fitness:
                            best_evo_fitness = best.fitness
                            best_evo_sharpe = best.sharpe
                            best_evo_pf = best.objectives.get('profit_factor', 0)
                            best_evo_mdd = best.objectives.get('max_drawdown', 0)
                        if champion and champion.get('promoted'):
                            promoted_count += 1

                        # 跨模板记录最优冠军，用于同步 v5 gates
                        if best.fitness > best_champion_fitness:
                            best_champion_fitness = best.fitness
                            best_champion_genome = dict(best.genome)
                            best_champion_genome["_template_id"] = str(tpl.template_id)

                        # ── 逐模板记录进化事件（防止后端重启丢失全部进度）──
                        self._record_evolution_event(
                            evolution_type="weekly",
                            trigger_reason=f"模板进化: {tpl.name or tpl.template_id}",
                            template_count=evolved_count,
                            promoted_count=promoted_count,
                            best_fitness=best_evo_fitness,
                            best_sharpe=best_evo_sharpe,
                            best_profit_factor=best_evo_pf,
                            best_max_drawdown=best_evo_mdd,
                            success=True,
                        )

                        # ── 喂入 QAABridge 进化历史 ──
                        try:
                            from backend.services.qaa_evolution_bridge import QAABridge
                            bridge = QAABridge.get_instance()
                            if bridge._enabled and bridge.history:
                                bridge.history.append(
                                    domain="trading",
                                    event="nsga2_evolution_cycle",
                                    details={
                                        "template_id": str(tpl.template_id),
                                        "best_fitness": float(best.fitness),
                                        "best_sharpe": float(best.sharpe),
                                        "profit_factor": float(best.objectives.get("profit_factor", 0)),
                                        "generations_run": int(front.generation),
                                        "pareto_front_size": len(front.individuals),
                                        "promoted": bool(champion and champion.get("promoted")),
                                    },
                                )
                        except Exception as _qaa_err:
                            logger.debug(f"[EvoScheduler] QAA history feed skipped: {_qaa_err}")
                    else:
                        # 降级到旧 evolver（fitness_fn 无法运行时）
                        evolver.run_evolution(
                            db=db,
                            template_id=tpl.template_id,
                            generations=30,
                            population_size=20,
                        )
                        logger.info(f"[EvoScheduler] 模板 {tpl.template_id} 降级到 StrategyEvolver 完成")
                except Exception as e:
                    # [2026-08-11 修复] 失败后先 rollback，保证下一个模板能继续使用同一会话。
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    logger.warning(f"[EvoScheduler] 模板 {tpl.template_id} 进化失败: {e}")

            # ── 冠军参数 → v5_runtime_gates.json（统一下发通道）──
            if best_champion_genome:
                try:
                    self._sync_champion_to_v5_gates(best_champion_genome, best_champion_fitness)
                except Exception as _g_err:
                    logger.warning(f"[EvoScheduler] 冠军参数同步 v5 gates 失败: {_g_err}")

            logger.info("[EvoScheduler] 自动进化全部完成")

            # ── 记录 evolution_events ──
            import json as _json
            self._record_evolution_event(
                evolution_type="weekly",
                trigger_reason="定时",
                template_count=evolved_count,
                promoted_count=promoted_count,
                best_fitness=best_evo_fitness,
                best_sharpe=best_evo_sharpe,
                best_profit_factor=best_evo_pf,
                best_max_drawdown=best_evo_mdd,
                success=True,
            )

            # 进化完成后自动触发智慧刷新 + 提示词注入
            try:
                self.daily_wisdom_refresh()
                self.weekly_prompt_review()
                logger.info("[EvoScheduler] 进化后联动：智慧刷新 + 策略复盘已触发")
            except Exception as chain_err:
                logger.warning(f"[EvoScheduler] 进化后联动执行异常: {chain_err}")

        except Exception as e:
            logger.error(f"[EvoScheduler] 每周进化调度异常: {e}", exc_info=True)
        finally:
            self._running_evolution = False
            db.close()

    def _sync_champion_to_v5_gates(self, genome: dict, fitness: float):
        """把冠军 genome 派生的风控门槛提交给 RuntimeGovernor 仲裁中枢。

        2026-06-14 调参中枢重构：进化反哺不再直写 data/v5_runtime_gates.json
        （此前与 decision_feedback 整文件覆盖互相打架），改为向 RuntimeGovernor
        提交一条 evolution_gc 意图，由仲裁中枢统一裁决后唯一写入 runtime_tuning.json。

        安全约束：
        - 只派生 min_risk_reward（TP/SL 盈亏比），夹紧在 [V5_MIN_RISK_REWARD, V5_MAX_RUNTIME_MIN_RR]
          —— 进化只能收紧或保持 V5 硬约束，不能放松；上限 2.5 避免 3.0 误拦高置信 AI 信号。
        - decision_feedback 的近期反馈优先级更高（实盘反馈 > 回测冠军）；本意图 TTL 7 天。
        """
        sl = float(genome.get("stop_loss_pct", 0) or 0)
        tp = float(genome.get("take_profit_pct", 0) or 0)
        if sl <= 0 or tp <= 0:
            logger.info("[EvoScheduler] 冠军 genome 无 TP/SL 参数，跳过 gates 同步")
            return

        try:
            from backend.config.settings import V5_MIN_RISK_REWARD, V5_MAX_RUNTIME_MIN_RR
            rr_floor = float(V5_MIN_RISK_REWARD)
            rr_cap = float(V5_MAX_RUNTIME_MIN_RR)
        except Exception:
            rr_floor = 1.8
            rr_cap = 2.5

        derived_rr = round(max(rr_floor, min(rr_cap, tp / sl)), 2)

        try:
            from backend.services.runtime_governor import runtime_governor as gov
            gov.submit_intent(
                "min_risk_reward", derived_rr, source="evolution_gc",
                confidence=0.5,
                reason=(
                    f"champion TP {tp:.1%}/SL {sl:.1%} → rr={derived_rr} "
                    f"(template={genome.get('_template_id', '')}, fitness={fitness:.3f})"
                ),
            )
            logger.info(
                f"[EvoScheduler] 冠军参数已提交 Governor 意图: min_risk_reward={derived_rr} "
                f"(TP {tp:.1%} / SL {sl:.1%}, fitness={fitness:.3f})"
            )
        except Exception as err:
            logger.warning(f"[EvoScheduler] 提交 Governor 意图失败: {err}")

    def daily_wisdom_refresh(self):
        """每天刷新交易智慧"""
        db = SessionLocal()
        try:
            from backend.services.wisdom_tracker import wisdom_tracker
            refreshed = wisdom_tracker.auto_refresh_wisdom(db)
            logger.info(f"[EvoScheduler] 每日智慧刷新完成: {refreshed} 个模板")

            # 智慧刷新后同步到 RAG 索引
            self._trigger_rag_index(db, "trading_wisdom")
        except Exception as e:
            logger.error(f"[EvoScheduler] 智慧刷新异常: {e}", exc_info=True)
        finally:
            db.close()

    def weekly_prompt_review(self):
        """每周策略复盘 + 提示词进化 + 检查是否有新晋升模板"""
        try:
            from backend.services.strategy_learning_service import strategy_learning
            results = strategy_learning.run_all_reviews(days=7)
            evolved_count = sum(1 for r in results if r.get("prompt_evolved"))
            adapted_count = sum(1 for r in results if r.get("parameters_adapted"))
            logger.info(
                f"[EvoScheduler] 每周复盘完成: {len(results)} 个策略, "
                f"{evolved_count} 个Prompt进化, {adapted_count} 个参数自适应"
            )
        except Exception as e:
            logger.error(f"[EvoScheduler] 策略复盘异常: {e}", exc_info=True)

    def weekly_data_decay(self):
        """每周执行数据衰减"""
        # StrategyRegimeScore 是主库模型，必须用 SessionLocal；
        # 此前误用 AnalyticsSessionLocal 导致 PG 三库部署下衰减任务一直异常空转
        db = SessionLocal()
        try:
            from backend.services.unified_learning_service import unified_learning
            unified_learning.decay_old_scores(db)
            logger.info("[EvoScheduler] 数据衰减完成")
        except Exception as e:
            logger.error(f"[EvoScheduler] 数据衰减异常: {e}", exc_info=True)
        finally:
            db.close()

        # M11: master_close_guard 反事实校准（被拦的 close 如果执行了会怎样）
        # M12: 退出路径统一审计（按通道聚合盈亏/留存率）
        try:
            from backend.services.close_guard_calibrator import (
                run_close_guard_calibration,
                run_exit_audit,
            )
            run_close_guard_calibration(lookback_days=14)
            run_exit_audit(lookback_days=30)
        except Exception as _cg_err:
            logger.error(f"[EvoScheduler] 平仓质量闭环异常: {_cg_err}", exc_info=True)

    def daily_signal_weight_update(self):
        """每日更新信号权重：基于交易结果反馈自适应调整情报引擎各信号分量的权重"""
        db = SessionLocal()
        try:
            from backend.services.signal_feedback_tracker import signal_feedback_tracker
            updated = signal_feedback_tracker.update_weights(db)
            if updated:
                logger.info("[EvoScheduler] 每日信号权重更新完成")
            else:
                logger.info("[EvoScheduler] 信号权重更新跳过（交易数不足或无显著变化）")

            # M7: 因子 IC 有效性闭环 — 评估因子方向胜率/IC，
            # 写 factor_performance_logs 并产出运行时降权文件
            try:
                from backend.services.factor_ic_evaluator import run_factor_ic_evaluation
                ic_results = run_factor_ic_evaluation(db, lookback_days=30)
                if ic_results:
                    logger.info(f"[EvoScheduler] 因子IC评估完成: {len(ic_results)} 个因子")
            except Exception as _ic_err:
                logger.error(f"[EvoScheduler] 因子IC评估异常: {_ic_err}", exc_info=True)

            # S4-C：按成交性质(scalp/swing/trend)分流评估因子 IC，供中长线健康视图。
            try:
                from backend.services.factor_ic_evaluator import run_factor_ic_evaluation_segmented
                seg = run_factor_ic_evaluation_segmented(db, lookback_days=45)
                if seg:
                    logger.info(
                        "[EvoScheduler] 因子IC分流评估: "
                        + ", ".join(f"{k}={len(v)}" for k, v in seg.items())
                    )
            except Exception as _seg_err:
                logger.debug(f"[EvoScheduler] 因子IC分流评估跳过: {_seg_err}")

            # S4-C：中长线 AI 辅助因子挖掘（OpenCode 受控生成，4h），
            # 由 factor_backtest_scorer 在对应时间框架样本外打分晋升（内部 23h 节流）。
            try:
                from backend.config.settings import MIDLONG_FACTOR_RESEARCH_ENABLED
                if MIDLONG_FACTOR_RESEARCH_ENABLED:
                    from backend.services.factor_discovery import factor_discovery_engine
                    ml_disc = factor_discovery_engine.run_discovery(
                        db, horizon="midlong", interval="4h",
                    )
                    if ml_disc.get("validated"):
                        logger.info(
                            f"[EvoScheduler] 中长线因子挖掘: "
                            f"validated={len(ml_disc['validated'])}"
                        )
            except Exception as _mld_err:
                logger.debug(f"[EvoScheduler] 中长线因子挖掘跳过: {_mld_err}")

            # S4-A：一次性把 Alpha101 公式因子库灌成中长线候选（幂等），
            # 随后由下方准入闸门在 4h/1d 上样本外打分晋升。
            try:
                from backend.config.settings import MIDLONG_FACTOR_RESEARCH_ENABLED
                if MIDLONG_FACTOR_RESEARCH_ENABLED and not getattr(self, "_alpha101_seeded", False):
                    from backend.services.factor_engine.alpha101_factors import seed_alpha101
                    seed = seed_alpha101()
                    self._alpha101_seeded = True
                    logger.info(f"[EvoScheduler] Alpha101 灌库: 登记{seed.get('registered')}")
            except Exception as _a101_err:
                logger.debug(f"[EvoScheduler] Alpha101 灌库跳过: {_a101_err}")

            # [2026-08-14 弹药扩源] registry Python 类因子（ai_generated/legacy_compat）
            # 登记为中线引用候选 + 每日排队扫描打分（4h/1d，复用闸门引擎）。
            # 扫描是重活（84 条 × 3 币），走 factor_job_manager single-flight。
            try:
                from backend.config.settings import MIDLONG_FACTOR_RESEARCH_ENABLED
                if MIDLONG_FACTOR_RESEARCH_ENABLED:
                    from backend.services.factor_engine.midlong_registry_factors import (
                        seed_registry_candidates,
                    )
                    from backend.services.factor_engine.factor_jobs import (
                        run_scan_registry_midlong,
                    )
                    if not getattr(self, "_registry_seeded", False):
                        _rseed = seed_registry_candidates(["4h", "1d"])
                        self._registry_seeded = True
                        logger.info(
                            f"[EvoScheduler] registry 中线候选登记: {_rseed.get('registered')}"
                        )
                    _scan_job = run_scan_registry_midlong(limit=200)
                    logger.info(
                        f"[EvoScheduler] 中线 registry 扫描已排队（single-flight job={_scan_job.id}）"
                    )
            except Exception as _rs_err:
                logger.debug(f"[EvoScheduler] registry 中线扫描跳过: {_rs_err}")

            # 阶段二 2.2/2.3：发现因子准入闸门 —— 给候选公式因子做样本外回测打分，
            # A/B 级晋升为 active（进入短线活跃因子集），其余淘汰。定时兜底，确保
            # 任何来源登记的候选都会被闸门过一遍，而不是永远停在 candidate。
            # [2026-08-14 P1-H4 修复] 原为同步直调 validate_all_candidates：与 API
            # /validate 的 factor_job_manager 后台单飞队列、两活跃集 recheck 无互斥，
            # 可同日并发对同批候选重复打分写状态。改走 factor_job_manager
            # （single-flight 单飞：已有 pending/running 同类任务时直接复用）。
            try:
                from backend.services.factor_engine.factor_jobs import run_validate_candidates
                job = run_validate_candidates(limit=60)
                logger.info(
                    f"[EvoScheduler] 因子发现闸门已排队（single-flight job={job.id}）"
                )
            except Exception as _fs_err:
                logger.error(f"[EvoScheduler] 因子发现闸门排队异常: {_fs_err}", exc_info=True)

            # 阶段二 2.4：活跃因子衰减复检 —— IC 衰减到阈值以下自动降权/退役。
            try:
                from backend.services.factor_engine.scalp_active_factor_set import (
                    scalp_active_factor_set,
                )
                prune = scalp_active_factor_set.recheck_and_prune()
                if prune.get("checked"):
                    logger.info(
                        f"[EvoScheduler] 活跃因子衰减复检: 检查{prune['checked']} "
                        f"退役{prune['retired']}"
                    )
            except Exception as _pr_err:
                logger.debug(f"[EvoScheduler] 活跃因子衰减复检跳过: {_pr_err}")

            # [2026-08-13 P0-2] 分数-胜率校准：用 scalp_signal_log 已结算样本分桶
            # 重估门槛与高分段条件，结果写 data/scalp_calibration.json 供 Router 读。
            try:
                from backend.services.scalp.scalp_score_calibration import calibrate
                _calib = calibrate()
                if _calib.get("enabled") and _calib.get("threshold"):
                    logger.info(
                        "[EvoScheduler] 分数-胜率校准: n=%s threshold=%s high_score_ok=%s",
                        _calib.get("n_samples"), _calib.get("threshold"),
                        _calib.get("high_score_ok"),
                    )
                elif _calib.get("enabled"):
                    logger.warning("[EvoScheduler] 分数-胜率校准无有效门槛: %s", _calib)
            except Exception as _cal_err:
                logger.debug(f"[EvoScheduler] 分数-胜率校准跳过: {_cal_err}")

            # S4 基座：中长线活跃因子集在 4h/1d 上的衰减复检退役（与短线独立）。
            try:
                from backend.services.factor_engine.midlong_active_factor_set import (
                    midlong_active_factor_set,
                )
                ml_prune = midlong_active_factor_set.recheck_and_prune()
                if ml_prune.get("checked"):
                    logger.info(
                        f"[EvoScheduler] 中长线活跃因子复检: 检查{ml_prune['checked']} "
                        f"退役{ml_prune['retired']} 降级{ml_prune.get('reduced', 0)}"
                    )
            except Exception as _mlpr_err:
                logger.debug(f"[EvoScheduler] 中长线活跃因子复检跳过: {_mlpr_err}")

            # M9: 套利中心接入学习进化 — 每日生成进化提案并在 Paper 自动应用，
            # 满 7 天后对照绩效决定留用/回滚（live 仍需人工确认）
            try:
                from backend.services.rebate_arb.rebate_strategy_evolver import (
                    rebate_strategy_evolver,
                )
                from backend.services.rebate_arb.proposal_auto_applier import (
                    run_auto_apply_cycle,
                )
                gen = rebate_strategy_evolver.generate_proposals()
                cycle = run_auto_apply_cycle()
                logger.info(
                    f"[EvoScheduler] 套利提案闭环: 生成{gen.get('count', 0)} "
                    f"评估{cycle['evaluated']} 应用{cycle['applied']}"
                )
            except Exception as _arb_err:
                logger.error(f"[EvoScheduler] 套利提案闭环异常: {_arb_err}", exc_info=True)

            # 信号权重更新后刷新交易记忆 RAG 索引
            self._trigger_rag_index(db, "trade_memory")
        except Exception as e:
            logger.error(f"[EvoScheduler] 信号权重更新异常: {e}", exc_info=True)
        finally:
            db.close()

    def weekly_experience_distill(self):
        """每周经验提炼：从 decision_snapshots 提炼成功/失败模式写入 StrategyMemory.key_lessons

        跨库注意：DecisionSnapshot 在 analytics 库，StrategyMemory 在主库，
        必须分别用 AnalyticsSessionLocal / SessionLocal，不能共用一个会话。
        """
        # [2026-08-07 v6 fix] 后台线程无 HTTP 身份：unified_learning 为系统策略
        # 建占位父行会 INSERT ai_strategies，被 RLS 拒绝导致每周经验提炼整体
        # 中断（日志反复出现 InsufficientPrivilege 行级安全策略）。
        # 模式同 s1-inject：set_system_identity() 穿透 RLS。
        from backend.core.tenant import set_system_identity
        set_system_identity()
        ana_db = AnalyticsSessionLocal()
        db = SessionLocal()
        try:
            from backend.database.models import DecisionSnapshot, StrategyMemory
            from datetime import timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(days=3)

            snapshots = ana_db.query(DecisionSnapshot).filter(
                DecisionSnapshot.timestamp >= cutoff,
                DecisionSnapshot.pnl.isnot(None),
            ).all()

            if len(snapshots) < 5:
                logger.info("[EvoScheduler] 本周决策快照不足5条，跳过经验提炼")
                return

            strategy_groups: dict = {}
            for snap in snapshots:
                sid = snap.strategy_id or "global"
                strategy_groups.setdefault(sid, []).append(snap)

            distilled_count = 0
            for sid, snaps in strategy_groups.items():
                wins = [s for s in snaps if (s.pnl or 0) > 0]
                losses = [s for s in snaps if (s.pnl or 0) <= 0]
                total = len(snaps)
                wr = len(wins) / total if total > 0 else 0

                new_lessons = []

                if wr < 0.35 and total >= 5:
                    top_loss_regimes = {}
                    for s in losses:
                        r = s.regime_at_decision or "unknown"
                        top_loss_regimes[r] = top_loss_regimes.get(r, 0) + 1
                    worst_regime = max(top_loss_regimes, key=top_loss_regimes.get) if top_loss_regimes else "unknown"
                    new_lessons.append({
                        "type": "weekly_low_wr",
                        "severity": "high",
                        "message": f"本周胜率仅{wr:.0%}({total}笔), {worst_regime}行情下亏损最多, 建议减少该行情下的交易",
                    })

                if losses:
                    avg_loss_conf = sum(s.confidence or 0 for s in losses) / len(losses)
                    if avg_loss_conf > 65:
                        new_lessons.append({
                            "type": "weekly_overconfident_loss",
                            "severity": "medium",
                            "message": f"本周亏损交易平均置信度{avg_loss_conf:.0f}%偏高, AI可能过度自信, 建议提高开仓门槛",
                        })

                if wins:
                    win_regimes = {}
                    for s in wins:
                        r = s.regime_at_decision or "unknown"
                        win_regimes[r] = win_regimes.get(r, 0) + 1
                    best_regime = max(win_regimes, key=win_regimes.get) if win_regimes else "unknown"
                    if win_regimes.get(best_regime, 0) >= 3:
                        new_lessons.append({
                            "type": "weekly_strong_regime",
                            "severity": "info",
                            "message": f"本周在{best_regime}行情下表现最好({win_regimes[best_regime]}笔盈利), 可适当增加该行情下的仓位",
                        })

                if not new_lessons:
                    continue

                if sid == "global":
                    continue

                # [2026-07-11 修复] strategy_memories.strategy_id 是 ai_strategies.strategy_id
                # 的外键。原逻辑直接用 decision_snapshots 里的 sid 建行，一旦 sid 是已删除策略
                # 或系统策略（scalp_router/cross_cycle_*等，不在 ai_strategies 表里），
                # db.flush() 会抛 ForeignKeyViolation，导致本轮及后续同批次提炼全部失败、
                # 周度学习闭环长期打断（日志里反复出现的 strategy_memories_strategy_id_fkey）。
                # 复用与 decision_feedback_service 一致的解析逻辑：已知系统策略自动建占位父行，
                # 无法解析的（真正的孤儿id）直接跳过，不再让整批提炼因一个坏 sid 而中断。
                from backend.services.unified_learning_service import unified_learning
                resolved_sid = unified_learning._resolve_strategy_id_for_fk(db, sid)
                if not resolved_sid:
                    logger.debug(
                        f"[EvoScheduler] 跳过经验提炼写入: strategy_id={sid[:20]} "
                        f"不在ai_strategies中且非已知系统策略前缀"
                    )
                    continue
                sid = resolved_sid

                mem = db.query(StrategyMemory).filter(
                    StrategyMemory.strategy_id == sid
                ).first()
                if not mem:
                    # 自动创建 StrategyMemory 记录，避免教训无处存放
                    mem = StrategyMemory(
                        strategy_id=sid,
                        total_trades=0,
                        key_lessons=[],
                    )
                    db.add(mem)
                    db.flush()
                    logger.info(f"[EvoScheduler] 自动创建 StrategyMemory: {sid[:12]}")

                existing = mem.key_lessons or []
                if isinstance(existing, list):
                    combined = existing[-7:] + new_lessons
                else:
                    combined = new_lessons
                mem.key_lessons = combined[-10:]
                mem.updated_at = datetime.now(timezone.utc)
                distilled_count += 1

            if distilled_count:
                db.commit()
                logger.info(f"[EvoScheduler] 每周经验提炼完成: {distilled_count} 个策略更新了教训")
            else:
                logger.info("[EvoScheduler] 每周经验提炼: 无需更新")

            # 经验提炼后触发 RAG 增量索引（决策快照在 analytics 库，策略教训在主库）
            self._trigger_rag_index(ana_db, "trade_decisions")
            self._trigger_rag_index(db, "strategy_lessons")

            # S7: 批量提取成功模式模板（PatternExtractor.extract_all_eligible）
            try:
                from backend.services.pattern_extractor import pattern_extractor
                extracted = pattern_extractor.extract_all_eligible(db)
                if extracted:
                    logger.info(
                        f"[EvoScheduler] PatternExtractor 批量提取: "
                        f"{len(extracted)} 个模板"
                    )
            except Exception as _pe_err:
                logger.debug(f"[EvoScheduler] PatternExtractor 批量提取跳过: {_pe_err}")

            # ── 策略库模板评级更新（混合路线Step 4）──
            try:
                from backend.services.strategy_library import strategy_library
                rating_result = strategy_library.update_ratings(db)
                if rating_result.get("updated"):
                    logger.info(
                        f"[EvoScheduler] 策略库评级更新: {rating_result['updated']} 个模板, "
                        f"champions={rating_result.get('champions', [])}, "
                        f"deactivated={rating_result.get('deactivated', [])}"
                    )
            except Exception as _sr_err:
                logger.debug(f"[EvoScheduler] 策略库评级更新跳过: {_sr_err}")

        except Exception as e:
            logger.error(f"[EvoScheduler] 每周经验提炼异常: {e}", exc_info=True)
            db.rollback()
        finally:
            ana_db.close()
            db.close()

    # ------------------------------------------------------------------
    #  RAG 增量索引触发（嵌入已有定时任务链）
    # ------------------------------------------------------------------

    def _trigger_rag_index(self, db, source_type: str):
        """安全触发 RAG 增量索引，不影响主流程"""
        try:
            from backend.services.rag_knowledge_service import rag_knowledge_service
            if rag_knowledge_service.is_ready:
                count = rag_knowledge_service.index_from_db(db, source_type, incremental=True)
                logger.info(f"[EvoScheduler] RAG 增量索引 {source_type}: {count} 条")
        except Exception as e:
            logger.debug(f"[EvoScheduler] RAG 索引 {source_type} 跳过: {e}")

    def weekly_rag_full_reindex(self):
        """每周全量重建 RAG 索引（防止增量漂移）

        跨库注意：trade_decisions 源在 analytics 库，
        strategy_lessons / trading_wisdom / trade_memory 源在主库，
        必须分会话索引；共用一个会话会在 PG 三库部署下 UndefinedTable。
        """
        ana_db = AnalyticsSessionLocal()
        main_db = SessionLocal()
        try:
            from backend.services.rag_knowledge_service import (
                rag_knowledge_service,
                COLL_TRADE_DECISIONS,
                COLL_STRATEGY_LESSONS,
                COLL_TRADING_WISDOM,
                COLL_TRADE_MEMORY,
            )
            if not rag_knowledge_service.is_ready:
                logger.info("[EvoScheduler] RAG 服务未就绪，跳过全量重建")
                return
            counts = {}
            counts[COLL_TRADE_DECISIONS] = rag_knowledge_service.index_from_db(
                ana_db, COLL_TRADE_DECISIONS, incremental=False
            )
            counts[COLL_STRATEGY_LESSONS] = rag_knowledge_service.index_from_db(
                main_db, COLL_STRATEGY_LESSONS
            )
            counts[COLL_TRADING_WISDOM] = rag_knowledge_service.index_from_db(
                main_db, COLL_TRADING_WISDOM
            )
            counts[COLL_TRADE_MEMORY] = rag_knowledge_service.index_from_db(
                main_db, COLL_TRADE_MEMORY
            )
            counts["static"] = rag_knowledge_service.index_static_knowledge()
            logger.info(f"[EvoScheduler] RAG 全量重建完成: {counts}")
        except Exception as e:
            logger.error(f"[EvoScheduler] RAG 全量重建异常: {e}", exc_info=True)
        finally:
            ana_db.close()
            main_db.close()

    def cross_exchange_scan(self):
        """每5分钟扫描跨交易所价差和资金费率套利机会。"""
        try:
            import asyncio as _aio
            from backend.services.exchange.exchange_manager import get_exchange_manager

            mgr = get_exchange_manager()
            if len(mgr.get_all_clients()) < 2:
                return

            loop = _aio.new_event_loop()
            try:
                rates = loop.run_until_complete(
                    mgr.get_cross_exchange_funding_rates()
                )
            finally:
                loop.close()

            if len(rates) < 2:
                return

            all_syms: set = set()
            for er in rates.values():
                all_syms.update(er.keys())

            opportunities = []
            for sym in all_syms:
                vals = {ex: r for ex, r in ((e, rates[e].get(sym)) for e in rates) if r is not None}
                if len(vals) < 2:
                    continue
                max_ex = max(vals, key=vals.get)
                min_ex = min(vals, key=vals.get)
                spread = vals[max_ex] - vals[min_ex]
                if abs(spread) > 0.0005:
                    opportunities.append({
                        "symbol": sym,
                        "long_exchange": min_ex,
                        "short_exchange": max_ex,
                        "rate_spread": round(spread, 6),
                        "annualized_pct": round(spread * 3 * 365 * 100, 2),
                    })

            if opportunities:
                opportunities.sort(key=lambda x: abs(x["rate_spread"]), reverse=True)
                logger.info(
                    "[CrossExchange] Found %d funding rate opportunities, top: %s %.4f%%",
                    len(opportunities),
                    opportunities[0]["symbol"],
                    opportunities[0]["rate_spread"] * 100,
                )
        except Exception as e:
            logger.warning("[CrossExchange] scan error: %s", e)

    def hypothesis_scan(self):
        """每6小时触发一次LLM假设生成 → 回测验证 → 自动晋升"""
        if self._running_evolution:
            logger.info("[EvoScheduler] 进化运行中，假设扫描延迟")
            return

        db = SessionLocal()
        try:
            from backend.services.strategy_hypothesis_engine import get_hypothesis_engine
            from backend.services.market_regime import MarketRegimeClassifier
            from backend.services.market_data import get_kline_data

            engine = get_hypothesis_engine()
            regime_clf = MarketRegimeClassifier()

            # 构建市场上下文
            context: dict = {"regime": "unknown", "factor_snapshot": {}, "recent_performance": {}}
            try:
                klines = get_kline_data("BTC", "1h", limit=100)
                if klines and len(klines) > 20:
                    regime_result = regime_clf.classify(klines)
                    context["regime"] = getattr(regime_result, "regime", "unknown") if regime_result else "unknown"
            except Exception as _re:
                logger.debug(f"[EvoScheduler] regime detection failed: {_re}")

            symbols = ["BTC", "ETH"]
            result = engine.run_full_cycle(context, symbols=symbols, db=db)

            logger.info(
                f"[EvoScheduler] 假设扫描完成: "
                f"generated={result.get('generated', 0)}, "
                f"passed={result.get('passed', 0)}, "
                f"promoted={result.get('promoted', 0)}"
            )

        except Exception as e:
            logger.error(f"[EvoScheduler] 假设扫描异常: {e}", exc_info=True)
        finally:
            db.close()

    # 紧急进化 24h 冷却（与 SystemCoordinator.EVOLUTION_COOLDOWN_SECONDS 对齐）
    EMERGENCY_COOLDOWN_SECONDS: int = 24 * 3600

    def trigger_emergency_evolution(self, template_id: str, reason: str = "紧急"):
        """紧急触发特定模板的重新进化（如连续亏损时）。

        返回：
            {"started": True, "template_id": ...}            — 已启动后台线程
            {"started": False, "reason": "running"}          — 已有进化在跑
            {"started": False, "reason": "cooldown", "remain_s": int}
            {"started": False, "reason": "exception", "error": str}

        协调器侧依此将 "emergency_evolution" 写入 triggered / skipped。
        """
        # [2026-08-14 P1-H1 修复] 检查+置位同处临界区（原锁外检查存在 TOCTOU：
        # 两个紧急触发可并发通过检查、各起一个进化线程）。
        with self._lock:
            if self._running_evolution:
                logger.info(f"[EvoScheduler] 进化正在运行，紧急进化 {template_id} 被拒绝")
                return {"started": False, "reason": "running"}
            self._running_evolution = True

        # —— 24h 冷却（P1-4 持久化层）——
        # [2026-08-14 P1-H2 修复] 冷却改用 evolution_events 表最近一次
        # evolution_type='emergency' 的记录；原实现读 SystemCoordinatorState.
        # last_evolution_at —— 该字段同时被 weekly 进化落库写入（persist_genetic_result），
        # 一次 weekly 完成后 24h 内紧急进化被误拒（冷却被 weekly 抢占/污染）。
        try:
            from backend.database.connection import SessionLocal as _SL
            from backend.database.models import EvolutionEvent as _EE
            _db = _SL()
            try:
                _last_emerg = (
                    _db.query(_EE.created_at)
                    .filter(_EE.evolution_type == "emergency")
                    .order_by(_EE.created_at.desc())
                    .first()
                )
                if _last_emerg is not None and _last_emerg[0] is not None:
                    _last = _last_emerg[0]
                    if getattr(_last, "tzinfo", None) is None:
                        from datetime import timezone as _tz
                        _last = _last.replace(tzinfo=_tz.utc)
                    from datetime import datetime as _dt, timezone as _tz
                    elapsed = (_dt.now(_tz.utc) - _last).total_seconds()
                    if elapsed < self.EMERGENCY_COOLDOWN_SECONDS:
                        remain = int(self.EMERGENCY_COOLDOWN_SECONDS - elapsed)
                        logger.info(
                            f"[EvoScheduler] 紧急进化冷却中 {template_id} "
                            f"已过{int(elapsed)}s，剩余{remain}s"
                        )
                        with self._lock:
                            self._running_evolution = False   # 释放预留位
                        return {"started": False, "reason": "cooldown", "remain_s": remain}
            finally:
                _db.close()
        except Exception as e:
            logger.warning(f"[EvoScheduler] 冷却检查失败(放行): {e}")

        logger.warning(f"[EvoScheduler] 紧急进化触发: {template_id} 原因: {reason}")
        try:
            threading.Thread(
                target=self._run_emergency_evolution,
                args=(template_id, reason,),
                daemon=True,
                name=f"emerg-evo-{template_id[:20]}",
            ).start()
            return {"started": True, "template_id": template_id, "reason": reason}
        except Exception as e:
            with self._lock:
                self._running_evolution = False   # 起线程失败必须释放预留位
            logger.error(f"[EvoScheduler] 无法启动紧急进化线程: {e}", exc_info=True)
            return {"started": False, "reason": "exception", "error": str(e)}

    def _run_emergency_evolution(self, template_id: str, reason: str = ""):
        """在后台线程执行紧急进化（使用 GeneticOptimizer 小规模快速搜索）

        支持特殊值:
        - "all_new": 进化所有来源为 'promoted' 的新模板
        - 其他: 按具体 template_id 进化单个模板
        """
        with self._lock:
            self._running_evolution = True

        # strategy_templates 在主库（同 weekly_evolution 的会话修复）
        db = SessionLocal()
        try:
            from backend.database.models import StrategyTemplate
            from backend.services.genetic_optimizer import GeneticOptimizer, Individual
            from backend.services.strategy_evolver import StrategyEvolver

            if template_id == "all_new":
                targets = db.query(StrategyTemplate).filter(
                    StrategyTemplate.is_active == True,
                    StrategyTemplate.source == "promoted",
                ).order_by(StrategyTemplate.created_at.desc()).limit(5).all()
                target_ids = [t.template_id for t in targets]
            else:
                target_ids = [template_id]

            if not target_ids:
                logger.info("[EvoScheduler] 无新晋升模板需要紧急进化")
                return

            evolver = StrategyEvolver()
            optimizer = GeneticOptimizer()

            for tid in target_ids:
                try:
                    param_ranges = _get_full_param_ranges()

                    # v3 整改: 从父模板 strategy_config 抽取 seed_genome，加速收敛 +
                    #         为后续 parent_strategy_id 血缘追踪铺路
                    seed_genome: Optional[dict] = None
                    try:
                        _parent_tpl = db.query(StrategyTemplate).filter(
                            StrategyTemplate.template_id == tid
                        ).first()
                        if _parent_tpl and isinstance(_parent_tpl.strategy_config, dict):
                            seed_genome = {
                                k: v for k, v in _parent_tpl.strategy_config.items()
                                if k in param_ranges and isinstance(v, (int, float))
                            } or None
                    except Exception as _seed_err:
                        logger.debug(f"[EvoScheduler] seed_genome 提取失败(放行) {tid}: {_seed_err}")

                    def _make_fitness(tpl_id, _evolver, _db):
                        def fitness_fn(genome: dict) -> Individual:
                            try:
                                tpl = _db.query(StrategyTemplate).filter(
                                    StrategyTemplate.template_id == tpl_id
                                ).first()
                                if tpl:
                                    result = _evolver._run_single_backtest_for_genome(
                                        template=tpl, genome=genome, db=_db
                                    )
                                    if result:
                                        score = StrategyEvolver.composite_fitness(result)
                                        return Individual(
                                            genome=genome, fitness=score,
                                            sharpe=float(result.get("sharpe", 0)),
                                            max_drawdown=float(result.get("max_drawdown", 1)),
                                            total_trades=int(result.get("total_trades", 0)),
                                        )
                            except Exception:
                                pass
                            return Individual(
                                genome=genome, fitness=0, sharpe=-1,
                                max_drawdown=1, total_trades=0,
                            )
                        return fitness_fn

                    result = optimizer.evolve(
                        template_id=str(tid),
                        param_ranges=param_ranges,
                        fitness_fn=_make_fitness(tid, evolver, db),
                        is_emergency=True,
                        seed_genome=seed_genome,  # v3 整改
                    )
                    if result and result.best_fitness > 0:
                        # P0-6：紧急进化 GA 成功分支同样落库 + 晋升 + 写 last_evolution_at
                        tpl_obj = db.query(StrategyTemplate).filter(
                            StrategyTemplate.template_id == tid
                        ).first()
                        champion = None
                        if tpl_obj is not None:
                            champion = evolver.persist_genetic_result(
                                db=db,
                                tpl=tpl_obj,
                                best_genome=result.best_genome,
                                generations_run=result.generations_run,
                                parent_run_id=None,
                            )
                        logger.info(
                            f"[EvoScheduler] 紧急进化完成: {tid} "
                            f"fitness={result.best_fitness:.3f} Sharpe={result.best_sharpe:.2f} "
                            f"promoted={bool(champion and champion.get('promoted'))}"
                        )

                        # ── 记录 evolution_events ──
                        import json as _json
                        self._record_evolution_event(
                            evolution_type="emergency",
                            trigger_reason=reason or "all_new",
                            template_count=1,
                            promoted_count=1 if (champion and champion.get("promoted")) else 0,
                            best_fitness=float(result.best_fitness),
                            best_sharpe=float(result.best_sharpe),
                            best_max_drawdown=float(getattr(result, "best_max_drawdown", 0.0) or 0.0),
                            success=True,
                        )

                        # ── 喂入 QAABridge 进化历史 ──
                        try:
                            from backend.services.qaa_evolution_bridge import QAABridge
                            bridge = QAABridge.get_instance()
                            if bridge._enabled and bridge.history:
                                bridge.history.append(
                                    domain="trading",
                                    event="emergency_evolution",
                                    details={
                                        "template_id": tid,
                                        "best_fitness": float(result.best_fitness),
                                        "best_sharpe": float(result.best_sharpe),
                                        "promoted": bool(champion and champion.get("promoted")),
                                    },
                                )
                        except Exception as _qaa_err:
                            logger.debug(f"[EvoScheduler] QAA emergency feed skipped: {_qaa_err}")

                        try:
                            from backend.services.ws_broadcast import ws_broadcast_hub
                            ws_broadcast_hub.broadcast_evolution_update({
                                "template_id": tid,
                                "best_fitness": float(result.best_fitness),
                                "best_sharpe": float(result.best_sharpe),
                                "best_max_drawdown": float(getattr(result, "best_max_drawdown", 0.0) or 0.0),
                                "generations": int(getattr(result, "generations_run", 0) or 0),
                                "mode": "emergency",
                                "status": "completed",
                                "promoted": bool(champion and champion.get("promoted")),
                            })
                        except Exception:
                            pass
                    else:
                        evolver.run_evolution(
                            db=db, template_id=tid,
                            generations=10, population_size=10,
                        )
                        logger.info(f"[EvoScheduler] 紧急进化（降级模式）完成: {tid}")
                        try:
                            from backend.services.ws_broadcast import ws_broadcast_hub
                            ws_broadcast_hub.broadcast_evolution_update({
                                "template_id": tid,
                                "mode": "emergency_fallback",
                                "status": "completed",
                            })
                        except Exception:
                            pass
                except Exception as e:
                    # 之前是 warning，会让 GA 失败看起来像"跑完了"。升 error+exc_info，方便排障
                    logger.error(
                        f"[EvoScheduler] 紧急进化 {tid} 失败: {e}", exc_info=True
                    )
                    # P0 修复（2026-07-20）：单模板失败后必须 rollback session，
                    # 否则 session 进入 PendingRollback 状态，后续 4 个模板会全部
                    # 报 "Can't reconnect until invalid transaction is rolled back"，
                    # 24h 内观测到 165 次重复错误。每个模板失败后立即 rollback，
                    # 让下一个模板能在一个干净的事务上继续。
                    try:
                        db.rollback()
                    except Exception as _rb_err:
                        logger.debug(f"[EvoScheduler] rollback after failure {tid}: {_rb_err}")
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub
                        ws_broadcast_hub.broadcast_evolution_update({
                            "template_id": tid,
                            "mode": "emergency",
                            "status": "failed",
                            "error": str(e),
                        })
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"[EvoScheduler] 紧急进化外层异常: {e}", exc_info=True)
        finally:
            self._running_evolution = False
            try:
                db.close()
            except Exception:
                pass
            logger.info("[EvoScheduler] 紧急进化线程结束，_running_evolution=False")

    def _record_evolution_event(
        self,
        evolution_type: str,
        template_count: int = 0,
        promoted_count: int = 0,
        best_fitness: float = 0,
        best_sharpe: float = 0,
        best_profit_factor: float = 0,
        best_max_drawdown: float = 0,
        objectives_json: str = None,
        success: bool = True,
        error_message: str = None,
        duration_seconds: float = None,
        trigger_reason: str = None,
    ):
        """写入 evolution_events 记录（2026-06-22 新增）。"""
        try:
            from backend.database.connection import SessionLocal as _SL
            from backend.database.models import EvolutionEvent
            _db = _SL()
            try:
                event = EvolutionEvent(
                    evolution_type=evolution_type,
                    trigger_reason=trigger_reason or "",
                    template_count=template_count,
                    promoted_count=promoted_count,
                    best_fitness=best_fitness,
                    best_sharpe=best_sharpe,
                    best_profit_factor=best_profit_factor,
                    best_max_drawdown=best_max_drawdown,
                    objectives_json=objectives_json,
                    success=success,
                    error_message=error_message,
                    duration_seconds=duration_seconds,
                )
                _db.add(event)
                _db.commit()
                logger.info(
                    f"[EvoScheduler] 进化事件已记录: type={evolution_type} "
                    f"success={success} fitness={best_fitness:.3f} promoted={promoted_count}"
                )
                
                # Phase 2 整合: 将进化关键发现写入统一知识池
                if promoted_count > 0 and best_fitness > 0:
                    try:
                        from backend.services.opencode_action_router import (
                            unified_knowledge_ingest, KnowledgeItem,
                        )
                        items = [KnowledgeItem(
                            source="evolution",
                            category="param_wisdom",
                            severity="info",
                            title=f"NSGA-II {evolution_type} evolution: {promoted_count} promoted",
                            finding_json={
                                "evolution_type": evolution_type,
                                "promoted_count": promoted_count,
                                "best_fitness": best_fitness,
                                "best_sharpe": best_sharpe,
                                "best_profit_factor": best_profit_factor,
                                "best_max_drawdown": best_max_drawdown,
                            },
                        )]
                        unified_knowledge_ingest(_db, items, trigger_rag_index=False)
                    except Exception as _uk_err:
                        logger.debug(f"[EvoScheduler] 统一知识池写入跳过: {_uk_err}")
            finally:
                _db.close()
        except Exception as e:
            logger.warning(f"[EvoScheduler] 进化事件记录失败: {e}")


evolution_scheduler = EvolutionScheduler()


def register_evolution_tasks():
    """注册进化相关的定时任务到全局调度器"""
    try:
        from backend.services.scheduler import task_scheduler

        if not task_scheduler.is_running():
            task_scheduler.start()

        CYCLE_SECONDS = 3 * 24 * 3600   # 7×24h 运行，3天一个进化周期
        DAY_SECONDS = 24 * 3600

        # [2026-08-13 P1-9] 进化节奏分档：短线周期（1m/5m/15m）每日一次——短线
        # regime 切换快于因子更新，3 天周期跟不上；中长线保持 3 天 CYCLE_SECONDS。
        # 5m 已由 main.py cron 每日 04:00 注册（task_id=factor_evolution_scalp_5m_daily），
        # 这里补 1m/15m；env FACTOR_EVO_SCALP_PERIODS 可配（空串 = 全部关闭）。
        try:
            from backend.services.evolution.factor_evolution_loop import (
                run_factor_evolution_loop as _run_factor_evo,
            )
            _scalp_periods = [
                _s.strip() for _s in
                (_os.getenv("FACTOR_EVO_SCALP_PERIODS", "1m,15m") or "").split(",")
                if _s.strip()
            ]
            for _per in _scalp_periods:
                def _run_scalp_evo(_per=_per):
                    try:
                        return _run_factor_evo(period=_per, quick=False, source="scalp_daily_sched")
                    except Exception as _e:
                        logger.warning("[EvoScheduler] 短线 %s 每日进化失败: %s", _per, _e)
                        return {"error": str(_e)[:150]}
                task_scheduler.add_interval_task(
                    task_func=_run_scalp_evo,
                    interval_seconds=DAY_SECONDS,
                    task_id=f"factor_evolution_scalp_{_per}_daily_evo",
                    max_instances=1,
                )
                logger.info("[EvoScheduler] 已注册短线 %s 每日进化任务", _per)
        except Exception as _e:
            logger.warning("[EvoScheduler] 短线周期每日进化注册失败: %s", _e)

        task_scheduler.add_interval_task(
            task_func=evolution_scheduler.weekly_evolution,
            interval_seconds=CYCLE_SECONDS,
            task_id="evolution_cycle_auto",
        )
        logger.info("[EvoScheduler] 已注册每3天自动进化任务")

        task_scheduler.add_interval_task(
            task_func=evolution_scheduler.daily_wisdom_refresh,
            interval_seconds=DAY_SECONDS,
            task_id="evolution_daily_wisdom",
        )
        logger.info("[EvoScheduler] 已注册每日智慧刷新任务")

        # weekly_prompt_review 已移交 OpenCode strategy_deep_dive（V1 双轨已移除，强制 V2）
        logger.info(
            "[EvoScheduler] weekly_prompt_review 已移交 OpenCode strategy_deep_dive，跳过注册"
        )

        task_scheduler.add_interval_task(
            task_func=evolution_scheduler.weekly_data_decay,
            interval_seconds=CYCLE_SECONDS,
            task_id="evolution_cycle_decay",
        )
        logger.info("[EvoScheduler] 已注册每3天数据衰减任务")

        task_scheduler.add_interval_task(
            task_func=evolution_scheduler.daily_signal_weight_update,
            interval_seconds=DAY_SECONDS,
            task_id="signal_weight_daily_update",
        )
        logger.info("[EvoScheduler] 已注册每日信号权重更新任务")

        # 短线信号日志结算（元标签数据采集）：每 5 分钟回填到期信号的输赢
        def _settle_scalp_signals():
            try:
                from backend.services.scalp_signal_logger import settle_pending
                settle_pending(limit=800)
            except Exception as _e:
                logger.debug(f"[EvoScheduler] scalp 信号结算跳过: {_e}")

        task_scheduler.add_interval_task(
            task_func=_settle_scalp_signals,
            interval_seconds=300,
            task_id="scalp_signal_settle",
        )
        logger.info("[EvoScheduler] 已注册短线信号结算任务(5min)")

        # 短线元标签模型自动训练+验证：每天一次；样本不足自动跳过，达标才标记 usable
        def _train_scalp_meta():
            try:
                from backend.services.scalp_meta_trainer import train_and_validate
                train_and_validate()
            except Exception as _e:
                logger.debug(f"[EvoScheduler] scalp 元标签训练跳过: {_e}")

        task_scheduler.add_interval_task(
            task_func=_train_scalp_meta,
            interval_seconds=DAY_SECONDS,
            task_id="scalp_meta_train_daily",
        )
        logger.info("[EvoScheduler] 已注册短线元标签每日训练任务")

        task_scheduler.add_interval_task(
            task_func=evolution_scheduler.weekly_experience_distill,
            interval_seconds=CYCLE_SECONDS,
            task_id="cycle_experience_distill",
        )
        logger.info("[EvoScheduler] 已注册每3天经验提炼任务")

        HYPOTHESIS_SECONDS = 6 * 3600  # 每6小时
        task_scheduler.add_interval_task(
            task_func=evolution_scheduler.hypothesis_scan,
            interval_seconds=HYPOTHESIS_SECONDS,
            task_id="hypothesis_scan_6h",
        )
        logger.info("[EvoScheduler] 已注册每6小时假设扫描任务")

        CROSS_ARB_SECONDS = 5 * 60  # 每5分钟
        task_scheduler.add_interval_task(
            task_func=evolution_scheduler.cross_exchange_scan,
            interval_seconds=CROSS_ARB_SECONDS,
            task_id="cross_exchange_scan_5m",
        )
        logger.info("[EvoScheduler] 已注册每5分钟跨所价差扫描任务")

        WEEK_SECONDS = 7 * 24 * 3600
        task_scheduler.add_interval_task(
            task_func=evolution_scheduler.weekly_rag_full_reindex,
            interval_seconds=WEEK_SECONDS,
            task_id="rag_weekly_reindex",
        )
        logger.info("[EvoScheduler] 已注册每周 RAG 全量重建任务")

        # ══════════════════════════════════════════════════
        #  AI学习系统整合: DRL表现归档 + 协调器检查
        # ══════════════════════════════════════════════════

        # 每天归档DRL表现数据（30天前明细→日聚合）
        def _daily_drl_archive():
            try:
                # DRLPerformance / DRLPerformanceDaily 均为主库模型，用 SessionLocal
                from backend.database.connection import SessionLocal
                from backend.services.unified_learning_service import unified_learning
                db = SessionLocal()
                try:
                    unified_learning.archive_drl_performance(db, days_to_keep=30)
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"[EvoScheduler] DRL归档失败: {e}")

        task_scheduler.add_interval_task(
            task_func=_daily_drl_archive,
            interval_seconds=DAY_SECONDS,
            task_id="drl_performance_daily_archive",
        )
        logger.info("[EvoScheduler] 已注册每日DRL表现归档任务")

        # ══════════════════════════════════════════════════
        #  QAA 进化系统集成: 灰度计划评估 + 指标聚合
        # ══════════════════════════════════════════════════

        QAA_CHECK_SECONDS = 10 * 60  # 每10分钟
        def _qaa_periodic_check():
            try:
                from backend.services.qaa_evolution_bridge import QAABridge
                from backend.database.connection import AnalyticsSessionLocal
                bridge = QAABridge.get_instance()
                if not bridge._enabled:
                    return
                db = AnalyticsSessionLocal()
                try:
                    bridge.check_grayscale_plans(db)
                    bridge.feed_aggregate_metrics(db)
                finally:
                    db.close()
            except Exception as e:
                logger.debug(f"[EvoScheduler] QAA periodic check skipped: {e}")

        task_scheduler.add_interval_task(
            task_func=_qaa_periodic_check,
            interval_seconds=QAA_CHECK_SECONDS,
            task_id="qaa_grayscale_check_10m",
        )
        logger.info("[EvoScheduler] 已注册每10分钟QAA灰度评估任务")

        # ══════════════════════════════════════════════════
        #  P1.5: 分层记忆衰减 + 叙事引擎
        # ══════════════════════════════════════════════════

        def _daily_memory_decay():
            """每日分层记忆衰减扫描：deep记忆365天，shallow记忆30天"""
            try:
                from backend.database.connection import SessionLocal
                from backend.database.models import StrategyMemory
                from backend.services.memory_decay_service import memory_decay_service
                db = SessionLocal()
                try:
                    result = memory_decay_service.run_daily_decay(db)
                    logger.info(
                        f"[EvoScheduler] 记忆衰减完成: "
                        f"expired={result.get('expired', 0)} "
                        f"total_lessons={result.get('total_lessons', 0)}"
                    )
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"[EvoScheduler] 记忆衰减失败: {e}")

        task_scheduler.add_interval_task(
            task_func=_daily_memory_decay,
            interval_seconds=DAY_SECONDS,
            task_id="memory_decay_daily",
        )
        logger.info("[EvoScheduler] 已注册每日分层记忆衰减任务")

        # 每日交易叙事构建已由 OpenCode regime_journal 接管（V1 双轨已移除）
        logger.info("[EvoScheduler] 每日交易叙事由 OpenCode regime_journal 构建")

        # v4 P0-1：协调器循环下沉到 LearningLoopService，这里统一注册三个 tick
        try:
            from backend.services.learning_loop_service import learning_loop
            learning_loop.register_tasks()
            logger.info("[EvoScheduler] LearningLoopService 已通过 register_evolution_tasks 注册")
        except Exception as e:
            logger.error(f"[EvoScheduler] LearningLoop 注册失败: {e}", exc_info=True)

        # 启动时立即在后台线程执行一次关键学习任务
        # 避免频繁重启导致定时任务永远等不到首次执行
        import threading
        def _run_startup_learning():
            import time as _t
            _t.sleep(60)  # 等待 60s 让系统完全就绪
            logger.info("[EvoScheduler] 启动后首次学习任务开始执行...")
            for task_name, task_func in [
                ("daily_wisdom_refresh", evolution_scheduler.daily_wisdom_refresh),
                ("daily_signal_weight_update", evolution_scheduler.daily_signal_weight_update),
                ("weekly_experience_distill", evolution_scheduler.weekly_experience_distill),
                ("hypothesis_scan", evolution_scheduler.hypothesis_scan),
            ]:
                try:
                    task_func()
                    logger.info(f"[EvoScheduler] 启动首次 {task_name} 完成")
                except Exception as _e:
                    logger.warning(f"[EvoScheduler] 启动首次 {task_name} 失败: {_e}")
            logger.info("[EvoScheduler] 启动后首次学习任务全部完成")

            # ── GA 主进化补跑：interval 任务首次触发在进程启动 3 天后，
            #    频繁重启会导致 weekly_evolution 永远等不到执行。
            #    修复（2026-06-22）：不再依赖 SystemCoordinatorState.last_evolution_at
            #    （该字段会被 emergency 进化污染），改为查询 evolution_events 表。
            #    如果 24h 内没有 weekly 类型的成功进化记录，立即补跑一次。──
            try:
                _should_backfill = True
                from backend.database.connection import SessionLocal as _SL
                from backend.database.models import EvolutionEvent
                _mdb = _SL()
                try:
                    _last_weekly = (
                        _mdb.query(EvolutionEvent)
                        .filter(EvolutionEvent.evolution_type == "weekly")
                        .filter(EvolutionEvent.success == True)
                        .order_by(EvolutionEvent.created_at.desc())
                        .first()
                    )
                    if _last_weekly is not None and _last_weekly.created_at is not None:
                        from datetime import datetime as _dt, timezone as _tz
                        _last = _last_weekly.created_at
                        if getattr(_last, "tzinfo", None) is None:
                            _last = _last.replace(tzinfo=_tz.utc)
                        BACKFILL_SECONDS = 24 * 3600  # 24h 内跑过则跳过
                        _should_backfill = (_dt.now(_tz.utc) - _last).total_seconds() > BACKFILL_SECONDS
                finally:
                    _mdb.close()
                if _should_backfill:
                    logger.info("[EvoScheduler] 24h 内无 GA 主进化记录，启动补跑 weekly_evolution")
                    evolution_scheduler.weekly_evolution()
                    logger.info("[EvoScheduler] 启动补跑 weekly_evolution 完成")
                else:
                    logger.info("[EvoScheduler] 24h 内已有 GA 主进化记录，跳过启动补跑")
            except Exception as _e:
                logger.warning(f"[EvoScheduler] 启动补跑 weekly_evolution 失败: {_e}", exc_info=True)

        _t = threading.Thread(target=_run_startup_learning, daemon=True, name="evo-startup")
        _t.start()
        logger.info("[EvoScheduler] 已安排启动后60s首次执行学习任务")

    except Exception as e:
        logger.error(f"[EvoScheduler] 定时任务注册失败: {e}", exc_info=True)
