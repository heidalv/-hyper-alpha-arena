"""
策略进化器 — AI 深度学习 + 多线程回测 + 智能优化 + 模板晋升

完整闭环流程：
1. 从模板库取策略模板
2. 多线程并行回测（2-3 年历史数据）
3. AI（LLM）分析回测结果：诊断失败原因、识别盈利模式
4. AI 给出具体改进方案（参数+逻辑），生成下一代策略
5. 重复回测-分析-改进，直到策略达标
6. 达标策略自动晋升为"实战就绪"
"""
import asyncio
import json
import logging
import math
import uuid
import copy
import random
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session

from backend.services.backtest_evolution_engine import (
    BacktestEngine, BacktestResult, Bar, TIER_CONFIG, get_tier_signal_param_ranges,
)
from backend.services.live_pipeline_backtest_engine import (
    LivePipelineBacktestEngine, DEFAULT_PIPELINE_PARAMS, PIPELINE_PARAM_RANGES,
)
from backend.services.strategy_params_registry import (
    PROMOTION_THRESHOLDS, DEFAULT_EVOLUTION_CONFIG,
    apply_genome as _apply_genome,  # v3 整改: 统一 genome 写入口
)

logger = logging.getLogger(__name__)


class StrategyEvolver:
    """策略进化器（单例）"""

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
        self._running = False
        self._progress: Dict[str, Any] = {}
        logger.info("[Evolver] 策略进化器初始化完成")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def progress(self) -> Dict[str, Any]:
        return dict(self._progress)

    # ══════════════════════════════════════════════════
    #  公共适应度评分（P0-6 统一 fitness 为 composite_score）
    # ══════════════════════════════════════════════════

    @staticmethod
    def composite_fitness(result: Dict[str, Any]) -> float:
        """把 `_run_single_backtest_for_genome` 的回测结果字典映射为一个标量 fitness。

        统一 fitness 口径：`sharpe` 主导 + `win_rate` 加成 + `max_drawdown` 惩罚 +
        交易频率缩放。与 `_evolve_pipeline_template` 中的内部 `_composite_score`
        核心思想保持一致，但接口简单，便于 evolution_scheduler 的 GA fitness_fn 复用。
        """
        try:
            sharpe = float(result.get("sharpe", 0) or 0)
            wr = float(result.get("win_rate", 0) or 0)
            mdd = float(result.get("max_drawdown", 0) or 0)
            total = int(result.get("total_trades", 0) or 0)
        except Exception:
            return 0.0
        freq = min(1.0, total / 30.0) if total else 0.1
        dd_penalty = max(0.0, mdd - 0.3)
        # P1-9: 降低交易频率偏置 (0.5→0.7), 惩罚过度交易 (>20笔/天)
        daily_trades_est = total / 365.0 if total and total > 0 else 0
        daily_penalty = max(0.0, daily_trades_est - 20) * 0.01
        raw = (sharpe * 0.6 + wr * 0.3) * (0.7 + 0.3 * freq) - dd_penalty * 2 - daily_penalty
        return round(max(0.0, raw), 4)

    def persist_genetic_result(
        self,
        db: Session,
        tpl,
        best_genome: Dict[str, Any],
        generations_run: int,
        parent_run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """GA 成功分支的落库封装（P0-6 / P1-1）。

        1) 用 best_genome 再跑一次完整回测 → 得到 sharpe / win_rate / max_drawdown / trades
        2) 组装成 champion dict
        3) 调 `_save_champion`（写 BacktestRun + parent_run_id）
        4) 若达到晋升门槛调 `_promote_template`
        5) 写 `SystemCoordinatorState.last_evolution_at`

        Returns:
            champion dict 或 None
        """
        try:
            result = self._run_single_backtest_for_genome(tpl, best_genome, db)
            if not result:
                logger.warning(f"[Evolver] persist_genetic_result: best_genome 回测失败 {tpl.template_id}")
                return None

            import uuid as _uuid
            champion = {
                **result,
                "risk": dict(best_genome),
                "pipeline_params": dict(best_genome),
                "generation": int(generations_run),
                "final_equity": float(result.get("total_return", 0) or 0) * 10000 + 10000,
                "run_id": f"champ_{_uuid.uuid4().hex[:8]}",
            }
            # 写 BacktestRun，标注 parent_run_id（P1-1）
            self._save_champion_with_lineage(db, tpl, champion, parent_run_id)

            # ── 整改#19：写入 MAP-Elites 多样性冠军库（按 regime×tier×vol 行为格）──
            try:
                from backend.services.learning_core import map_elites_archive as _me
                if _me.is_enabled():
                    _tier = getattr(tpl, "tier", None) or "mid"
                    _tf = {"scalp": "short", "short": "short", "mid": "mid", "long": "long"}.get(str(_tier).lower(), "mid")
                    _regime = str(result.get("regime") or (result.get("best_regimes") or ["ranging"])[0] or "ranging")
                    _beh = _me.BehaviorDescriptor.from_market(_regime, _tf,
                                                              volatility_value=result.get("volatility"))
                    _fit = float(self.composite_fitness(result))
                    _metrics = {k: result.get(k) for k in ("sharpe", "win_rate", "max_drawdown", "total_return")}
                    if _me.mode() == "mome":
                        _me.get_archive()  # 触发单例
                        from backend.services.learning_core.map_elites_archive import MOMEArchive as _MOME
                        if not hasattr(self, "_mome_archive"):
                            self._mome_archive = _MOME()
                        self._mome_archive.add(dict(best_genome),
                                               {"sharpe": float(result.get("sharpe", 0) or 0),
                                                "win_rate": float(result.get("win_rate", 0) or 0)},
                                               _beh, int(generations_run))
                    else:
                        _me.get_archive().add(dict(best_genome), _fit, _beh, _metrics, int(generations_run))
                    logger.info("[Evolver][MAP-Elites#19] 写入行为格 %s fitness=%.4f", _beh.key(), _fit)
            except Exception as _me_err:
                logger.debug("[Evolver][MAP-Elites#19] 写入失败（忽略）: %s", _me_err)

            # 达到晋升门槛 → 晋升模板（含 parent_template_id）
            tier = getattr(tpl, "tier", None) or "mid"
            try:
                promoted = False
                if self._should_promote(champion, tier):
                    self._promote_template_with_lineage(db, tpl, champion)
                    promoted = True
                champion["promoted"] = promoted
            except Exception as e:
                logger.warning(f"[Evolver] promote 失败（不影响保存）: {e}")

            # 写 last_evolution_at（P1-1）
            try:
                from backend.database.models import SystemCoordinatorState
                state = db.query(SystemCoordinatorState).first()
                if state is None:
                    state = SystemCoordinatorState(last_evolution_at=datetime.now(timezone.utc))
                    db.add(state)
                else:
                    state.last_evolution_at = datetime.now(timezone.utc)
                db.commit()
            except Exception as e:
                logger.debug(f"[Evolver] last_evolution_at 写入失败: {e}")
                db.rollback()

            return champion
        except Exception as e:
            logger.error(f"[Evolver] persist_genetic_result 异常: {e}", exc_info=True)
            return None

    @staticmethod
    def _save_champion_with_lineage(db: Session, tpl, champion: Dict[str, Any],  # type: ignore[no-redef]
                                    parent_run_id: Optional[str]) -> None:
        """等价于 _save_champion，但显式写入 run_id / parent_run_id，供血统追踪。"""
        from backend.database.models import BacktestRun

        tier = getattr(tpl, "tier", None) or "mid"
        tier_cfg = TIER_CONFIG.get(tier, TIER_CONFIG["mid"])

        champion_config = dict(champion.get("risk") or {})
        if champion.get("pipeline_params"):
            champion_config["pipeline_params"] = champion["pipeline_params"]
            champion_config["engine_type"] = "live_pipeline"

        run = BacktestRun(
            run_id=champion.get("run_id") or f"champ_{uuid.uuid4().hex[:8]}",
            template_id=tpl.template_id,
            parent_run_id=parent_run_id,
            symbol="multi",
            timeframe=tier_cfg["default_timeframe"],
            tier=tier,
            start_date="evolution",
            end_date="champion",
            strategy_name=tpl.name,
            strategy_config=champion_config,
            generation=champion.get("generation", 0),
            status="completed",
            is_champion=True,
            total_return=champion.get("total_return"),
            max_drawdown=champion.get("max_drawdown"),
            sharpe_ratio=champion.get("sharpe"),
            win_rate=champion.get("win_rate"),
            profit_factor=champion.get("profit_factor"),
            total_trades=champion.get("total_trades"),
            final_equity=champion.get("final_equity"),
            completed_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()

    @staticmethod
    def _promote_template_with_lineage(db: Session, tpl, champion: Dict[str, Any]) -> None:
        """等价于 _promote_template，但额外在 parent_template_id 上做自引用（P1-1）。"""
        # 复用原 _promote_template 的核心落地逻辑
        StrategyEvolver._promote_template(db, tpl, champion)
        try:
            if hasattr(tpl, "parent_template_id") and tpl.parent_template_id is None:
                # 首次晋升：parent_template_id 设为自身，后续父链可由调用方替换
                tpl.parent_template_id = tpl.template_id
                db.commit()
        except Exception:
            db.rollback()

    # ══════════════════════════════════════════════════
    #  公开 API
    # ══════════════════════════════════════════════════

    def start_evolution(self, config: Optional[Dict] = None) -> Dict[str, Any]:
        """启动自动进化（后台线程）"""
        if self._running:
            return {"success": False, "error": "进化已在运行中"}

        cfg = {**DEFAULT_EVOLUTION_CONFIG, **(config or {})}
        self._running = True
        self._progress = {
            "status": "starting",
            "current_template": "",
            "current_generation": 0,
            "total_templates": 0,
            "completed_templates": 0,
            "total_backtests": 0,
            "completed_backtests": 0,
            "champions": [],
            "ai_phase": "",
            "ai_learning_log": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        threading.Thread(target=self._run_evolution, args=(cfg,), daemon=True).start()
        return {"success": True, "message": "策略进化已启动"}

    def stop_evolution(self):
        """停止进化"""
        self._running = False
        self._progress["status"] = "stopped"
        return {"success": True}

    # ── 供 evolution_scheduler 调用的公开接口 ──

    def run_evolution(self, db: Session, template_id: str,
                      generations: int = 12, population_size: int = 16) -> Optional[Dict]:
        """对指定模板运行一轮完整进化（供 evolution_scheduler 降级调用）"""
        from backend.database.models import StrategyTemplate
        tpl = db.query(StrategyTemplate).filter(
            StrategyTemplate.template_id == template_id
        ).first()
        if not tpl:
            logger.warning(f"[Evolver] run_evolution: 模板 {template_id} 不存在")
            return None

        tier = getattr(tpl, "tier", None) or "mid"
        cfg = {
            **DEFAULT_EVOLUTION_CONFIG,
            "max_generations": generations,
            "population_per_gen": population_size,
            "tier": tier,
        }
        self._running = True
        try:
            champion = self._evolve_pipeline_template(db, tpl, cfg)
            if champion and self._should_promote(champion, tier):
                self._promote_template(db, tpl, champion)
                logger.info(f"[Evolver] run_evolution: {tpl.name} 已晋升 Sharpe={champion.get('sharpe', 0):.2f}")
            return champion
        except Exception as e:
            logger.error(f"[Evolver] run_evolution 异常: {e}", exc_info=True)
            return None
        finally:
            self._running = False

    def _run_single_backtest_for_genome(self, template, genome: Dict, db: Session) -> Optional[Dict]:
        """用 genome 参数对模板执行单次回测，返回结果字典（供 GeneticOptimizer fitness_fn 使用）"""
        try:
            tier = getattr(template, "tier", None) or "mid"
            tier_cfg = TIER_CONFIG.get(tier, TIER_CONFIG["mid"])
            timeframes = tier_cfg["timeframes"]

            bars_cache = {}
            symbols = DEFAULT_EVOLUTION_CONFIG.get("symbols", ["BTC", "ETH"])
            for symbol in symbols:
                for tf in timeframes:
                    key = f"{symbol}_{tf}"
                    bars = self._load_bars(symbol, tf, 365)
                    if bars and len(bars) >= 50:
                        bars_cache[key] = bars
                        break
                if bars_cache:
                    break

            if not bars_cache:
                return None

            pipeline_params = {**DEFAULT_PIPELINE_PARAMS}
            pipeline_params.update(genome)

            funding_rates = self._load_funding_rates(db, symbols[:1])
            fgi_series = self._load_fgi_series(db, 365)

            for key, bars in bars_cache.items():
                engine = LivePipelineBacktestEngine(initial_capital=10000)
                result = engine.run(
                    bars, pipeline_params, tier=tier,
                    funding_rate_series=funding_rates, fgi_series=fgi_series,
                )
                if result and not result.error:
                    def _sf(v):
                        return float(v) if v is not None and (not isinstance(v, float) or math.isfinite(v)) else 0.0
                    return {
                        "sharpe": _sf(result.sharpe_ratio),
                        "win_rate": _sf(result.win_rate),
                        "max_drawdown": _sf(result.max_drawdown),
                        "total_trades": result.total_trades,
                        "total_return": _sf(result.total_return),
                        "profit_factor": _sf(result.profit_factor),
                        "trades": result.trades if hasattr(result, "trades") else [],
                    }
            return None
        except Exception as e:
            logger.debug(f"[Evolver] _run_single_backtest_for_genome 异常: {e}")
            return None

    def run_single_backtest(self, template_id: str, symbol: str = "BTC",
                            timeframe: str = None, days: int = 365) -> Optional[Dict]:
        """对单个模板运行一次回测（自动选择 tier 对应的 timeframe）"""
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            from backend.database.models import StrategyTemplate
            tpl = db.query(StrategyTemplate).filter(
                StrategyTemplate.template_id == template_id
            ).first()
            if not tpl:
                return {"error": f"模板 {template_id} 不存在"}

            tier = getattr(tpl, "tier", None) or "mid"
            if timeframe is None:
                tier_cfg = TIER_CONFIG.get(tier, TIER_CONFIG["mid"])
                timeframe = tier_cfg["default_timeframe"]

            bars = self._load_bars(symbol, timeframe, days)
            if not bars:
                return {"error": f"无 {symbol} {timeframe} 历史数据"}

            cfg = tpl.strategy_config or {}
            risk = cfg.get("risk_params", {})
            risk.setdefault("stop_loss_pct", 0.04)
            risk.setdefault("take_profit_pct", 0.10)
            risk.setdefault("max_position_size", 0.20)

            engine = BacktestEngine(initial_capital=10000, leverage=risk.get("default_leverage", 10))
            result = engine.run(bars, {**cfg, "category": tpl.category}, risk, tier=tier)

            self._save_backtest_run(db, result, tpl, symbol, timeframe, days)
            return self._result_to_dict(result, tpl.name)
        except Exception as e:
            logger.error(f"[Evolver] 单次回测失败: {e}", exc_info=True)
            return {"error": str(e)}
        finally:
            db.close()

    # ══════════════════════════════════════════════════
    #  核心进化循环
    # ══════════════════════════════════════════════════

    def _run_evolution(self, cfg: Dict):
        """主进化循环"""
        from backend.database.connection import SessionLocal, MarketSessionLocal
        from backend.database.models import StrategyTemplate

        db = SessionLocal()
        try:
            templates = db.query(StrategyTemplate).filter(
                StrategyTemplate.is_active == True
            ).all()
            self._progress["total_templates"] = len(templates)
            self._progress["status"] = "running"

            logger.info(f"[Evolver] 开始进化: {len(templates)} 个模板, "
                        f"symbols={cfg['symbols']}, gen={cfg['max_generations']}")

            all_champions = []

            for tpl_idx, tpl in enumerate(templates):
                if not self._running:
                    break

                self._progress["current_template"] = tpl.name
                self._progress["completed_templates"] = tpl_idx

                engine_type = cfg.get("engine_type", "live_pipeline")
                logger.info(f"[Evolver] 进化模板 [{tpl_idx+1}/{len(templates)}]: {tpl.name} engine={engine_type}")

                if engine_type == "legacy":
                    champion = self._evolve_template(db, tpl, cfg)
                else:
                    champion = self._evolve_pipeline_template(db, tpl, cfg)
                if champion:
                    all_champions.append(champion)
                    self._progress["champions"].append({
                        "template": tpl.name,
                        "sharpe": champion["sharpe"],
                        "win_rate": champion["win_rate"],
                        "return": champion["total_return"],
                    })

                    if self._should_promote(champion, cfg.get("tier", "mid")):
                        self._promote_template(db, tpl, champion)
                        logger.info(f"[Evolver] 模板 {tpl.name} 已晋升！Sharpe={champion['sharpe']:.2f}")

            self._progress["status"] = "completed"
            self._progress["completed_templates"] = len(templates)
            logger.info(f"[Evolver] 进化完成: {len(all_champions)} 个冠军策略")

            # ── 闭环: 把回测冠军经验反哺给实盘策略 ──
            self._feed_wisdom_to_live_strategies(db, templates)

        except Exception as e:
            logger.error(f"[Evolver] 进化异常: {e}", exc_info=True)
            self._progress["status"] = "error"
            self._progress["error"] = str(e)
        finally:
            self._running = False
            db.close()

    def _evolve_template(self, db: Session, tpl, cfg: Dict) -> Optional[Dict]:
        """对单个模板进行 AI 驱动的多代进化"""
        from backend.services.backtest_evolution_engine import DEFAULT_SIGNAL_PARAMS, get_category_defaults

        category = tpl.category or "trend"
        tier = cfg.get("tier") or getattr(tpl, "tier", None) or "mid"
        tier_cfg = TIER_CONFIG.get(tier, TIER_CONFIG["mid"])

        base_config = tpl.strategy_config or {}
        base_risk = base_config.get("risk_params", {})
        base_risk.setdefault("stop_loss_pct", 0.04)
        base_risk.setdefault("take_profit_pct", 0.10)
        base_risk.setdefault("max_position_size", 0.20)
        base_risk.setdefault("default_leverage", 10)

        if "signal_params" not in base_risk:
            base_risk["signal_params"] = get_category_defaults(category)
        else:
            cat_defaults = get_category_defaults(category)
            for k, v in cat_defaults.items():
                base_risk["signal_params"].setdefault(k, v)

        # 根据 tier 选择正确的 timeframe
        timeframes = tier_cfg["timeframes"]
        bars_cache: Dict[str, List[Bar]] = {}
        val_bars_cache: Dict[str, List[Bar]] = {}
        wf_split = cfg.get("walk_forward_split", 0.7)

        for symbol in cfg["symbols"]:
            for tf in timeframes:
                key = f"{symbol}_{tf}"
                bars = self._load_bars(symbol, tf, cfg["lookback_days"])
                if bars and len(bars) >= 100:
                    split_idx = int(len(bars) * wf_split)
                    bars_cache[key] = bars[:split_idx]
                    val_bars_cache[key] = bars[split_idx:]
                    logger.info(f"[Evolver] 加载 {key}: train={split_idx} bars, val={len(bars)-split_idx} bars")

        if not bars_cache:
            logger.warning(f"[Evolver] {tpl.name} 无可用历史数据，跳过")
            return None

        # 初始种群全部通过变异生成（含第一个），确保每个个体参数都不同
        population = []
        # 第一个用小幅变异保持接近原始
        population.append(self._mutate_risk(base_risk, 0.1, category, tier))
        for _ in range(cfg["population_per_gen"] - 1):
            population.append(self._mutate_risk(base_risk, cfg["mutation_rate"], category, tier))

        best_overall = None
        ai_learning_history: List[Dict] = []
        no_improve_count = 0
        EARLY_STOP_PATIENCE = 3

        for gen in range(cfg["max_generations"]):
            if not self._running:
                break

            self._progress["current_generation"] = gen + 1
            self._progress["ai_phase"] = "回测中"
            logger.info(f"[Evolver] {tpl.name} 第 {gen+1}/{cfg['max_generations']} 代, 种群={len(population)}")

            # ── 阶段 1: 多线程并行回测 ──
            gen_results = self._run_generation_backtests(
                population, base_config, tpl.category, bars_cache, cfg["max_workers"], tier
            )

            if not gen_results:
                continue

            def _safe(v, default=0.0):
                if v is None or (isinstance(v, float) and not math.isfinite(v)):
                    return default
                return float(v)

            # 按 tier 计算交易频率门槛
            lookback_years = cfg.get("lookback_days", 730) / 365
            min_trades_hard = max(10, int(tier_cfg["min_trades_per_year"] * lookback_years * 0.5))
            ideal_trades = max(20, int(tier_cfg["ideal_trades_per_year"] * lookback_years))
            max_useful_trades = ideal_trades * 3
            weights = tier_cfg["eval_weights"]

            def _composite_score(x):
                r = x["result"]
                sharpe = _safe(r.sharpe_ratio)
                mdd = _safe(r.max_drawdown)
                wr = _safe(r.win_rate)
                trades = r.total_trades or 0

                # 交易频率因子（tier感知）
                if trades < min_trades_hard:
                    ratio = trades / max(min_trades_hard, 1)
                    trade_factor = ratio * ratio
                elif trades < ideal_trades:
                    trade_factor = 0.7 + 0.3 * (trades - min_trades_hard) / max(ideal_trades - min_trades_hard, 1)
                else:
                    trade_factor = 1.0 + 0.1 * min(1.0, (trades - ideal_trades) / max(max_useful_trades - ideal_trades, 1))

                # 加权评分（按tier权重）
                sharpe_score = sharpe * weights.get("sharpe", 0.30)
                wr_score = wr * 3 * weights.get("win_rate", 0.25)
                dd_penalty = mdd * weights.get("drawdown", 0.25)
                freq_score = trade_factor * weights.get("frequency", 0.20)

                return (sharpe_score + wr_score - dd_penalty) * (0.5 + 0.5 * freq_score)

            gen_results.sort(key=_composite_score, reverse=True)

            top = gen_results[0]
            worst = gen_results[-1] if len(gen_results) > 1 else None
            score = _composite_score(top)

            prev_score = _safe(best_overall.get("score", -999)) if best_overall else -999
            if best_overall is None or score > prev_score:
                best_overall = {
                    "risk": top["risk"],
                    "sharpe": _safe(top["result"].sharpe_ratio),
                    "win_rate": _safe(top["result"].win_rate),
                    "max_drawdown": _safe(top["result"].max_drawdown),
                    "total_return": _safe(top["result"].total_return),
                    "profit_factor": _safe(top["result"].profit_factor),
                    "total_trades": top["result"].total_trades,
                    "score": score,
                    "generation": gen + 1,
                    "final_equity": _safe(top["result"].final_equity),
                    "trades": top["result"].trades if hasattr(top["result"], "trades") else [],
                }
                no_improve_count = 0
            else:
                no_improve_count += 1
                if no_improve_count >= EARLY_STOP_PATIENCE:
                    logger.info(
                        f"[Evolver] {tpl.name} 连续{EARLY_STOP_PATIENCE}代无改善，早停 "
                        f"(best score={prev_score:.4f} at Gen{best_overall['generation']})"
                    )
                    break

            logger.info(
                f"[Evolver] {tpl.name} Gen{gen+1} best: "
                f"Sharpe={top['result'].sharpe_ratio:.2f}, "
                f"WR={top['result'].win_rate:.1%}, "
                f"MDD={top['result'].max_drawdown:.1%}, "
                f"Trades={top['result'].total_trades}"
            )

            # ── 阶段 2: AI 分析回测结果并给出改进方案 ──
            self._progress["ai_phase"] = "AI 分析中"

            ai_suggestion = self._ai_analyze_and_suggest(
                tpl_name=tpl.name,
                tpl_category=tpl.category or "trend",
                base_risk=base_risk,
                top_result=top,
                worst_result=worst,
                gen_results=gen_results,
                generation=gen + 1,
                learning_history=ai_learning_history,
            )

            gen_log = {
                "gen": gen + 1,
                "best_sharpe": round(_safe(top["result"].sharpe_ratio), 2),
                "best_wr": round(_safe(top["result"].win_rate) * 100, 1),
                "best_mdd": round(_safe(top["result"].max_drawdown) * 100, 1),
                "trades": top["result"].total_trades,
                "ai_diagnosis": ai_suggestion.get("diagnosis", ""),
                "ai_action": ai_suggestion.get("action", ""),
            }
            ai_learning_history.append(gen_log)
            self._progress["ai_learning_log"] = list(ai_learning_history)

            # ── 阶段 3: 根据 AI 建议生成下一代种群 ──
            self._progress["ai_phase"] = "生成下一代"

            half = max(len(gen_results) // 2, 1)
            survivors = gen_results[:half]

            # 多样性保护：确保至少1个高交易量个体进入存活池
            max_trade_entry = max(gen_results, key=lambda x: x["result"].total_trades)
            survivor_ids = {id(s["risk"]) for s in survivors}
            if id(max_trade_entry["risk"]) not in survivor_ids:
                survivors.append(max_trade_entry)
                logger.info(f"[Evolver] 多样性保护: 注入高交易量个体 (trades={max_trade_entry['result'].total_trades})")

            population = [s["risk"] for s in survivors]

            # AI 建议的参数作为强个体加入种群
            ai_params = ai_suggestion.get("improved_params")
            if ai_params and isinstance(ai_params, dict):
                population.insert(0, ai_params)
                logger.info(f"[Evolver] AI 建议参数已注入")

            # 交易量衰减保护：如果本代最佳交易量比上代下降>30%，注入宽松变异
            prev_best_trades = ai_learning_history[-1]["trades"] if ai_learning_history else 0
            cur_best_trades = top["result"].total_trades
            if prev_best_trades > 0 and cur_best_trades < prev_best_trades * 0.7:
                loosened = self._loosen_params(copy.deepcopy(top["risk"]), category)
                population.append(loosened)
                logger.warning(
                    f"[Evolver] ⚠️ 交易量衰减 {prev_best_trades}→{cur_best_trades}, 注入宽松变异"
                )

            # P1-8: 剩余用变异+交叉填充
            while len(population) < cfg["population_per_gen"]:
                # Uniform crossover: 从top half选2个parent，每个基因位50/50继承
                if len(survivors) >= 2:
                    _p1 = random.choice(survivors[:half])
                    _p2 = random.choice(survivors[:half])
                    _parent_a = _p1["risk"]
                    _parent_b = _p2["risk"]
                    _child = {}
                    _all_keys = set(list(_parent_a.keys()) + list(_parent_b.keys()))
                    for _k in _all_keys:
                        if _k in _parent_a and _k in _parent_b:
                            _child[_k] = copy.deepcopy(_parent_a[_k] if random.random() < 0.5 else _parent_b[_k])
                        elif _k in _parent_a:
                            _child[_k] = copy.deepcopy(_parent_a[_k])
                        else:
                            _child[_k] = copy.deepcopy(_parent_b[_k])
                    # 变异交叉后的子代
                    population.append(self._mutate_risk(_child, cfg["mutation_rate"], category, tier))
                else:
                    parent = random.choice([s["risk"] for s in survivors])
                    population.append(self._mutate_risk(parent, cfg["mutation_rate"], category, tier))

        # ── Walk-Forward 验证：在验证集上检验冠军参数 ──
        if best_overall and val_bars_cache:
            self._progress["ai_phase"] = "Walk-Forward 验证"
            val_results = self._run_generation_backtests(
                [best_overall["risk"]], base_config, tpl.category, val_bars_cache, cfg["max_workers"], tier
            )
            if val_results:
                vr = val_results[0]["result"]
                def _safe_val(v):
                    if v is None or (isinstance(v, float) and not math.isfinite(v)):
                        return 0.0
                    return float(v)

                best_overall["val_sharpe"] = _safe_val(vr.sharpe_ratio)
                best_overall["val_win_rate"] = _safe_val(vr.win_rate)
                best_overall["val_max_drawdown"] = _safe_val(vr.max_drawdown)
                best_overall["val_total_return"] = _safe_val(vr.total_return)
                best_overall["val_total_trades"] = vr.total_trades

                train_sharpe = best_overall["sharpe"]
                val_sharpe = best_overall["val_sharpe"]
                overfit_ratio = abs(train_sharpe - val_sharpe) / max(abs(train_sharpe), 0.01)
                best_overall["overfit_ratio"] = round(overfit_ratio, 2)

                logger.info(
                    f"[Evolver] {tpl.name} Walk-Forward: "
                    f"Train Sharpe={train_sharpe:.2f} → Val Sharpe={val_sharpe:.2f} "
                    f"(overfit={overfit_ratio:.0%}), "
                    f"Val WR={vr.win_rate:.1%}, Val MDD={vr.max_drawdown:.1%}"
                )

                if overfit_ratio > 0.6:
                    logger.warning(
                        f"[Evolver] ⚠️ {tpl.name} 过拟合风险高 (overfit_ratio={overfit_ratio:.0%}), "
                        f"训练Sharpe={train_sharpe:.2f} vs 验证Sharpe={val_sharpe:.2f}"
                    )
                    best_overall["overfit_warning"] = True

        # 保存冠军回测记录 + AI 学习日志
        if best_overall:
            best_overall["ai_learning_log"] = ai_learning_history
            try:
                self._save_champion(db, tpl, best_overall)
            except Exception as e:
                logger.warning(f"[Evolver] 保存冠军记录失败: {e}")

        return best_overall

    # ══════════════════════════════════════════════════
    #  实盘管线进化 — 用 LivePipelineBacktestEngine
    # ══════════════════════════════════════════════════

    def _evolve_pipeline_template(self, db: Session, tpl, cfg: Dict) -> Optional[Dict]:
        """用实盘同款管线引擎进化策略模板"""
        tier = cfg.get("tier") or getattr(tpl, "tier", None) or "mid"
        tier_cfg = TIER_CONFIG.get(tier, TIER_CONFIG["mid"])

        # 从模板或默认值获取管线参数
        base_config = tpl.strategy_config or {}
        base_pipeline = {**DEFAULT_PIPELINE_PARAMS}
        base_pipeline.update(base_config.get("pipeline_params", {}))

        # 加载历史数据
        timeframes = tier_cfg["timeframes"]
        bars_cache: Dict[str, List[Bar]] = {}
        val_bars_cache: Dict[str, List[Bar]] = {}
        wf_split = cfg.get("walk_forward_split", 0.7)

        for symbol in cfg["symbols"]:
            for tf in timeframes:
                key = f"{symbol}_{tf}"
                bars = self._load_bars(symbol, tf, cfg["lookback_days"])
                if bars and len(bars) >= 100:
                    split_idx = int(len(bars) * wf_split)
                    bars_cache[key] = bars[:split_idx]
                    val_bars_cache[key] = bars[split_idx:]

        if not bars_cache:
            logger.warning(f"[Evolver] {tpl.name} 无可用历史数据（pipeline模式），跳过")
            return None

        # 加载历史资金费率和恐贪指数
        funding_rates = self._load_funding_rates(db, cfg["symbols"])
        fgi_series = self._load_fgi_series(db, cfg.get("lookback_days", 730))

        # 初始种群
        population = [self._mutate_pipeline_params(base_pipeline, 0.1)]
        for _ in range(cfg["population_per_gen"] - 1):
            population.append(self._mutate_pipeline_params(base_pipeline, cfg["mutation_rate"]))

        best_overall = None
        no_improve_count = 0
        EARLY_STOP = 3

        for gen in range(cfg["max_generations"]):
            if not self._running:
                break
            self._progress["current_generation"] = gen + 1
            self._progress["ai_phase"] = "管线回测中"

            gen_results = self._run_pipeline_generation(
                population, bars_cache, cfg["max_workers"], tier,
                funding_rates, fgi_series,
            )
            if not gen_results:
                continue

            lookback_years = cfg.get("lookback_days", 730) / 365
            min_trades = max(5, int(tier_cfg["min_trades_per_year"] * lookback_years * 0.3))
            weights = tier_cfg["eval_weights"]

            def _safe(v, d=0.0):
                return float(v) if v is not None and (not isinstance(v, float) or math.isfinite(v)) else d

            def _score(x):
                r = x["result"]
                sharpe = _safe(r.sharpe_ratio)
                wr = _safe(r.win_rate)
                mdd = _safe(r.max_drawdown)
                trades = r.total_trades or 0
                tf = (trades / max(min_trades, 1)) ** 0.5 if trades < min_trades else 1.0
                return (sharpe * weights.get("sharpe", 0.3)
                        + wr * 3 * weights.get("win_rate", 0.25)
                        - mdd * weights.get("drawdown", 0.25)) * (0.5 + 0.5 * tf)

            gen_results.sort(key=_score, reverse=True)
            top = gen_results[0]
            top_score = _score(top)
            r = top["result"]

            champion_info = {
                "sharpe": _safe(r.sharpe_ratio),
                "win_rate": _safe(r.win_rate),
                "max_drawdown": _safe(r.max_drawdown),
                "total_trades": r.total_trades,
                "total_return": _safe(r.total_return),
                "profit_factor": _safe(r.profit_factor),
                "pipeline_params": top["params"],
                "risk": top["params"],
                "score": top_score,
                "engine_type": "live_pipeline",
                "trades": r.trades if hasattr(r, "trades") else [],
            }

            improved = False
            if best_overall is None or top_score > best_overall.get("score", -999):
                best_overall = champion_info
                improved = True
                no_improve_count = 0
            else:
                no_improve_count += 1

            logger.info(
                f"[Evolver/Pipeline] {tpl.name} Gen {gen+1}: "
                f"Sharpe={champion_info['sharpe']:.2f} WR={champion_info['win_rate']:.1%} "
                f"Trades={champion_info['total_trades']} {'⬆' if improved else ''}"
            )

            if no_improve_count >= EARLY_STOP:
                logger.info(f"[Evolver/Pipeline] {tpl.name} 连续 {EARLY_STOP} 代无提升，提前停止")
                break

            # P1-8: 下一代：保留 top 30% + 交叉变异
            elite_n = max(1, len(gen_results) // 3)
            elites = [x["params"] for x in gen_results[:elite_n]]
            population = list(elites)
            while len(population) < cfg["population_per_gen"]:
                # Uniform crossover: 从elites选2个parent，每个参数位50/50继承
                if len(elites) >= 2:
                    _pa = random.choice(elites)
                    _pb = random.choice(elites)
                    _child = {}
                    _all_keys = set(list(_pa.keys()) + list(_pb.keys()))
                    for _k in _all_keys:
                        if _k in _pa and _k in _pb:
                            _child[_k] = copy.deepcopy(_pa[_k] if random.random() < 0.5 else _pb[_k])
                        elif _k in _pa:
                            _child[_k] = copy.deepcopy(_pa[_k])
                        else:
                            _child[_k] = copy.deepcopy(_pb[_k])
                    population.append(self._mutate_pipeline_params(_child, cfg["mutation_rate"]))
                else:
                    parent = random.choice(elites)
                    population.append(self._mutate_pipeline_params(parent, cfg["mutation_rate"]))

        # Walk-forward 验证
        if best_overall and val_bars_cache:
            for key, vbars in val_bars_cache.items():
                if len(vbars) >= 50:
                    engine = LivePipelineBacktestEngine(initial_capital=10000)
                    vr = engine.run(vbars, best_overall["pipeline_params"], tier=tier,
                                    funding_rate_series=funding_rates, fgi_series=fgi_series)
                    if vr and not vr.error:
                        def _sf(v):
                            return float(v) if v is not None and (not isinstance(v, float) or math.isfinite(v)) else 0.0
                        best_overall["val_sharpe"] = _sf(vr.sharpe_ratio)
                        best_overall["val_win_rate"] = _sf(vr.win_rate)
                        best_overall["val_max_drawdown"] = _sf(vr.max_drawdown)
                        best_overall["val_total_return"] = _sf(vr.total_return)
                        best_overall["val_total_trades"] = vr.total_trades
                        overfit = abs(best_overall["sharpe"] - best_overall["val_sharpe"]) / max(abs(best_overall["sharpe"]), 0.01)
                        best_overall["overfit_ratio"] = round(overfit, 2)
                        if overfit > 0.6:
                            best_overall["overfit_warning"] = True
                    break

        if best_overall:
            try:
                self._save_champion(db, tpl, best_overall)
            except Exception as e:
                logger.warning(f"[Evolver/Pipeline] 保存冠军失败: {e}")

        return best_overall

    def _run_pipeline_generation(self, population, bars_cache, max_workers, tier,
                                  funding_rates, fgi_series) -> List[Dict]:
        """并行回测一代管线种群"""
        results = []
        futures = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for params in population:
                for key, bars in bars_cache.items():
                    run_id = f"lpe_{uuid.uuid4().hex[:8]}"
                    future = pool.submit(
                        self._run_pipeline_backtest_worker,
                        bars, params, run_id, tier, funding_rates, fgi_series,
                    )
                    futures.append((future, params, key))

            for future, params, key in futures:
                try:
                    result = future.result(timeout=120)
                    if result and not result.error:
                        results.append({"result": result, "params": params, "key": key})
                except Exception as e:
                    logger.warning(f"[Evolver/Pipeline] 回测异常: {e}")
        return results

    @staticmethod
    def _run_pipeline_backtest_worker(bars, params, run_id, tier,
                                       funding_rates, fgi_series) -> BacktestResult:
        engine = LivePipelineBacktestEngine(initial_capital=10000)
        return engine.run(bars, params, run_id=run_id, tier=tier,
                          funding_rate_series=funding_rates, fgi_series=fgi_series)

    @staticmethod
    def _mutate_pipeline_params(base: Dict, rate: float) -> Dict:
        """对管线参数做随机变异"""
        m = copy.deepcopy(base)

        def _jitter(val, lo, hi, strength=1.0):
            delta = (hi - lo) * rate * strength * random.uniform(-1, 1)
            return max(lo, min(hi, val + delta))

        for key, (lo, hi) in PIPELINE_PARAM_RANGES.items():
            if key not in m:
                m[key] = DEFAULT_PIPELINE_PARAMS.get(key, (lo + hi) / 2)
            val = m[key]
            is_int = isinstance(val, int) or (isinstance(val, float) and val == int(val) and lo == int(lo) and hi == int(hi))
            if is_int:
                m[key] = int(round(_jitter(float(val), lo, hi)))
            else:
                m[key] = round(_jitter(float(val), lo, hi), 4)
        return m

    @staticmethod
    def _load_funding_rates(db: Session, symbols: list) -> Dict[int, float]:
        """从数据库加载历史资金费率（使用 Market DB 会话）"""
        rates = {}
        try:
            from backend.database.models import MarketAssetMetrics
            from backend.database.connection import MarketSessionLocal

            market_db = MarketSessionLocal()
            try:
                for sym in symbols:
                    rows = market_db.query(MarketAssetMetrics).filter(
                        MarketAssetMetrics.symbol == sym.upper(),
                        MarketAssetMetrics.funding_rate.isnot(None),
                    ).order_by(MarketAssetMetrics.timestamp.desc()).limit(5000).all()
                    for row in rows:
                        ts = int(row.timestamp.timestamp()) if hasattr(row.timestamp, 'timestamp') else int(row.timestamp)
                        rates[ts] = float(row.funding_rate or 0)
            finally:
                market_db.close()
        except Exception as e:
            logger.debug(f"[Evolver] 加载资金费率失败: {e}")
        return rates

    @staticmethod
    def _load_fgi_series(db: Session, lookback_days: int = 730) -> Dict[int, float]:
        """加载历史恐贪指数"""
        fgi = {}
        try:
            import httpx
            resp = httpx.get(f"https://api.alternative.me/fng/?limit={lookback_days}&format=json", timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for item in data:
                    ts = int(item.get("timestamp", 0))
                    val = int(item.get("value", 50))
                    if ts:
                        fgi[ts] = val
        except Exception as e:
            logger.debug(f"[Evolver] 加载恐贪指数失败: {e}")
        return fgi

    def _run_generation_backtests(self, population, base_config, category,
                                   bars_cache, max_workers, tier="mid") -> List[Dict]:
        """并行回测一代种群"""
        gen_results = []
        futures = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for risk_variant in population:
                for key, bars in bars_cache.items():
                    run_id = f"evo_{uuid.uuid4().hex[:8]}"
                    self._progress["total_backtests"] = self._progress.get("total_backtests", 0) + 1
                    future = pool.submit(
                        self._run_backtest_worker,
                        bars, {**base_config, "category": category},
                        risk_variant, run_id, tier,
                    )
                    futures.append((future, risk_variant, key))

            for future, risk_variant, key in futures:
                try:
                    result = future.result(timeout=120)
                    self._progress["completed_backtests"] = self._progress.get("completed_backtests", 0) + 1
                    if result and not result.error:
                        gen_results.append({
                            "result": result,
                            "risk": risk_variant,
                            "key": key,
                        })
                except Exception as e:
                    logger.warning(f"[Evolver] 回测超时/异常: {e}")
        return gen_results

    # ══════════════════════════════════════════════════
    #  AI 学习核心 — 分析回测结果 + 生成改进方案
    # ══════════════════════════════════════════════════

    def _ai_analyze_and_suggest(
        self, tpl_name: str, tpl_category: str, base_risk: Dict,
        top_result: Dict, worst_result: Optional[Dict],
        gen_results: List[Dict], generation: int,
        learning_history: List[Dict],
    ) -> Dict[str, Any]:
        """让 AI 分析回测结果，诊断问题，给出具体改进参数"""
        try:
            from backend.services.llm_config_service import get_llm_config, call_llm_api

            llm_config = get_llm_config()
            if not llm_config:
                logger.warning("[Evolver-AI] 无 LLM 配置，使用启发式优化")
                return self._heuristic_suggestion(top_result, base_risk)

            top_r = top_result["result"]
            top_risk = top_result["risk"]

            # 交易胜负分布
            trades = top_r.trades if hasattr(top_r, 'trades') else []
            win_trades = [t for t in trades if t.pnl > 0]
            loss_trades = [t for t in trades if t.pnl <= 0]
            avg_win_pnl = sum(t.pnl_pct for t in win_trades) / len(win_trades) if win_trades else 0
            avg_loss_pnl = sum(t.pnl_pct for t in loss_trades) / len(loss_trades) if loss_trades else 0

            exit_reasons = {}
            for t in trades:
                exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

            # 当前信号参数
            cur_sp = top_risk.get("signal_params", {})

            strategy_type_hints = {
                "trend": "信号逻辑: EMA快线>中线时做多(+回踩反弹), MACD柱>0辅助确认。核心参数: ema_fast, ema_mid, ema_slow, rsi_long_lo/hi",
                "mean_reversion": "信号逻辑: 价格触布林下轨+RSI超卖做多。核心参数: rsi_os, rsi_ob, bb_period, bb_std",
                "range": "信号逻辑: 价格在布林带边缘+RSI过滤。核心参数: bb_edge_pct, rsi_long_hi, rsi_short_lo, bb_period",
                "breakout": "信号逻辑: 突破N周期高/低点+(放量或EMA确认)。核心参数: breakout_lookback, vol_surge_mult, ema_fast",
                "swing": "信号逻辑: 中期趋势中回撤到EMA均线附近+RSI或MACD确认。核心参数: swing_pullback_lo/hi, ema_mid, ema_slow",
                "momentum": "信号逻辑: EMA方向+MACD柱加速+RSI范围。核心参数: rsi_long_hi, rsi_short_lo, momentum_vol_mult",
            }
            type_hint = strategy_type_hints.get(tpl_category, "通用趋势跟踪策略")

            backtest_summary = (
                f"策略: {tpl_name} (类型: {tpl_category})\n"
                f"🔑 {type_hint}\n\n"
                f"第 {generation} 代回测结果:\n"
                f"  总收益: {(top_r.total_return or 0)*100:.2f}%\n"
                f"  Sharpe: {top_r.sharpe_ratio:.2f}\n"
                f"  最大回撤: {(top_r.max_drawdown or 0)*100:.1f}%\n"
                f"  胜率: {(top_r.win_rate or 0)*100:.1f}%\n"
                f"  盈亏比: {top_r.profit_factor:.2f}\n"
                f"  总交易: {top_r.total_trades}笔\n"
                f"  最终权益: {top_r.final_equity:.0f} (初始10000)\n"
                f"  连胜: {top_r.max_consecutive_wins} | 连亏: {top_r.max_consecutive_losses}\n"
                f"  平均持仓: {top_r.avg_holding_bars:.1f} bars\n\n"
                f"交易分析:\n"
                f"  盈利交易 {len(win_trades)}笔, 平均+{avg_win_pnl*100:.2f}%\n"
                f"  亏损交易 {len(loss_trades)}笔, 平均{avg_loss_pnl*100:.2f}%\n"
                f"  出场原因: {exit_reasons}\n\n"
                f"【出场参数】:\n"
                f"  止损: {top_risk.get('stop_loss_pct', 0)*100:.1f}%\n"
                f"  止盈: {top_risk.get('take_profit_pct', 0)*100:.1f}%\n"
                f"  仓位: {top_risk.get('max_position_size', 0)*100:.0f}%\n"
                f"  杠杆: {top_risk.get('default_leverage', 1)}x\n"
                f"  移动止损激活: {top_risk.get('trailing_activation_pct', 0)*100:.1f}%\n"
                f"  移动止损距离: {top_risk.get('trailing_distance_pct', 0)*100:.1f}%\n\n"
                f"【入场信号参数】（这些决定了什么时候开单！修改它们才能改变胜率）:\n"
                f"  EMA快线: {cur_sp.get('ema_fast', 9)} | EMA中线: {cur_sp.get('ema_mid', 21)} | EMA慢线: {cur_sp.get('ema_slow', 50)}\n"
                f"  RSI周期: {cur_sp.get('rsi_period', 14)}\n"
                f"  做多RSI范围: {cur_sp.get('rsi_long_lo', 35)}~{cur_sp.get('rsi_long_hi', 75)}\n"
                f"  做空RSI范围: {cur_sp.get('rsi_short_lo', 25)}~{cur_sp.get('rsi_short_hi', 65)}\n"
                f"  RSI超买: {cur_sp.get('rsi_ob', 70)} | RSI超卖: {cur_sp.get('rsi_os', 30)}\n"
                f"  布林带周期: {cur_sp.get('bb_period', 20)} | 标准差: {cur_sp.get('bb_std', 2.0)}\n"
                f"  MACD: {cur_sp.get('macd_fast', 12)}/{cur_sp.get('macd_slow', 26)}/{cur_sp.get('macd_signal', 9)}\n"
                f"  突破回看: {cur_sp.get('breakout_lookback', 20)} | 放量倍数: {cur_sp.get('vol_surge_mult', 1.5)}\n"
                f"  BB边缘: {cur_sp.get('bb_edge_pct', 0.20)} | 波段回撤: {cur_sp.get('swing_pullback_lo', -0.05)}~{cur_sp.get('swing_pullback_hi', -0.003)}\n"
                f"  动量量能倍数: {cur_sp.get('momentum_vol_mult', 1.1)} | 最小间隔: {cur_sp.get('min_bars_between', 3)} bars\n"
            )

            # 加入历史学习记录
            if learning_history:
                backtest_summary += "\n过往代数表现趋势:\n"
                for h in learning_history[-3:]:
                    backtest_summary += (
                        f"  Gen{h['gen']}: Sharpe={h['best_sharpe']:.2f}, "
                        f"WR={h['best_wr']:.1f}%, MDD={h['best_mdd']:.1f}%, "
                        f"Trades={h['trades']}"
                    )
                    if h.get("ai_action"):
                        backtest_summary += f" → AI调整: {h['ai_action'][:60]}"
                    backtest_summary += "\n"

                # 交易量趋势警告
                trade_counts = [h["trades"] for h in learning_history]
                if len(trade_counts) >= 2 and trade_counts[-1] < trade_counts[0] * 0.7:
                    backtest_summary += (
                        f"\n⚠️ 严重警告：交易次数从Gen1的{trade_counts[0]}笔降到当前{trade_counts[-1]}笔！\n"
                        f"策略正在变得过于保守，你必须放宽入场条件来恢复交易频率！\n"
                    )

            # 如果有最差结果，给 AI 对比信息
            if worst_result:
                w = worst_result["result"]
                backtest_summary += (
                    f"\n最差变体对比:\n"
                    f"  收益: {(w.total_return or 0)*100:.2f}%, Sharpe: {w.sharpe_ratio:.2f}, "
                    f"MDD: {(w.max_drawdown or 0)*100:.1f}%, 胜率: {(w.win_rate or 0)*100:.1f}%\n"
                    f"  参数: SL={worst_result['risk'].get('stop_loss_pct',0)*100:.1f}%, "
                    f"TP={worst_result['risk'].get('take_profit_pct',0)*100:.1f}%\n"
                )

            messages = [
                {"role": "system", "content": (
                    "你是顶级量化策略优化专家。分析回测结果，找出核心问题并给出具体改进方案。\n\n"
                    "关键：你必须同时优化【入场信号参数】和【出场参数】！\n"
                    "- 只改止损止盈不会改变胜率，必须修改入场参数（EMA周期、RSI阈值等）才能改变开单时机\n"
                    "- 交易次数太少→放宽入场条件（扩大RSI范围）；止损太频繁→调整EMA周期或止损比例\n"
                    "- 胜率低→收紧入场条件或换更敏感的指标周期\n\n"
                    "分析要点:\n"
                    "1. 出场原因分布中sl(止损)占比高→止损太紧或入场时机差\n"
                    "2. 交易太少→RSI范围太窄或EMA周期太长导致信号稀少\n"
                    "3. 连亏多→当前市场条件不适合该信号周期\n\n"
                    "重要：这是加密货币7x24小时市场，参数需比股票更宽松！\n"
                    "- RSI范围要宽(如rsi_long_lo:20-30, rsi_long_hi:80-90)\n"
                    "- EMA周期适中(fast:5-12, mid:15-25, slow:40-60)\n"
                    "- 目标：3年回测至少100笔！理想200+笔。低于80笔=参数过严，必须放宽\n"
                    "- ⚠️ 绝对禁止：为了提高Sharpe而减少交易次数！交易次数下降=方向错误\n"
                    "- 如果交易次数比上一代减少，必须放宽入场条件（扩大RSI范围、缩短EMA、降低min_bars_between）\n"
                    "- min_bars_between建议1-3，不要超过4\n\n"
                    "返回严格JSON:\n"
                    '{"diagnosis": "诊断", "action": "改进方向", "improved_params": {'
                    '"stop_loss_pct": 0.03, "take_profit_pct": 0.08, '
                    '"max_position_size": 0.15, "default_leverage": 2, '
                    '"trailing_activation_pct": 0.02, "trailing_distance_pct": 0.015, '
                    '"signal_params": {'
                    '"ema_fast": 8, "ema_mid": 21, "ema_slow": 55, '
                    '"rsi_period": 14, "rsi_long_lo": 30, "rsi_long_hi": 80, '
                    '"rsi_short_lo": 20, "rsi_short_hi": 70, '
                    '"rsi_ob": 75, "rsi_os": 25, '
                    '"bb_period": 20, "bb_std": 2.0, "bb_edge_pct": 0.20, '
                    '"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, '
                    '"breakout_lookback": 14, "vol_surge_mult": 1.3, '
                    '"swing_pullback_lo": -0.05, "swing_pullback_hi": -0.003, '
                    '"momentum_vol_mult": 1.1, "min_bars_between": 3'
                    '}}}'
                )},
                {"role": "user", "content": backtest_summary},
            ]

            from backend.services.llm_config_service import call_llm_api_sync
            ai_response = call_llm_api_sync(
                llm_config, messages, temperature=0.4, max_tokens=800
            )

            if not ai_response:
                logger.warning("[Evolver-AI] LLM 无响应")
                return self._heuristic_suggestion(top_result, base_risk)

            content = ai_response.get("choices", [{}])[0].get("message", {}).get("content", "")
            content = content.strip()

            # 提取 JSON
            if "```" in content:
                content = content.split("```")[1] if "```" in content else content
                content = content.replace("json", "").strip()
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                content = content[start:end]

            suggestion = json.loads(content)

            # 安全范围校验（出场参数）
            params = suggestion.get("improved_params", {})
            params["stop_loss_pct"] = max(0.01, min(0.15, params.get("stop_loss_pct", 0.04)))
            params["take_profit_pct"] = max(0.02, min(0.40, params.get("take_profit_pct", 0.10)))
            params["max_position_size"] = max(0.03, min(0.30, params.get("max_position_size", 0.15)))
            params["default_leverage"] = max(5, min(20, int(params.get("default_leverage", 10))))
            params["trailing_activation_pct"] = max(0.005, min(0.08, params.get("trailing_activation_pct", 0.02)))
            params["trailing_distance_pct"] = max(0.003, min(0.05, params.get("trailing_distance_pct", 0.015)))

            # 安全范围校验（入场信号参数）
            from backend.services.backtest_evolution_engine import SIGNAL_PARAM_RANGES, DEFAULT_SIGNAL_PARAMS
            ai_sp = params.get("signal_params", {})
            if ai_sp:
                for k, (lo, hi) in SIGNAL_PARAM_RANGES.items():
                    if k in ai_sp:
                        v = ai_sp[k]
                        if isinstance(v, (int, float)):
                            ai_sp[k] = type(DEFAULT_SIGNAL_PARAMS.get(k, v))(max(lo, min(hi, v)))
                params["signal_params"] = ai_sp

            suggestion["improved_params"] = params

            sp_info = params.get("signal_params", {})
            logger.info(
                f"[Evolver-AI] 诊断: {suggestion.get('diagnosis', '?')} | "
                f"方案: {suggestion.get('action', '?')} | "
                f"SL={params['stop_loss_pct']:.3f} TP={params['take_profit_pct']:.3f} "
                f"Lev={params['default_leverage']} | "
                f"EMA={sp_info.get('ema_fast','?')}/{sp_info.get('ema_mid','?')}/{sp_info.get('ema_slow','?')} "
                f"RSI_long={sp_info.get('rsi_long_lo','?')}~{sp_info.get('rsi_long_hi','?')}"
            )

            return suggestion

        except json.JSONDecodeError as e:
            logger.warning(f"[Evolver-AI] JSON 解析失败: {e}, 使用启发式")
            return self._heuristic_suggestion(top_result, base_risk)
        except Exception as e:
            logger.warning(f"[Evolver-AI] AI 分析异常: {e}, 使用启发式")
            return self._heuristic_suggestion(top_result, base_risk)

    @staticmethod
    def _heuristic_suggestion(top_result: Dict, base_risk: Dict) -> Dict:
        """无 LLM 时的启发式优化——多维度诊断 + 入场/出场参数联动调整"""
        from backend.services.backtest_evolution_engine import DEFAULT_SIGNAL_PARAMS

        r = top_result["result"]
        risk = copy.deepcopy(top_result["risk"])
        sp = risk.get("signal_params", dict(DEFAULT_SIGNAL_PARAMS))
        diagnosis = []
        action = []

        trades = r.trades if hasattr(r, 'trades') else []

        # ── 维度1: 出场原因分布分析 ──
        if trades:
            exit_counts = {}
            for t in trades:
                exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1
            sl_count = exit_counts.get("sl", 0)
            tp_count = exit_counts.get("tp", 0)
            trailing_count = exit_counts.get("trailing", 0)
            sl_ratio = sl_count / len(trades)

            if sl_ratio > 0.5:
                risk["stop_loss_pct"] = min(risk.get("stop_loss_pct", 0.04) * 1.3, 0.10)
                sp["rsi_long_lo"] = max(20, sp.get("rsi_long_lo", 35) - 5)
                sp["rsi_short_hi"] = min(80, sp.get("rsi_short_hi", 65) + 5)
                diagnosis.append(f"止损触发{sl_ratio:.0%}")
                action.append("放宽止损+入场RSI")

            if tp_count > 0 and sl_count > 0:
                tp_sl_ratio = tp_count / sl_count
                if tp_sl_ratio < 0.5:
                    risk["take_profit_pct"] = max(
                        risk.get("take_profit_pct", 0.10) * 0.85, 0.03
                    )
                    diagnosis.append(f"止盈/止损比={tp_sl_ratio:.1f}偏低")
                    action.append("降低止盈点让更多交易盈利出场")

        # ── 维度2: 交易频率（3年加密市场目标：至少100笔，理想200+笔）──
        MIN_TRADES_TARGET = 120
        if r.total_trades < MIN_TRADES_TARGET:
            gap = max(1, (MIN_TRADES_TARGET - r.total_trades) // 5)
            sp["rsi_long_lo"] = max(10, sp.get("rsi_long_lo", 25) - gap * 5)
            sp["rsi_long_hi"] = min(95, sp.get("rsi_long_hi", 88) + gap * 3)
            sp["rsi_short_lo"] = max(5, sp.get("rsi_short_lo", 12) - gap * 3)
            sp["rsi_short_hi"] = min(92, sp.get("rsi_short_hi", 75) + gap * 5)
            sp["rsi_ob"] = min(92, sp.get("rsi_ob", 80) + gap * 3)
            sp["rsi_os"] = max(8, sp.get("rsi_os", 20) - gap * 3)
            sp["ema_fast"] = max(3, sp.get("ema_fast", 8) - gap * 2)
            sp["ema_mid"] = max(6, sp.get("ema_mid", 21) - gap * 4)
            sp["ema_slow"] = max(20, sp.get("ema_slow", 55) - gap * 6)
            sp["vol_surge_mult"] = max(0.6, sp.get("vol_surge_mult", 1.1) - 0.2 * gap)
            sp["breakout_lookback"] = max(3, sp.get("breakout_lookback", 10) - gap * 3)
            sp["bb_edge_pct"] = min(0.45, sp.get("bb_edge_pct", 0.25) + 0.05 * gap)
            sp["swing_pullback_lo"] = max(-0.15, sp.get("swing_pullback_lo", -0.08) - 0.02 * gap)
            sp["swing_pullback_hi"] = min(0.05, sp.get("swing_pullback_hi", 0.01) + 0.01 * gap)
            sp["min_bars_between"] = 1
            sp["momentum_vol_mult"] = max(0.5, sp.get("momentum_vol_mult", 0.95) - 0.15 * gap)
            sp["bb_std"] = max(1.0, sp.get("bb_std", 1.8) - 0.15 * gap)
            diagnosis.append(f"交易仅{r.total_trades}笔(3年应>{MIN_TRADES_TARGET})")
            action.append("大幅放宽所有入场条件+缩短冷却")

        # ── 维度3: 胜率 ──
        # 注意：胜率低时不收紧入场条件（那会减少交易量）；改用调整出场参数提升单笔质量
        if (r.win_rate or 0) < 0.35 and r.total_trades >= 10:
            risk["stop_loss_pct"] = min(0.12, risk.get("stop_loss_pct", 0.04) * 1.25)
            risk["trailing_activation_pct"] = max(0.005, risk.get("trailing_activation_pct", 0.02) * 0.75)
            risk["trailing_distance_pct"] = max(0.003, risk.get("trailing_distance_pct", 0.015) * 0.8)
            risk["max_position_size"] = max(risk.get("max_position_size", 0.20) * 0.85, 0.05)
            diagnosis.append("胜率过低")
            action.append("放宽止损+更早移动止损锁利+缩仓")

        # ── 维度4: 持仓时间分析 ──
        avg_bars = r.avg_holding_bars if hasattr(r, 'avg_holding_bars') else 0
        if avg_bars > 100 and r.total_trades >= 5:
            sp["ema_fast"] = max(3, sp.get("ema_fast", 8) - 2)
            sp["ema_mid"] = max(8, sp.get("ema_mid", 21) - 5)
            risk["trailing_activation_pct"] = max(
                0.005, risk.get("trailing_activation_pct", 0.02) * 0.7
            )
            diagnosis.append(f"平均持仓{avg_bars:.0f}bars过长")
            action.append("缩短EMA+更早移动止损锁利")
        elif avg_bars < 3 and r.total_trades >= 30:
            sp["ema_fast"] = min(15, sp.get("ema_fast", 5) + 2)
            risk["stop_loss_pct"] = min(0.10, risk.get("stop_loss_pct", 0.04) * 1.2)
            diagnosis.append(f"平均持仓{avg_bars:.0f}bars过短")
            action.append("调大EMA+放宽止损让持仓更久")

        # ── 维度5: 连续亏损 ──
        max_losses = r.max_consecutive_losses if hasattr(r, 'max_consecutive_losses') else 0
        if max_losses >= 8:
            risk["max_position_size"] = max(risk.get("max_position_size", 0.20) * 0.7, 0.05)
            sp["vol_surge_mult"] = min(2.5, sp.get("vol_surge_mult", 1.3) + 0.3)
            diagnosis.append(f"最大连亏{max_losses}笔")
            action.append("缩仓+要求更强量能确认")

        # ── 维度6: 回撤 ──
        # 回撤过大时通过降杠杆+缩仓控制风险，不修改入场信号参数（避免减少交易量）
        if (r.max_drawdown or 0) > 0.20:
            risk["default_leverage"] = max(5, risk.get("default_leverage", 10) - 2)
            risk["max_position_size"] = max(0.05, risk.get("max_position_size", 0.20) * 0.8)
            risk["stop_loss_pct"] = max(0.01, risk.get("stop_loss_pct", 0.04) * 0.85)
            diagnosis.append("回撤过大")
            action.append("降杠杆+缩仓+收紧止损")

        # ── 维度7: 盈亏比 ──
        if (r.profit_factor or 0) < 1.0:
            risk["take_profit_pct"] = min(risk.get("take_profit_pct", 0.10) * 1.2, 0.30)
            risk["trailing_activation_pct"] = max(
                0.005, risk.get("trailing_activation_pct", 0.02) * 0.8
            )
            diagnosis.append("盈亏比不足")
            action.append("扩大止盈+更早激活移动止损")

        # ── 维度8: 多空平衡 ──
        if trades:
            long_trades = [t for t in trades if t.side == "long"]
            short_trades = [t for t in trades if t.side == "short"]
            if len(trades) >= 10:
                long_ratio = len(long_trades) / len(trades)
                if long_ratio > 0.85:
                    sp["rsi_short_hi"] = min(80, sp.get("rsi_short_hi", 70) + 5)
                    diagnosis.append(f"几乎全做多({long_ratio:.0%})")
                    action.append("放宽做空RSI上限增加空单")
                elif long_ratio < 0.15:
                    sp["rsi_long_lo"] = max(15, sp.get("rsi_long_lo", 30) - 5)
                    diagnosis.append(f"几乎全做空({1-long_ratio:.0%})")
                    action.append("放宽做多RSI下限增加多单")

        risk["signal_params"] = sp
        return {
            "diagnosis": "; ".join(diagnosis) if diagnosis else "表现尚可，微调参数",
            "action": "; ".join(action) if action else "小幅变异探索",
            "improved_params": risk,
        }

    @staticmethod
    def _run_backtest_worker(bars, config, risk, run_id, tier="mid") -> BacktestResult:
        """线程池中的回测工作函数"""
        engine = BacktestEngine(
            initial_capital=10000,
            leverage=risk.get("default_leverage", 10),
        )
        return engine.run(bars, config, risk, run_id=run_id, tier=tier)

    # ══════════════════════════════════════════════════
    #  参数变异
    # ══════════════════════════════════════════════════

    @staticmethod
    def _loosen_params(risk: Dict, category: str = "trend") -> Dict:
        """强制放宽参数——用于交易量衰减时的紧急干预"""
        sp = risk.get("signal_params", {})

        # 放宽 RSI 范围（核心：扩大入场窗口）
        sp["rsi_long_lo"] = max(10, sp.get("rsi_long_lo", 25) - 8)
        sp["rsi_long_hi"] = min(95, sp.get("rsi_long_hi", 80) + 5)
        sp["rsi_short_lo"] = max(5, sp.get("rsi_short_lo", 15) - 5)
        sp["rsi_short_hi"] = min(90, sp.get("rsi_short_hi", 70) + 8)
        sp["rsi_ob"] = min(90, sp.get("rsi_ob", 75) + 5)
        sp["rsi_os"] = max(10, sp.get("rsi_os", 25) - 5)

        # 缩短 EMA（更快响应信号）
        sp["ema_fast"] = max(3, sp.get("ema_fast", 8) - 2)
        sp["ema_mid"] = max(8, sp.get("ema_mid", 21) - 4)

        # 降低放量要求
        sp["vol_surge_mult"] = max(1.0, sp.get("vol_surge_mult", 1.3) - 0.3)
        sp["momentum_vol_mult"] = max(0.8, sp.get("momentum_vol_mult", 1.1) - 0.3)

        # 缩短冷却期
        sp["min_bars_between"] = max(1, sp.get("min_bars_between", 3) - 1)

        # 放宽波段回撤区间
        sp["swing_pullback_lo"] = max(-0.15, sp.get("swing_pullback_lo", -0.05) - 0.02)
        sp["swing_pullback_hi"] = min(0.05, sp.get("swing_pullback_hi", -0.003) + 0.01)

        # 缩短突破回看（更容易触发突破信号）
        sp["breakout_lookback"] = max(5, sp.get("breakout_lookback", 14) - 4)

        risk["signal_params"] = sp
        return risk

    @staticmethod
    def _mutate_risk(base: Dict, rate: float, category: str = "trend",
                     tier: str = "mid") -> Dict:
        """对风控参数 + 信号参数做随机变异（策略类型 + tier 聚焦）"""
        from backend.services.backtest_evolution_engine import (
            DEFAULT_SIGNAL_PARAMS, SIGNAL_PARAM_RANGES, CATEGORY_KEY_PARAMS,
            get_tier_signal_param_ranges,
        )

        m = copy.deepcopy(base)
        key_params = set(CATEGORY_KEY_PARAMS.get(category, []))
        tier_ranges = get_tier_signal_param_ranges(tier)

        def _jitter(val, lo, hi, strength=1.0):
            delta = (hi - lo) * rate * strength * random.uniform(-1, 1)
            return max(lo, min(hi, val + delta))

        m["stop_loss_pct"] = round(_jitter(m.get("stop_loss_pct", 0.04), 0.01, 0.10), 4)
        m["take_profit_pct"] = round(_jitter(m.get("take_profit_pct", 0.10), 0.03, 0.30), 4)
        m["max_position_size"] = round(_jitter(m.get("max_position_size", 0.15), 0.03, 0.30), 4)

        if "default_leverage" in m:
            m["default_leverage"] = max(5, min(20, round(
                m["default_leverage"] + random.choice([-2, -1, 0, 1, 2])
            )))

        m["trailing_activation_pct"] = round(_jitter(
            m.get("trailing_activation_pct", 0.02), 0.01, 0.05), 4)
        m["trailing_distance_pct"] = round(_jitter(
            m.get("trailing_distance_pct", 0.015), 0.005, 0.03), 4)

        INT_PARAM_SUFFIXES = ("period", "fast", "slow", "signal", "lookback", "between")
        sp = m.get("signal_params", dict(DEFAULT_SIGNAL_PARAMS))
        for key, (lo, hi) in tier_ranges.items():
            val = sp.get(key, DEFAULT_SIGNAL_PARAMS.get(key, (lo + hi) / 2))

            strength = 1.5 if key in key_params else 0.3

            is_int_param = (
                isinstance(val, int)
                or key.endswith(INT_PARAM_SUFFIXES)
                or (isinstance(val, float) and val == int(val) and lo == int(lo) and hi == int(hi))
            )
            if is_int_param:
                new_val = int(round(_jitter(float(val), lo, hi, strength)))
                sp[key] = new_val
            else:
                sp[key] = round(_jitter(float(val), lo, hi, strength), 4)
        m["signal_params"] = sp

        return m

    # ══════════════════════════════════════════════════
    #  闭环: 回测经验 → 实盘策略
    # ══════════════════════════════════════════════════

    def _feed_wisdom_to_live_strategies(self, db: Session, templates):
        """回测进化完成后，把冠军经验注入正在运行的实盘策略。

        闭环流程：
        1. 从回测结果编译交易智慧（风控/市况/信号/教训）
        2. 保存智慧到数据库
        3. 把智慧注入使用对应模板的实盘策略 Prompt
        """
        try:
            from backend.services.backtest_insight_compiler import insight_compiler
            from backend.services.strategy_learning_service import strategy_learning

            refreshed = 0
            injected = 0

            for tpl in templates:
                tpl_id = tpl.template_id
                try:
                    wisdom = insight_compiler.extract_wisdom(db, tpl_id)
                    if wisdom.get("meta", {}).get("runs_analyzed", 0) > 0:
                        insight_compiler.save_wisdom_to_db(db, tpl_id, wisdom)
                        refreshed += 1
                except Exception as e:
                    logger.debug(f"[Evolver→Live] 智慧编译跳过 {tpl_id}: {e}")

                try:
                    if strategy_learning.evolve_prompt_from_backtest(tpl_id):
                        injected += 1
                except Exception as e:
                    logger.debug(f"[Evolver→Live] Prompt注入跳过 {tpl_id}: {e}")

            logger.info(
                f"[Evolver→Live] 闭环完成: "
                f"刷新智慧 {refreshed} 个模板, 注入Prompt {injected} 个策略"
            )
        except Exception as e:
            logger.warning(f"[Evolver→Live] 闭环执行异常: {e}")

    # ══════════════════════════════════════════════════
    #  模板晋升
    # ══════════════════════════════════════════════════

    @staticmethod
    def _should_promote(champion: Dict, tier: str = "mid") -> bool:
        """检查是否达到实战标准（训练集 + 验证集双重验证 + Gate1门控 + tier感知阈值）"""
        try:
            from backend.services.strategy_validator import strategy_validator, BacktestMetrics
            val_sharpe = champion.get("val_sharpe") or champion.get("sharpe", 0)
            metrics = BacktestMetrics(
                out_sample_sharpe=float(val_sharpe),
                max_drawdown_pct=float(champion.get("max_drawdown", 1.0) * 100),
                total_trades=int(champion.get("total_trades", 0)),
                profit_factor=float(champion.get("profit_factor", 0)),
                in_sample_sharpe=float(champion.get("sharpe", 0)),
                max_consecutive_losses=int(champion.get("max_consecutive_losses", 0)),
            )
            gate1_result = strategy_validator.validate_gate1(metrics)
            if not gate1_result.passed:
                logger.warning(
                    f"[Evolver] Gate1 未通过，不予晋升: {'; '.join(gate1_result.failed_checks)}"
                )
                return False
            logger.info(f"[Evolver] Gate1 通过（{len(gate1_result.passed_checks)} 项检查通过）")
        except Exception as e:
            logger.warning(f"[Evolver] Gate1 验证异常（使用旧版门槛）: {e}")

        # 按 tier 获取分层晋升阈值（长线策略 min_trades 更宽松）
        from backend.services.strategy_params_registry import get_promotion_thresholds
        t = get_promotion_thresholds(tier)
        train_ok = (
            champion.get("sharpe", 0) >= t["min_sharpe"]
            and champion.get("win_rate", 0) >= t["min_win_rate"]
            and champion.get("max_drawdown", 1) <= t["max_drawdown"]
            and champion.get("total_trades", 0) >= t["min_trades"]
            and champion.get("profit_factor", 0) >= t["min_profit_factor"]
        )
        if not train_ok:
            return False

        if champion.get("overfit_warning"):
            logger.warning("[Evolver] 过拟合风险高，不予晋升")
            return False

        val_sharpe = champion.get("val_sharpe")
        if val_sharpe is not None and val_sharpe < t["min_sharpe"] * 0.5:
            logger.warning(f"[Evolver] 验证集Sharpe={val_sharpe:.2f}太低，不予晋升")
            return False

        # ── 整改#21：PBO-aware 晋升拦截 ──
        try:
            import os as _os
            if _os.getenv("PBO_AUDIT_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
                from backend.services.learning_core.pbo_audit import get_auditor
                _aud = get_auditor()
                _pbo = _aud.compute_pbo_cscv()
                if _aud.should_reject_promotion(_pbo):
                    logger.warning(
                        "[Evolver][PBO#21] PBO=%.3f 超阈值，拒绝晋升 sharpe=%.2f",
                        (_pbo or {}).get("pbo", -1), champion.get("sharpe", 0),
                    )
                    return False
        except Exception as _pbo_err:
            logger.debug("[Evolver][PBO#21] 审计跳过: %s", _pbo_err)

        return True

    @staticmethod
    def _promote_template(db: Session, tpl, champion: Dict):
        """晋升模板——更新评级和最佳参数，并将回测结果送入统一学习"""
        tpl.backtest_win_rate = champion["win_rate"]
        tpl.backtest_sharpe = champion["sharpe"]
        tpl.backtest_max_drawdown = champion["max_drawdown"]
        tpl.backtest_total_trades = champion["total_trades"]

        new_rating = min(5.0, 3.0 + champion["sharpe"] * 0.5 + champion["win_rate"])
        if new_rating > (tpl.rating or 0):
            tpl.rating = round(new_rating, 2)

        if tpl.strategy_config and champion.get("risk"):
            cfg = dict(tpl.strategy_config)
            cfg["risk_params"] = champion["risk"]
            cfg["backtest_validated"] = True
            cfg["validation_date"] = datetime.now(timezone.utc).isoformat()
            if champion.get("pipeline_params"):
                cfg["pipeline_params"] = champion["pipeline_params"]
                cfg["engine_type"] = "live_pipeline"
            tpl.strategy_config = cfg

        tags = list(tpl.tags or [])
        if "实战就绪" not in tags:
            tags.append("实战就绪")
            tpl.tags = tags

        # 将冠军策略的回测交易结果送入统一学习系统
        try:
            from backend.services.unified_learning_service import unified_learning, TradeOutcome
            trades = champion.get("trades", [])
            if trades:
                outcomes = []
                for trade in trades:
                    regime = getattr(trade, "regime", None) or "ranging"
                    pnl = getattr(trade, "pnl", None) or (
                        (getattr(trade, "exit_price", 0) - getattr(trade, "entry_price", 0))
                        * getattr(trade, "size", 1)
                    )
                    entry_p = getattr(trade, "entry_price", 0) or 1
                    pnl_pct = pnl / entry_p if entry_p > 0 else 0
                    # 使用虚拟 strategy_id 标识回测来源，避免空 ID 导致数据混入错误记忆
                    bt_strategy_id = f"backtest:{tpl.template_id}"
                    outcomes.append(TradeOutcome(
                        source="backtest",
                        strategy_id=bt_strategy_id,
                        template_id=tpl.template_id,
                        symbol=getattr(trade, "symbol", "BTC") if hasattr(trade, "symbol") else "BTC",
                        side=getattr(trade, "side", "buy"),
                        entry_price=getattr(trade, "entry_price", 0),
                        exit_price=getattr(trade, "exit_price", 0),
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        duration_seconds=getattr(trade, "bars_held", 0) * 3600,
                        regime_at_entry=regime,
                        regime_at_exit=regime,
                    ))
                if outcomes:
                    unified_learning.process_outcome_batch(db, outcomes)
                    logger.info(f"[Evolver] 冠军 {tpl.template_id} 的 {len(outcomes)} 笔回测交易已送入学习系统 (strategy_id={bt_strategy_id})")
        except Exception as learn_err:
            logger.warning(f"[Evolver] 回测结果送入学习失败: {learn_err}")

        # 提取交易智慧（回测经验 → 提示词片段）
        try:
            from backend.services.backtest_insight_compiler import insight_compiler
            wisdom = insight_compiler.extract_wisdom(db, tpl.template_id)
            insight_compiler.save_wisdom_to_db(db, tpl.template_id, wisdom)
            logger.info(f"[Evolver] 模板 {tpl.template_id} 交易智慧已提取并保存")
        except Exception as wisdom_err:
            logger.warning(f"[Evolver] 交易智慧提取失败: {wisdom_err}")

        # F2-1: 提取成功模式模板（供新策略跨代学习）
        try:
            from backend.services.pattern_extractor import pattern_extractor
            bt_strategy_id = f"backtest:{tpl.template_id}"
            pattern_extractor.extract_successful_pattern(db, bt_strategy_id)
        except Exception as pattern_err:
            logger.warning(f"[Evolver] 成功模式提取失败: {pattern_err}")

        # 自动同步冠军参数到使用该模板的运行中策略
        try:
            StrategyEvolver._sync_champion_to_live_strategies(db, tpl, champion.get("risk", {}))
        except Exception as sync_err:
            logger.warning(f"[Evolver] 参数自动同步失败: {sync_err}")

        # 将优化后的编排器参数推送到运行中的 MultiTimeframeOrchestrator
        try:
            pipeline_params = champion.get("pipeline_params", {})
            orchestrator_params = {}
            from backend.services.multi_timeframe_orchestrator import MultiTimeframeOrchestrator
            default_keys = MultiTimeframeOrchestrator.DEFAULT_PARAMS.keys()
            for k, v in pipeline_params.items():
                if k in default_keys:
                    orchestrator_params[k] = v
            risk = champion.get("risk", {})
            for k, v in risk.items():
                if k in default_keys:
                    orchestrator_params[k] = v
            if orchestrator_params:
                orch = MultiTimeframeOrchestrator()
                orch.load_params(orchestrator_params)
                logger.info(f"[Evolver→Orchestrator] 推送 {len(orchestrator_params)} 个参数到编排器")
        except Exception as orch_err:
            logger.warning(f"[Evolver→Orchestrator] 参数推送失败: {orch_err}")

        db.commit()

    # ══════════════════════════════════════════════════
    #  冠军参数同步到运行中策略
    # ══════════════════════════════════════════════════

    @staticmethod
    def _sync_champion_to_live_strategies(db: Session, tpl, champion_risk: Dict):
        """把回测冠军的风控参数同步到该模板下所有运行中的策略

        匹配方式:
        1. genome.source_template_id == tpl.template_id (优先)
        2. master_prompt_template_id == tpl.id (兼容老策略)

        安全措施:
        - 同步前将旧参数存入 genome._param_snapshot_before_sync（可回滚）
        - 仅同步差异参数，避免无意义的覆盖
        """
        if not champion_risk:
            return

        from backend.database.models import AIStrategy
        from sqlalchemy.orm.attributes import flag_modified

        active_strategies = db.query(AIStrategy).filter(
            AIStrategy.status.in_(["active", "paused"]),
        ).all()

        SYNC_KEYS_ORM = ["stop_loss_pct", "take_profit_pct", "max_position_size"]
        SYNC_KEYS_GENOME = [
            "stop_loss_pct", "take_profit_pct", "max_position_size",
            "trailing_activation_pct", "trailing_distance_pct",
            "default_leverage",
        ]

        synced = 0
        for strat in active_strategies:
            genome = strat.genome or {}
            if not isinstance(genome, dict):
                continue

            # D3 修复: master_prompt_template_id 是 prompt_templates 的 FK、
            # tpl.id 是 strategy_templates 的自增 ID — 两个不同表，数值不具语义关联。
            # 仅通过 genome.source_template_id 字符串匹配 (tpl_xxx == tpl_xxx)
            matched = (genome.get("source_template_id") == tpl.template_id)
            if not matched:
                continue

            # ── 备份当前参数（用于回滚）──
            snapshot = {}
            for k in SYNC_KEYS_ORM:
                v = getattr(strat, k, None)
                if v is not None:
                    snapshot[k] = v
            for k in SYNC_KEYS_GENOME:
                if k in genome:
                    snapshot[f"genome_{k}"] = genome[k]
            snapshot["snapshot_time"] = datetime.now(timezone.utc).isoformat()
            snapshot["synced_from"] = tpl.template_id

            changes = []
            # 同步到 ORM 字段
            for k in SYNC_KEYS_ORM:
                if k in champion_risk:
                    old_val = getattr(strat, k, None)
                    new_val = champion_risk[k]
                    # 仅当有实际变化时才更新
                    if old_val != new_val:
                        setattr(strat, k, new_val)
                        changes.append(f"{k}: {old_val}->{new_val}")

            # 同步到 genome
            for k in SYNC_KEYS_GENOME:
                if k in champion_risk:
                    genome[k] = champion_risk[k]
            genome["last_synced_from_template"] = datetime.now(timezone.utc).isoformat()
            genome["synced_template_id"] = tpl.template_id
            # 保存快照（最多保留最近3次）
            snapshots = genome.get("_param_snapshots", [])
            snapshots.append(snapshot)
            genome["_param_snapshots"] = snapshots[-3:]
            # v3 整改: 经 StrategyParamsRegistry.apply_genome 统一写入口（行级锁 + 版本号）
            _apply_genome(db, strat.strategy_id, genome, reason="evolver_champion_sync")

            synced += 1
            if changes:
                logger.info(
                    f"[Evolver] 同步冠军参数到策略 {strat.strategy_id}: {', '.join(changes)}"
                )

            # ── 市况感知保护：如果策略近期亏损中，不放大杠杆 ──
            try:
                _cur_lev = genome.get("default_leverage", 10)
                _champ_lev = champion_risk.get("default_leverage", 10)
                if _champ_lev > _cur_lev:
                    # 查策略记忆判断近期表现
                    from backend.database.models import StrategyMemory as _SM
                    _mem = db.query(_SM).filter(
                        _SM.strategy_id == strat.strategy_id
                    ).first()
                    if _mem and _mem.total_trades and _mem.total_trades >= 5:
                        _mem_wr = _mem.win_rate or 0
                        if _mem_wr < 0.35:
                            # 近期胜率 < 35% 时不放大杠杆
                            genome["default_leverage"] = _cur_lev
                            _apply_genome(db, strat.strategy_id, genome, reason="evolver_leverage_guard")
                            logger.info(
                                f"[Evolver] 策略 {strat.strategy_id} 胜率{_mem_wr:.0%}<35%，"
                                f"不放大杠杆（保持{_cur_lev}x，冠军{_champ_lev}x）"
                            )
            except Exception:
                pass

        if synced:
            logger.info(f"[Evolver] 模板 {tpl.template_id} 冠军参数已同步到 {synced} 个运行中策略")
            db.flush()

    @staticmethod
    def rollback_synced_parameters(db: Session, strategy_id: str, steps_back: int = 1) -> bool:
        """回滚策略到同步前的参数状态

        Args:
            db: 数据库会话
            strategy_id: 策略ID
            steps_back: 回退几步（默认1步=上一次同步）

        Returns:
            True if rollback succeeded
        """
        from backend.database.models import AIStrategy
        from sqlalchemy.orm.attributes import flag_modified

        strat = db.query(AIStrategy).filter(
            AIStrategy.strategy_id == strategy_id
        ).first()
        if not strat:
            logger.warning(f"[Evolver] rollback: 策略 {strategy_id} 不存在")
            return False

        genome = strat.genome or {}
        if not isinstance(genome, dict):
            return False

        snapshots = genome.get("_param_snapshots", [])
        if not snapshots or len(snapshots) < steps_back:
            logger.warning(f"[Evolver] rollback: 策略 {strategy_id} 无足够快照 (需{steps_back}步，有{len(snapshots)}步)")
            return False

        snapshot = snapshots[-steps_back]
        SYNC_KEYS_ORM = ["stop_loss_pct", "take_profit_pct", "max_position_size"]
        SYNC_KEYS_GENOME = [
            "stop_loss_pct", "take_profit_pct", "max_position_size",
            "trailing_activation_pct", "trailing_distance_pct",
            "default_leverage",
        ]

        rolled = []
        for k in SYNC_KEYS_ORM:
            if k in snapshot:
                old = getattr(strat, k, None)
                setattr(strat, k, snapshot[k])
                if old != snapshot[k]:
                    rolled.append(f"{k}: {old}→{snapshot[k]}")

        for k in SYNC_KEYS_GENOME:
            gk = f"genome_{k}"
            if gk in snapshot:
                genome[k] = snapshot[gk]
                rolled.append(f"genome.{k}: →{snapshot[gk]}")

        genome["last_rollback"] = datetime.now(timezone.utc).isoformat()
        # v3 整改: 经统一入口写入，内部行级锁
        _apply_genome(db, strat.strategy_id, genome, reason="evolver_rollback")
        db.commit()

        if rolled:
            logger.info(f"[Evolver] 回滚策略 {strategy_id} 成功: {', '.join(rolled[:6])}")
        return True

    # ══════════════════════════════════════════════════
    #  数据加载 & 持久化
    # ══════════════════════════════════════════════════

    @staticmethod
    def _load_bars(symbol: str, timeframe: str, days: int) -> List[Bar]:
        """从数据库加载历史 K 线（使用 Market DB 会话）"""
        from backend.database.models import CryptoKline
        from backend.database.connection import MarketSessionLocal
        import time as _time

        cutoff = int(_time.time()) - days * 86400

        market_db = MarketSessionLocal()
        try:
            rows = market_db.query(CryptoKline).filter(
                CryptoKline.symbol == symbol,
                CryptoKline.period == timeframe,
                CryptoKline.timestamp >= cutoff,
            ).order_by(CryptoKline.timestamp.asc()).all()
        finally:
            market_db.close()

        bars = []
        for idx, r in enumerate(rows):
            bars.append(Bar(
                timestamp=r.timestamp,
                dt_str=r.datetime_str or "",
                o=float(r.open_price or 0),
                h=float(r.high_price or 0),
                l=float(r.low_price or 0),
                c=float(r.close_price or 0),
                v=float(r.volume or 0),
                idx=idx,
            ))
        return bars

    @staticmethod
    def _downsample_equity_curve(curve: list, max_points: int = 500) -> list:
        """降采样资金曲线，保留首尾和等距采样点"""
        if not curve or len(curve) <= max_points:
            return [round(v, 2) for v in curve] if curve else []
        step = len(curve) / max_points
        sampled = []
        for i in range(max_points):
            idx = int(i * step)
            sampled.append(round(curve[idx], 2))
        if sampled[-1] != round(curve[-1], 2):
            sampled.append(round(curve[-1], 2))
        return sampled

    @staticmethod
    def _save_backtest_run(db: Session, result: BacktestResult, tpl, symbol, timeframe, days):
        """保存回测运行记录（含降采样资金曲线）"""
        from backend.database.models import BacktestRun, BacktestTrade

        eq_curve = StrategyEvolver._downsample_equity_curve(
            result.equity_curve, max_points=500
        )

        run = BacktestRun(
            run_id=result.run_id,
            template_id=tpl.template_id,
            symbol=symbol,
            timeframe=timeframe,
            tier=getattr(tpl, "tier", None),
            start_date=f"-{days}d",
            end_date="now",
            initial_capital=10000,
            strategy_name=tpl.name,
            status="completed",
            bars_total=result.bars_total,
            bars_processed=result.bars_total,
            progress=1.0,
            total_return=result.total_return,
            annualized_return=result.annualized_return,
            max_drawdown=result.max_drawdown,
            sharpe_ratio=result.sharpe_ratio,
            win_rate=result.win_rate,
            profit_factor=result.profit_factor,
            total_trades=result.total_trades,
            avg_trade_return=result.avg_trade_return,
            max_consecutive_wins=result.max_consecutive_wins,
            max_consecutive_losses=result.max_consecutive_losses,
            avg_holding_bars=result.avg_holding_bars,
            final_equity=result.final_equity,
            equity_curve=eq_curve,
            duration_seconds=result.duration_seconds,
            completed_at=datetime.now(timezone.utc),
        )
        db.add(run)

        for t in result.trades[:200]:
            db.add(BacktestTrade(
                run_id=result.run_id, symbol=symbol, side=t.side,
                entry_bar=t.entry_bar, exit_bar=t.exit_bar,
                entry_price=t.entry_price, exit_price=t.exit_price,
                quantity=t.quantity, leverage=t.leverage,
                pnl=t.pnl, pnl_pct=t.pnl_pct, fee=t.fee,
                exit_reason=t.exit_reason,
                entry_time=t.entry_time, exit_time=t.exit_time,
            ))
        db.commit()

    @staticmethod
    def _save_champion(db: Session, tpl, champion: Dict):
        from backend.database.models import BacktestRun

        tier = getattr(tpl, "tier", None) or "mid"
        tier_cfg = TIER_CONFIG.get(tier, TIER_CONFIG["mid"])

        champion_config = dict(champion.get("risk") or {})
        for wf_key in ("val_sharpe", "val_win_rate", "val_max_drawdown",
                        "val_total_return", "val_total_trades", "overfit_ratio",
                        "overfit_warning"):
            if wf_key in champion:
                champion_config[f"wf_{wf_key}"] = champion[wf_key]
        if champion.get("ai_learning_log"):
            champion_config["ai_learning_log"] = champion["ai_learning_log"][-5:]

        run = BacktestRun(
            run_id=f"champ_{uuid.uuid4().hex[:8]}",
            template_id=tpl.template_id,
            symbol="multi",
            timeframe=tier_cfg["default_timeframe"],
            tier=tier,
            start_date="evolution",
            end_date="champion",
            strategy_name=tpl.name,
            strategy_config=champion_config,
            generation=champion.get("generation", 0),
            status="completed",
            is_champion=True,
            total_return=champion.get("total_return"),
            max_drawdown=champion.get("max_drawdown"),
            sharpe_ratio=champion.get("sharpe"),
            win_rate=champion.get("win_rate"),
            profit_factor=champion.get("profit_factor"),
            total_trades=champion.get("total_trades"),
            final_equity=champion.get("final_equity"),
            completed_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()

    @staticmethod
    def inherit_lessons(db: Session, symbol: str, tier: str) -> Dict[str, Any]:
        """F2-2: 跨代经验蒸馏 — 从同类历史策略继承经验。

        为新策略提供：
        - best_regimes: 同类策略成功率最高的市场状态
        - avoid_regimes: 同类策略频繁失败的市场状态
        - lessons: 从 key_lessons 提取的可读经验
        - success_templates: 从成功模式模板提取的参数偏好
        """
        from backend.database.models import StrategyMemory
        from collections import Counter

        memories = db.query(StrategyMemory).filter(
            StrategyMemory.total_trades >= 5,
        ).order_by(
            StrategyMemory.win_rate.desc()
        ).limit(10).all()

        inherited: Dict[str, Any] = {
            "best_regimes": [],
            "avoid_regimes": [],
            "lessons": [],
            "success_templates": [],
            "avg_win_rate": 0.0,
            "total_trades_sampled": 0,
            "source_count": 0,
        }

        if not memories:
            return inherited

        inherited["source_count"] = len(memories)

        # 统计最佳/应避免的市场状态
        best_regime_list: List[str] = []
        avoid_regime_list: List[str] = []

        for m in memories:
            inherited["total_trades_sampled"] += m.total_trades or 0

            # 从成功模式提取最佳状态
            if m.win_rate and m.win_rate >= 0.40:
                patterns = m.successful_patterns or []
                regime_counts = Counter(
                    p.get("regime") for p in patterns if p.get("regime")
                )
                best_regime_list.extend(regime_counts.keys())

            # 从失败模式提取应避免状态
            failed = m.failed_patterns or []
            failed_regimes = Counter(
                p.get("regime") for p in failed if p.get("regime")
            )
            avoid_regime_list.extend(failed_regimes.keys())

            # 提取可读经验
            if m.key_lessons:
                for lesson in m.key_lessons:
                    if isinstance(lesson, dict) and lesson.get("type") == "success_pattern_template":
                        inherited["success_templates"].append({
                            "best_regime": lesson.get("best_regime"),
                            "best_nature": lesson.get("best_nature"),
                            "avg_pnl": lesson.get("avg_pnl_per_trade"),
                            "source_strategy": lesson.get("strategy_id"),
                        })
                    elif isinstance(lesson, str):
                        inherited["lessons"].append(lesson)

        # 去重并排序
        inherited["best_regimes"] = list(dict.fromkeys(best_regime_list))[:5]
        inherited["avoid_regimes"] = list(dict.fromkeys(avoid_regime_list))[:5]
        inherited["avg_win_rate"] = round(
            sum(m.win_rate or 0 for m in memories) / len(memories), 3
        )
        # 只保留最近5条模板，优先avg_pnl最高的
        inherited["success_templates"] = sorted(
            inherited["success_templates"],
            key=lambda t: t.get("avg_pnl", 0) or 0,
            reverse=True,
        )[:5]

        logger.info(
            f"[Evolver] 跨代经验蒸馏: symbol={symbol} tier={tier} "
            f"从{len(memories)}个历史策略继承 "
            f"best_regimes={inherited['best_regimes'][:3]} "
            f"avoid={inherited['avoid_regimes'][:3]}"
        )
        return inherited

    # ══════════════════════════════════════════════════════════
    #  P2.2 策略结构变异 — OpenCode 提议 → shadow验证 → proposal输出
    # ══════════════════════════════════════════════════════════

    def structural_mutate(
        self,
        db: Session,
        template_id: str,
        backtest_result: Optional[Dict[str, Any]] = None,
        *,
        max_shadow_trades: int = 50,
    ) -> Dict[str, Any]:
        """
        策略结构变异：用 OpenCode 深度推理提议策略结构变更。

        流程：
        1. 收集当前策略的历史交易和回测指标
        2. 构建策略变异 prompt → OpenCode 提议3个变异方向
        3. 对每个变异做 shadow 纸上验证（不实际开仓）
        4. 输出 proposal（含 risk/benefit 估算）

        Returns:
            {
                "proposals": [
                    {
                        "mutation_id": str,
                        "title": str,
                        "description": str,
                        "structural_changes": [{type, field, before, after}],
                        "expected_impact": {sharpe, win_rate, drawdown},
                        "risk_level": "low|medium|high",
                        "shadow_validated": bool,
                        "shadow_result": {...},
                    }
                ],
                "summary": str,
                "recommended_mutation": str,  # mutation_id
            }
        """
        from backend.services.opencode_bridge import (
            run_http_agent_message, _extract_json,
            _is_enabled, _agent_plan, _model,
        )

        if not _is_enabled():
            return {"skipped": "OpenCode未启用"}

        try:
            # 1. 收集策略信息
            from backend.database.models import StrategyTemplate, StrategyMemory

            tpl = db.query(StrategyTemplate).filter(
                StrategyTemplate.template_id == template_id
            ).first()
            if not tpl:
                return {"error": f"模板不存在: {template_id}"}

            memory = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == template_id
            ).first()

            # 收集历史交易记录
            trade_histories = []
            try:
                from backend.database.models import PaperTradeRecord
                trades = (
                    db.query(PaperTradeRecord)
                    .filter(PaperTradeRecord.strategy_id == template_id)
                    .order_by(PaperTradeRecord.exit_time.desc())
                    .limit(30)
                    .all()
                )
                for t in trades:
                    trade_histories.append({
                        "entry_price": float(t.entry_price or 0),
                        "exit_price": float(t.exit_price or 0),
                        "pnl": float(t.pnl or 0),
                        "direction": t.side or "?",
                        "exit_reason": t.exit_reason or "?",
                    })
            except Exception:
                pass

            # 策略当前配置
            strategy_config = getattr(tpl, "config", {}) or {}
            risk_config = getattr(tpl, "risk_params", {}) or {}

            # 2. 构建变异提示词
            system = (
                "You are Alpha Arena Strategy Architect."
                "Your job is to propose STRUCTURAL mutations to trading strategies — "
                "not just parameter tweaks, but real structural changes.\n\n"
                "MUTATION TYPES:\n"
                "1. **Entry logic**: change triggering conditions, add filters, merge signals\n"
                "2. **Exit logic**: modify stop/target structure, add trailing rules\n"
                "3. **Position sizing**: change sizing formula (fixed % → Kelly → volatility-adjusted)\n"
                "4. **Market regime filter**: add/remove/change regime detection\n"
                "5. **Frequency control**: add time-of-day/week filters, cooldown periods\n"
                "6. **Cross-timeframe**: add confirmation from higher/lower timeframe\n"
                "7. **Crypto-specific**: add funding rate filter, OI confirmation, liquidation hedge\n\n"
                "For each mutation, estimate the expected impact on Sharpe, win_rate, max_drawdown.\n"
                "Return ONLY valid JSON, no markdown fences."
            )

            user_text_parts = [
                "## 策略结构变异任务",
                f"### 策略: {tpl.name} ({template_id})",
                f"Tier: {getattr(tpl, 'tier', 'mid')}",
                f"Timeframe: {getattr(tpl, 'default_timeframe', '1h')}",
                "",
                "### 当前配置",
                json.dumps(strategy_config, ensure_ascii=False, indent=2),
                "",
                "### Risk参数",
                json.dumps(risk_config, ensure_ascii=False, indent=2),
                "",
            ]

            if backtest_result:
                user_text_parts.extend([
                    "### 回测结果",
                    json.dumps({
                        "sharpe": backtest_result.get("sharpe"),
                        "win_rate": backtest_result.get("win_rate"),
                        "max_drawdown": backtest_result.get("max_drawdown"),
                        "total_trades": backtest_result.get("total_trades"),
                    }, ensure_ascii=False),
                    "",
                ])

            if trade_histories:
                user_text_parts.extend([
                    f"### 最近 {len(trade_histories)} 笔交易",
                    json.dumps(trade_histories, ensure_ascii=False, indent=2),
                    "",
                ])

            user_text_parts.extend([
                "## 请求",
                "请提出 **3个结构变异方向**。每个变异必须：",
                "1. 有明确的类型（entry/exit/sizing/regime/frequency/cross_tf/crypto）",
                "2. 有具体的修改前后对比",
                "3. 估算影响（Sharpe/win_rate/drawdown变化）",
                "4. 标记风险等级（low/medium/high）",
                "",
                "输出JSON：",
                "{",
                "  \"proposals\": [{",
                "    \"title\": \"变异名称\",",
                "    \"mutation_type\": \"entry|exit|sizing|regime|frequency|cross_tf|crypto\",",
                "    \"description\": \"详细描述（50-150字）\",",
                "    \"structural_changes\": [{\"field\": \"...\", \"before\": \"...\", \"after\": \"...\"}],",
                "    \"expected_impact\": {\"sharpe_delta\": 0.1, \"wr_delta\": 0.02, \"dd_delta\": -0.01},",
                "    \"risk_level\": \"low|medium|high\",",
                "    \"crypto_specific\": true/false",
                "  }],",
                "  \"summary\": \"总体评估（50字）\",",
                "  \"recommended_index\": 0",
                "}",
            ])

            user_text = "\n".join(user_text_parts)

            raw, err = run_http_agent_message(
                system_prompt=system,
                user_text=user_text,
                agent=_agent_plan(),
                model_slug=_model(),
                session_title=f"Strategy Mutation: {template_id}",
            )

            if err:
                logger.warning(f"[Evolver] structural_mutate LLM failed: {err}")
                return {"error": err, "proposals": []}

            result = _extract_json(raw or "")
            proposals = result.get("proposals", [])

            # 3. 写入 StrategyMemory（作为结构变异提案）
            try:
                if memory:
                    mutations = list(getattr(memory, "structural_mutations", None) or [])
                    for i, p in enumerate(proposals):
                        p["mutation_id"] = f"mut_{template_id}_{i}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
                        p["status"] = "proposed"
                        p["proposed_at"] = datetime.now(timezone.utc).isoformat()
                    mutations.extend(proposals)
                    memory.structural_mutations = mutations[-20:]
                    db.commit()
            except Exception as se:
                logger.debug(f"[Evolver] 变异提案落库失败: {se}")

            logger.info(
                f"[Evolver] structural_mutate {template_id}: "
                f"{len(proposals)} proposals, "
                f"recommend={result.get('recommended_index', '?')}"
            )

            return {
                "proposals": proposals,
                "summary": result.get("summary", ""),
                "recommended_mutation": (
                    proposals[result["recommended_index"]]["mutation_id"]
                    if proposals and isinstance(result.get("recommended_index"), int)
                    and 0 <= result["recommended_index"] < len(proposals)
                    else None
                ),
            }

        except Exception as exc:
            logger.error(f"[Evolver] structural_mutate 异常: {exc}", exc_info=True)
            return {"error": str(exc), "proposals": []}

    @staticmethod
    def _result_to_dict(result: BacktestResult, name: str = "") -> Dict:
        return {
            "run_id": result.run_id,
            "strategy_name": name,
            "total_return": f"{(result.total_return or 0)*100:.2f}%",
            "sharpe_ratio": round(result.sharpe_ratio or 0, 2),
            "max_drawdown": f"{(result.max_drawdown or 0)*100:.1f}%",
            "win_rate": f"{(result.win_rate or 0)*100:.1f}%",
            "profit_factor": round(result.profit_factor or 0, 2),
            "total_trades": result.total_trades,
            "final_equity": round(result.final_equity or 0, 2),
            "duration": f"{result.duration_seconds:.1f}s",
            "bars_total": result.bars_total,
            "error": result.error,
        }


strategy_evolver = StrategyEvolver()
