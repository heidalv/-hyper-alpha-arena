"""
统一学习服务 — 三源（实盘/模拟/回测）统一学习入口

核心职责：
1. 接收来自实盘、模拟、回测的交易结果（TradeOutcome）
2. 按来源加权更新环境-绩效矩阵（StrategyRegimeScore）
3. 更新策略记忆（StrategyMemory）
4. 检测实盘与回测的偏离（过拟合检测）
5. 触发参数自适应 / 重进化
"""

import math
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SOURCE_WEIGHTS = {"live": 1.0, "paper": 0.6, "backtest": 0.3}

TIER_TO_NATURE = {"short": "scalp", "mid": "swing", "long": "position"}

# EMA 更新平滑因子基数（乘以 source weight）
# Increased from 0.1 to 0.15 for faster adaptation to market regime changes
EMA_ALPHA_BASE = 0.15
# 偏离阈值：实盘得分低于回测得分的这个比例就标记过拟合
# Tightened from 0.5 to 0.4 for earlier overfit detection
DIVERGENCE_THRESHOLD = 0.4
# 触发重进化的最低样本数
MIN_SAMPLES_FOR_DIVERGENCE = 10
# 触发参数自适应的连续亏损次数
# Increased from 5 to 7 to reduce false-positive risk reduction
ADAPT_LOSS_STREAK = 7


@dataclass
class TradeOutcome:
    """统一的交易结果结构（内存对象，非数据库表）"""
    source: str               # "live" | "paper" | "backtest"
    strategy_id: str = ""
    template_id: str = ""
    symbol: str = ""
    side: str = ""            # "buy" | "sell"
    tier: str = "mid"         # legacy: "short" | "mid" | "long"
    trade_nature: str = ""    # "scalp" | "swing" | "position" (优先于tier)
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    duration_seconds: int = 0
    regime_at_entry: str = "ranging"
    regime_at_exit: str = "ranging"
    fingerprint_at_entry: Optional[Dict] = None
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    # ── 真实仓位规模与开仓时间（v2 修复 Bug B / Bug C） ──
    # 调用方应传入真实数值；旧调用方未设置时从 metadata 或 entry_price 兜底。
    position_size: float = 0.0
    opened_at: Optional[datetime] = None
    peak_pnl_pct: float = 0.0
    exit_pnl_pct: float = 0.0
    retention_ratio: Optional[float] = None
    health_at_exit: Optional[float] = None
    reversal_level_at_exit: str = ""
    exit_channel: str = ""
    # —— 防止 ghost 循环：回填路径（_tick_outcome_batch）传 False，
    #    这样 process_outcome 只做学习更新，不再生成新的 StrategyTrade。
    persist_trade: bool = True


def _safe(val, default=0.0):
    if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
        return default
    return float(val)


def calc_composite_score(
    win_rate: float,
    avg_pnl_pct: float,
    sharpe: float,
    max_dd: float,
    sample_count: int,
) -> float:
    """多维度加权评分，样本不足时打折。

    核心理念：胜率仅占 15%，盈亏比（通过 sharpe+avg_pnl 体现）占主导。
    胜率高不挣钱 → 低分；胜率低但盈亏比高 → 可能高分。
    """
    sample_confidence = min(1.0, sample_count / 30)
    raw = (
        _safe(win_rate) * 0.15
        + min(max(_safe(sharpe) / 3.0, 0), 1.0) * 0.35
        + (1 - min(_safe(max_dd), 1.0)) * 0.20
        + min(max(_safe(avg_pnl_pct) * 10, 0), 1.0) * 0.30
    )
    return round(raw * sample_confidence, 4)


class UnifiedLearningService:
    """三源统一学习引擎（单例）"""

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
        self._loss_streaks: Dict[str, int] = {}
        self._trade_counters: Dict[str, int] = {}
        self._prompt_evo_interval = 20
        logger.info("[UnifiedLearning] 统一学习服务初始化完成")

    @staticmethod
    def _compute_decision_quality(
        pnl: float, pnl_pct: float, side: str,
        confidence: float, close_reason: str,
        duration_seconds: int, nature: str,
    ) -> float:
        """P2-1: 交易关闭时自动计算决策质量评分 (0.0~1.0)

        评分维度:
        - PnL 得分 (0-0.40): 盈利=高分，亏损=低分
        - 出场质量 (0-0.30): TP/止盈=好，止损/强平=差
        - 方向准确 (0-0.30): 做多盈利或做空盈利=方向正确
        """
        # ── 1. PnL 得分 (0~0.40) ──
        _pnl = float(pnl or 0.0)
        _pnl_pct = float(pnl_pct or 0.0)
        if _pnl > 0:
            # 盈利：按 pnl_pct 映射，超 5% 给满分
            _pnl_score = min(0.40, _pnl_pct * 8.0)  # 5% → 0.40
        elif _pnl < 0:
            # 亏损：线性扣分，亏 3% 以上→0
            _pnl_score = max(0.0, 0.20 + _pnl_pct * 6.67)  # -3%→0, 0%→0.20
        else:
            _pnl_score = 0.20  # 平出给基础分

        # ── 2. 出场质量得分 (0~0.30) ──
        _reason_lower = (close_reason or "").lower()
        if any(kw in _reason_lower for kw in ("tp", "take_profit", "safety_tp", "target")):
            _exit_score = 0.30 if _pnl >= 0 else 0.15  # TP但亏损=假TP
        elif any(kw in _reason_lower for kw in ("trailing", "trailing_hit")):
            _exit_score = 0.25 if _pnl >= 0 else 0.10
        elif any(kw in _reason_lower for kw in ("ai_take_profit", "breakeven")):
            _exit_score = 0.22
        elif any(kw in _reason_lower for kw in ("sl", "stop_loss", "force_close", "liquidation")):
            _exit_score = 0.0  # 止损=出场质量0分
        elif any(kw in _reason_lower for kw in ("ai_cut_loss", "manual")):
            _exit_score = 0.10
        else:
            _exit_score = 0.15  # 未知原因给中等偏下

        # ── 3. 方向准确得分 (0~0.30) ──
        _side_is_long = (side or "").lower() in ("long", "buy")
        _side_is_short = (side or "").lower() in ("short", "sell")
        if _pnl > 0:
            # 盈利=方向正确
            _dir_score = 0.30
        elif _pnl < 0:
            # 亏损：小亏给部分分，大亏=0
            _dir_score = max(0.0, 0.15 + _pnl_pct * 5.0) if _pnl_pct > -0.03 else 0.0
        else:
            _dir_score = 0.15

        # ── 4. 置信度打折 ──
        _conf = float(confidence or 0.5)
        _conf_mult = max(0.5, min(1.0, _conf))

        # ── 5. 持仓时长合理性 (短线/中线/长线匹配) ──
        _dur_hours = max(1, int(duration_seconds or 0)) / 3600.0
        _nature_lower = (nature or "swing").lower()
        _dur_mult = 1.0
        if _nature_lower in ("scalp",) and _dur_hours > 4:
            _dur_mult = 0.7  # 超短线拿太久=决策犹豫
        elif _nature_lower in ("position",) and _dur_hours < 1 and _pnl < 0:
            _dur_mult = 0.6  # 长线仓位被秒止损=入场错误
        elif _nature_lower in ("swing",) and _dur_hours < 0.25 and _pnl < 0:
            _dur_mult = 0.8  # 波段秒亏=入场错误

        _raw = (_pnl_score + _exit_score + _dir_score) * _conf_mult * _dur_mult
        return round(min(1.0, max(0.0, _raw)), 4)

    def process_outcome(self, db: Session, outcome: TradeOutcome):
        """统一处理所有来源的交易结果"""
        try:
            weight = SOURCE_WEIGHTS.get(outcome.source, 0.3)

            # 2026-06-11: 让 AIStrategy.learning_enabled 真正生效。
            # 关闭学习的策略仍持久化交易记录（Kelly/统计需要事实数据），
            # 但跳过记忆/绩效矩阵/提示词进化等学习环节。
            if not self._is_learning_enabled(outcome.strategy_id):
                if getattr(outcome, "persist_trade", True):
                    self._persist_strategy_trade(db, outcome)
                db.commit()
                logger.info(
                    f"[UnifiedLearning] 策略 {outcome.strategy_id} learning_enabled=false，"
                    f"仅落交易记录，跳过学习环节"
                )
                return

            if getattr(outcome, "persist_trade", True):
                self._persist_strategy_trade(db, outcome)
            self._update_regime_score(db, outcome, weight)
            self._update_strategy_memory(db, outcome)
            self._track_loss_streak(outcome)
            self._check_adaptation_needed(db, outcome)
            self._check_divergence(db, outcome)
            self._check_prompt_evolution_trigger(db, outcome)
            self._evaluate_wisdom_effectiveness(db, outcome)

            # MLTO thesis 归因学习
            try:
                meta = outcome.metadata if isinstance(outcome.metadata, dict) else {}
                if meta.get("thesis_id"):
                    from backend.database.connection import AnalyticsSessionLocal
                    from backend.services.mlto.learning_bridge import record_outcome as mlto_record
                    _adb = AnalyticsSessionLocal()
                    try:
                        mlto_record(_adb, outcome, analytics_db=_adb)
                    finally:
                        _adb.close()
                    try:
                        from backend.services.learning_bus import get_learning_bus
                        get_learning_bus().enqueue_thesis_postmortem(outcome)
                    except Exception:
                        pass
            except Exception as _mlto_le:
                logger.debug("[UnifiedLearning] MLTO outcome skip: %s", _mlto_le)

            db.commit()

            # Fix 7: 短线平仓记录币种熔断追踪（在短线硬闸门生效前提下）
            try:
                from backend.services.short_tier_entry_gate import record_short_tier_outcome
                _is_short = (outcome.tier in ("short",)) or (
                    (outcome.trade_nature or "") in ("scalp", "intraday")
                )
                if _is_short and outcome.symbol:
                    record_short_tier_outcome(outcome.symbol, float(outcome.pnl or 0))
            except Exception:
                pass

            # TrendAgent scenario 命中率评分（trend_follow/position 平仓）
            try:
                nature = outcome.trade_nature or TIER_TO_NATURE.get(outcome.tier, "swing")
                if nature in ("trend_follow", "position"):
                    _pid = (outcome.metadata or {}).get("paper_position_id")
                    if _pid:
                        from backend.services.trend_prediction_service import trend_prediction_service
                        trend_prediction_service.score_on_close(
                            paper_position_id=int(_pid),
                            exit_price=float(outcome.exit_price or 0),
                            close_reason=str((outcome.metadata or {}).get("close_reason") or ""),
                            side=outcome.side or "long",
                            pnl_pct=float(outcome.pnl_pct or 0),
                        )
            except Exception as _tps_err:
                logger.debug(f"[TrendPrediction] 平仓评分跳过: {_tps_err}")

            # ── 统一后端调度 (L2 收敛) ──
            # 原 6 个内联后端调用（causal_diagnosis/reflexion/promotion/
            # template_stats/qaa/factor_joint/drift）+ 旧 LearningBus 的
            # review/miner/pattern/causal_discovery 全部收敛到此一处。
            # 新增后端只需在 backend_loader 注册，无需改动本方法。
            try:
                from backend.services.learning_registry_bridge import get_registry
                get_registry().handle_all(db, outcome)
            except Exception as _be_err:
                logger.debug(f"[UnifiedLearning] 后端调度跳过: {_be_err}")

            nature = outcome.trade_nature or TIER_TO_NATURE.get(outcome.tier, "swing")
            logger.info(
                f"[UnifiedLearning] 处理完成: {outcome.source}/{outcome.strategy_id} "
                f"{outcome.symbol}[{nature}] pnl={outcome.pnl:.4f} "
                f"regime={outcome.regime_at_entry}"
            )
        except Exception as e:
            err_text = str(e)
            if "ForeignKeyViolation" in err_text or "strategy_trades_strategy_id_fkey" in err_text:
                logger.warning(
                    "[UnifiedLearning] 外键约束跳过 strategy_id=%s: %s",
                    getattr(outcome, "strategy_id", None),
                    err_text[:240],
                )
                try:
                    db.rollback()
                except Exception:
                    pass
                return
            logger.error(f"[UnifiedLearning] 处理失败: {e}", exc_info=True)
            db.rollback()
            raise

    def _is_learning_enabled(self, strategy_id) -> bool:
        """检查策略的 learning_enabled 字段（带 60s TTL 缓存）。

        2026-06-11 前该字段是 UI/DB 假字段：用户在前端关闭学习不生效。
        查询失败或策略不存在时默认放行（True），避免误杀学习管线。
        """
        if not strategy_id:
            return True
        import time as _time
        cache = getattr(self, "_learning_enabled_cache", None)
        if cache is None:
            cache = {}
            self._learning_enabled_cache = cache
        hit = cache.get(strategy_id)
        if hit is not None and (_time.time() - hit[1]) < 60:
            return hit[0]
        enabled = True
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import AIStrategy
            sdb = SessionLocal()
            try:
                row = sdb.query(AIStrategy.learning_enabled).filter(
                    AIStrategy.strategy_id == str(strategy_id)
                ).first()
                if row is not None and row[0] is not None:
                    enabled = bool(row[0])
            finally:
                sdb.close()
        except Exception as e:
            logger.debug(f"[UnifiedLearning] learning_enabled 查询失败(默认放行): {e}")
        cache[strategy_id] = (enabled, _time.time())
        return enabled

    def process_outcome_batch(self, db: Session, outcomes: List[TradeOutcome]):
        """批量处理（回测场景）— 与 process_outcome 逻辑对齐"""
        for outcome in outcomes:
            try:
                weight = SOURCE_WEIGHTS.get(outcome.source, 0.3)
                self._update_regime_score(db, outcome, weight)
                self._update_strategy_memory(db, outcome)
            except Exception as e:
                logger.warning(f"[UnifiedLearning] 批量处理单条失败: {e}")
        try:
            db.commit()
        except Exception:
            db.rollback()

    # ══════════════════════════════════════════════════
    #  写入 StrategyTrade（供 strategy_learning_service 复盘）
    # ══════════════════════════════════════════════════

    def _resolve_strategy_id_for_fk(self, db: Session, raw_id: Optional[str]) -> Optional[str]:
        """解析/校验 strategy_trades 外键：仅返回 ai_strategies.strategy_id 中存在的值。

        修复（2026-06-25）：原逻辑对不在 ai_strategies 表里的系统策略（scalp_router、
        cross_cycle_BTC、swing_agent、trend_agent 等）返回 None，导致这些策略的平仓学习
        全部被跳过（11325 次平仓学习但 strategy_memories 自 6/22 起无更新）。
        现对已知的系统策略自动创建占位父行，让 FK 满足、学习数据能正常落盘。
        """
        if not raw_id:
            return None
        sid = str(raw_id).strip()
        if not sid:
            return None
        from backend.database.models import AIStrategy

        row = (
            db.query(AIStrategy.strategy_id)
            .filter(AIStrategy.strategy_id == sid)
            .first()
        )
        if row:
            return sid
        # 误传数字主键 id 时尝试映射
        if sid.isdigit():
            row2 = (
                db.query(AIStrategy.strategy_id)
                .filter(AIStrategy.id == int(sid))
                .first()
            )
            if row2:
                return str(row2[0])
        # template_id 误作 strategy_id
        row3 = (
            db.query(AIStrategy.strategy_id)
            .filter(AIStrategy.parent_strategy_id == sid)
            .order_by(AIStrategy.id.desc())
            .first()
        )
        if row3:
            return str(row3[0])

        # ── 修复（2026-06-25）：为已知的系统策略自动创建占位父行 ──
        # scalp_router / cross_cycle_* / swing_agent / trend_agent 等是合法的系统
        # 策略模块，不在 ai_strategies 表里（它们不是 AI 生成的），但它们的平仓
        # 学习同样重要。为它们创建占位父行让 FK 满足。
        _SYSTEM_STRATEGY_PREFIXES = (
            "scalp_router", "cross_cycle_", "swing_agent", "trend_agent",
            "scalp_", "short_tier_", "paper_engine",
        )
        if any(sid.startswith(p) for p in _SYSTEM_STRATEGY_PREFIXES):
            try:
                # [2026-08-07 v6 fix] 占位账户 id=1 不存在（accounts 实际 id=7/14/146），
                # 外键约束导致 INSERT ai_strategies 失败 → 每周经验提炼整体中断。
                # 改为取最小真实账户；无账户则跳过（不硬造非法行）。
                from backend.database.models import Account
                acc = db.query(Account.id).order_by(Account.id.asc()).first()
                if not acc:
                    logger.warning(
                        "[UnifiedLearning] 无可用账户，跳过 %s 占位父行创建", sid[:20]
                    )
                    return None
                placeholder = AIStrategy(
                    strategy_id=sid,
                    name=f"系统策略/{sid}",
                    status="active",
                    account_id=acc[0],
                )
                db.add(placeholder)
                db.flush()
                logger.info("[UnifiedLearning] 为系统策略 %s 创建占位父行（首次出现）", sid[:20])
                return sid
            except Exception as e:
                logger.debug("[UnifiedLearning] 占位父行创建失败 %s: %s", sid[:20], str(e)[:60])
                return None

        return None

    def _persist_strategy_trade(self, db: Session, outcome: TradeOutcome):
        """将每笔平仓结果写入 strategy_trades 表，使 run_periodic_review 有数据可用"""
        try:
            from backend.database.models import StrategyTrade
            from datetime import timedelta as _td

            strategy_id = self._resolve_strategy_id_for_fk(
                db, outcome.strategy_id or outcome.template_id
            )
            if not strategy_id:
                logger.debug(
                    "[UnifiedLearning] 跳过 StrategyTrade：无效 strategy_id=%s template_id=%s",
                    outcome.strategy_id,
                    outcome.template_id,
                )
                return

            _meta = outcome.metadata if isinstance(outcome.metadata, dict) else {}

            # paper 平仓补偿需要稳定幂等键，优先按 paper_position_id 去重。
            # 旧的 10 分钟价格窗口只能防 ghost，不能可靠识别补偿重放。
            _paper_position_id = _meta.get("paper_position_id")
            if _paper_position_id:
                try:
                    from sqlalchemy import cast
                    from sqlalchemy.types import Text
                    dup_by_position = (
                        db.query(StrategyTrade)
                        .filter(
                            StrategyTrade.strategy_id == strategy_id,
                            cast(StrategyTrade.decision_context, Text).contains(
                                f'"paper_position_id": {_paper_position_id}'
                            ),
                        )
                        .first()
                    )
                    if dup_by_position is not None:
                        logger.info(
                            f"[UnifiedLearning] 跳过重复 StrategyTrade(paper_position_id): "
                            f"{strategy_id}/{outcome.symbol}/pos={_paper_position_id}"
                        )
                        return
                except Exception as _pid_dup_err:
                    logger.debug(f"[UnifiedLearning] paper_position_id 去重检查失败(放行): {_pid_dup_err}")

            # —— 软去重防御（ghost-guard，10 min 窗口内同 entry/exit/pnl 视为同笔）——
            try:
                _entry = round(_safe(outcome.entry_price), 8)
                _exit = round(_safe(outcome.exit_price), 8)
                _pnl = round(_safe(outcome.pnl), 4)
                _since = datetime.now(timezone.utc) - _td(minutes=10)
                dup = (
                    db.query(StrategyTrade)
                    .filter(
                        StrategyTrade.strategy_id == strategy_id,
                        StrategyTrade.symbol == outcome.symbol,
                        StrategyTrade.side == outcome.side,
                        StrategyTrade.entry_price == _entry,
                        StrategyTrade.exit_price == _exit,
                        StrategyTrade.closed_at >= _since.replace(tzinfo=None),
                    )
                    .first()
                )
                if dup is not None and abs((float(dup.pnl or 0.0)) - _pnl) < 1e-4:
                    logger.warning(
                        f"[UnifiedLearning] 跳过重复 StrategyTrade(ghost-guard): "
                        f"{strategy_id}/{outcome.symbol}/{outcome.side} "
                        f"entry={_entry} exit={_exit} pnl={_pnl}"
                    )
                    return
            except Exception as _dup_err:
                logger.debug(f"[UnifiedLearning] ghost-guard 检查失败(放行): {_dup_err}")

            nature = outcome.trade_nature or TIER_TO_NATURE.get(outcome.tier, "swing")

            # ── Bug B 修复：position_size 用真实仓位（按调用方 → metadata → 0 顺序兜底）──
            #   原错误公式 abs(exit_price * 1) 与仓位无关，导致 168/168 笔 position_size==exit_price。
            _real_size = _safe(getattr(outcome, "position_size", 0))
            if _real_size <= 0:
                _real_size = _safe(_meta.get("position_size") or _meta.get("size")
                                   or _meta.get("quantity") or 0)
            # 仍为 0 时记 0（不再用 exit_price 充数）

            # ── Bug C 修复：opened_at 用真实开仓时间（按调用方 → duration 反推 → now-1s 兜底） ──
            #   原默认值 server_default=current_timestamp 会把 opened_at 写成"插入瞬间"，
            #   导致 168/168 笔 opened_at==closed_at。
            _closed_at_dt = datetime.now(timezone.utc)
            _opened_at_dt = getattr(outcome, "opened_at", None)
            if _opened_at_dt is None and outcome.duration_seconds:
                from datetime import timedelta as _td
                _opened_at_dt = _closed_at_dt - _td(seconds=int(outcome.duration_seconds))
            if _opened_at_dt is None:
                from datetime import timedelta as _td
                _opened_at_dt = _closed_at_dt - _td(seconds=1)
            # SQLite TIMESTAMP 字段不接受带 tz 的 datetime，统一转 naive UTC
            if hasattr(_opened_at_dt, "tzinfo") and _opened_at_dt.tzinfo is not None:
                _opened_at_dt = _opened_at_dt.replace(tzinfo=None)

            # ── P2-1: 自动计算决策质量评分 ──
            _close_reason = _meta.get("close_reason", "")
            _dq_score = self._compute_decision_quality(
                pnl=_safe(outcome.pnl),
                pnl_pct=_safe(outcome.pnl_pct),
                side=outcome.side,
                confidence=outcome.confidence,
                close_reason=_close_reason,
                duration_seconds=outcome.duration_seconds,
                nature=nature,
            )
            _decision_context = {
                "regime": outcome.regime_at_entry,
                "source": outcome.source,
                "nature": nature,
                "confidence": outcome.confidence,
                "template_id": outcome.template_id,
                "tier": outcome.tier,
            }
            for _key in (
                "paper_position_id",
                "paper_order_id",
                "closed_at",
                "close_reason",
                "exchange",
                "market_type",
                "snapshot_id",
                "data_source",
                "agent_source",
                "agent_envelope",
                "alignment_score",
                "cited_fact_ids",
                "cited_facts",
            ):
                if _meta.get(_key) is not None:
                    _decision_context[_key] = _meta.get(_key)

            # 标记已由学习管线处理，防止 _tick_outcome_batch 重复 EMA 更新
            _decision_context["_learning_loop_processed"] = True

            trade = StrategyTrade(
                strategy_id=strategy_id,
                symbol=outcome.symbol,
                side=outcome.side,
                entry_price=_safe(outcome.entry_price),
                exit_price=_safe(outcome.exit_price),
                position_size=_real_size,
                leverage=_meta.get("leverage", 1.0),
                decision_context=_decision_context,
                signal_context=outcome.fingerprint_at_entry,
                ai_reasoning=_close_reason,
                pnl=_safe(outcome.pnl),
                pnl_pct=_safe(outcome.pnl_pct),
                holding_period=outcome.duration_seconds,
                decision_quality_score=_dq_score,
                status="closed",
                opened_at=_opened_at_dt,
                closed_at=_closed_at_dt.replace(tzinfo=None),
            )
            db.add(trade)
            db.flush()
            logger.debug(
                f"[UnifiedLearning] StrategyTrade persisted: "
                f"{strategy_id}/{outcome.symbol} pnl={outcome.pnl:.4f}"
            )
        except Exception as e:
            err_text = str(e)
            if "ForeignKeyViolation" in err_text or "strategy_trades_strategy_id_fkey" in err_text:
                logger.warning(
                    "[UnifiedLearning] StrategyTrade 外键跳过 strategy_id=%s: %s",
                    outcome.strategy_id,
                    err_text[:200],
                )
                try:
                    db.rollback()
                except Exception:
                    pass
                return
            logger.warning(f"[UnifiedLearning] StrategyTrade 写入失败: {e}")
            try:
                db.rollback()
            except Exception:
                pass
            raise

    # ══════════════════════════════════════════════════
    #  环境-绩效矩阵更新
    # ══════════════════════════════════════════════════

    def _update_regime_score(self, db: Session, outcome: TradeOutcome, weight: float):
        """用EMA方式更新绩效矩阵。

        修复 live 路径 template_id 空的问题：若 outcome.template_id 为空，
        尝试从 AIStrategy.parent_strategy_id 回溯到 StrategyTemplate.template_id。
        """
        from backend.database.models import StrategyRegimeScore, AIStrategy

        if not outcome.regime_at_entry:
            return

        template_id = outcome.template_id
        if not template_id and outcome.strategy_id:
            try:
                ais = (
                    db.query(AIStrategy)
                    .filter(AIStrategy.strategy_id == outcome.strategy_id)
                    .first()
                )
                if ais is not None and ais.parent_strategy_id:
                    # parent_strategy_id 可能直接是 tpl_xxx，也可能指向另一条 auto_xxx，
                    # 这里只要落到 tpl_* 前缀就当作模板 id（足够 regime_scores 聚合使用）
                    pid = str(ais.parent_strategy_id)
                    if pid.startswith("tpl_"):
                        template_id = pid
                        outcome.template_id = pid  # 反填，给后续调用者复用
                # 如果仍无 template_id，用 strategy_id 的 trade_nature 作为分组键
                if not template_id and ais is not None:
                    _genome = ais.genome or {}
                    _nature = _genome.get("trade_nature", "") or getattr(ais, "timeframe_tier", "") or "swing"
                    _sym = ais.primary_symbol or "?"
                    template_id = f"auto_{_nature}_{_sym}"
                    outcome.template_id = template_id
            except Exception as _resolve_err:
                logger.debug(
                    f"[UnifiedLearning] template_id 回溯失败(放行): {_resolve_err}"
                )

        if not template_id:
            return

        existing = db.query(StrategyRegimeScore).filter(
            StrategyRegimeScore.template_id == template_id,
            StrategyRegimeScore.regime == outcome.regime_at_entry,
            StrategyRegimeScore.source == outcome.source,
        ).first()

        is_win = 1.0 if outcome.pnl > 0 else 0.0
        pnl_pct = _safe(outcome.pnl_pct)

        if existing:
            n = (existing.sample_count or 0) + 1
            existing.sample_count = n

            if n <= 20:
                # 前20笔用增量平均，避免EMA初始化偏差
                existing.win_rate = _safe(existing.win_rate) + (is_win - _safe(existing.win_rate)) / n
                existing.avg_pnl_pct = _safe(existing.avg_pnl_pct) + (pnl_pct - _safe(existing.avg_pnl_pct)) / n
            else:
                alpha = weight * EMA_ALPHA_BASE
                existing.win_rate = _safe(existing.win_rate) * (1 - alpha) + is_win * alpha
                existing.avg_pnl_pct = _safe(existing.avg_pnl_pct) * (1 - alpha) + pnl_pct * alpha

            # 更新 Sharpe（增量估算：mean/std of pnl_pct）
            old_sharpe = _safe(existing.sharpe)
            if n <= 20:
                existing.sharpe = (existing.avg_pnl_pct / max(abs(existing.avg_pnl_pct) * 2, 0.001)) if existing.avg_pnl_pct != 0 else 0
            else:
                existing.sharpe = old_sharpe * 0.95 + (pnl_pct / max(abs(pnl_pct), 0.001)) * 0.05

            # 更新回撤（取最大值）
            if pnl_pct < 0 and abs(pnl_pct) > _safe(existing.max_drawdown):
                existing.max_drawdown = abs(pnl_pct)

            existing.composite_score = calc_composite_score(
                existing.win_rate, existing.avg_pnl_pct,
                _safe(existing.sharpe), _safe(existing.max_drawdown),
                existing.sample_count,
            )
            existing.last_updated = datetime.now(timezone.utc)
        else:
            new_score = StrategyRegimeScore(
                template_id=template_id,
                regime=outcome.regime_at_entry,
                source=outcome.source,
                sample_count=1,
                win_rate=is_win,
                avg_pnl_pct=pnl_pct,
                sharpe=0.0,
                max_drawdown=abs(pnl_pct) if pnl_pct < 0 else 0.0,
                profit_factor=1.0,
                composite_score=0.0,
                decay_factor=1.0,
            )
            new_score.composite_score = calc_composite_score(
                new_score.win_rate, new_score.avg_pnl_pct,
                0.0, new_score.max_drawdown, 1,
            )
            db.add(new_score)

        db.flush()

    # ══════════════════════════════════════════════════
    #  策略记忆更新
    # ══════════════════════════════════════════════════

    def _update_strategy_memory(self, db: Session, outcome: TradeOutcome):
        """更新策略记忆表"""
        from backend.database.models import StrategyMemory

        if not outcome.strategy_id:
            return

        resolved_sid = self._resolve_strategy_id_for_fk(db, outcome.strategy_id)
        if not resolved_sid:
            logger.debug(
                "[UnifiedLearning] skip strategy_memory unknown strategy_id=%s",
                outcome.strategy_id,
            )
            return

        weight = float((outcome.metadata or {}).get("learning_weight") or 1.0)
        weight = max(0.1, min(1.0, weight))
        effective_pnl = float(outcome.pnl or 0) * weight

        mem = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == resolved_sid
        ).first()

        if not mem:
            mem = StrategyMemory(
                strategy_id=resolved_sid,
                total_trades=0,
                win_rate=0.0,
                avg_profit=0.0,
                avg_loss=0.0,
                sharpe_ratio=0.0,
                max_drawdown=0.0,
            )
            db.add(mem)
            db.flush()

        n = (mem.total_trades or 0) + 1
        is_win = effective_pnl > 0
        old_wr = _safe(mem.win_rate)
        new_val = 1.0 if is_win else 0.0
        mem.win_rate = old_wr + (new_val - old_wr) / n
        mem.total_trades = n

        if is_win:
            old_avg = _safe(mem.avg_profit)
            win_count = max(round(mem.win_rate * n), 1)
            mem.avg_profit = old_avg + (effective_pnl - old_avg) / win_count
        else:
            old_avg = _safe(mem.avg_loss)
            loss_count = max(n - round(mem.win_rate * n), 1)
            mem.avg_loss = old_avg + (effective_pnl - old_avg) / loss_count

        pnl_pct = _safe(outcome.pnl_pct)
        if pnl_pct < 0 and abs(pnl_pct) > _safe(mem.max_drawdown):
            mem.max_drawdown = abs(pnl_pct)

        # 增量更新 sharpe_ratio（EMA 方式）
        old_sharpe = _safe(mem.sharpe_ratio)
        if n <= 10:
            avg_pnl = _safe(mem.avg_profit) * _safe(mem.win_rate) + _safe(mem.avg_loss) * (1 - _safe(mem.win_rate))
            mem.sharpe_ratio = (avg_pnl / max(abs(avg_pnl) * 2, 0.001)) if avg_pnl != 0 else 0
        else:
            instant_sharpe = pnl_pct / max(abs(pnl_pct), 0.001) if pnl_pct != 0 else 0
            mem.sharpe_ratio = old_sharpe * 0.95 + instant_sharpe * 0.05

        # 更新 performance_by_regime
        regime_perf = mem.performance_by_regime or {}
        regime = outcome.regime_at_entry or "unknown"
        if regime not in regime_perf:
            regime_perf[regime] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        regime_perf[regime]["trades"] += 1
        if is_win:
            regime_perf[regime]["wins"] += 1
        regime_perf[regime]["total_pnl"] = _safe(regime_perf[regime].get("total_pnl", 0)) + effective_pnl
        mem.performance_by_regime = regime_perf

        # 按 trade_nature 分层统计（存入 performance_by_regime 子节点）
        nature = outcome.trade_nature or TIER_TO_NATURE.get(outcome.tier or "mid", "swing")
        nature_key = f"nature_{nature}"
        if nature_key not in regime_perf:
            regime_perf[nature_key] = {
                "trades": 0, "wins": 0, "total_pnl": 0.0,
                "avg_pnl_pct": 0.0, "avg_duration_s": 0,
            }
        nature_stats = regime_perf[nature_key]
        nature_stats["trades"] += 1
        if is_win:
            nature_stats["wins"] += 1
        nature_stats["total_pnl"] = _safe(nature_stats.get("total_pnl", 0)) + effective_pnl
        t_n = nature_stats["trades"]
        old_avg_pnl = _safe(nature_stats.get("avg_pnl_pct", 0))
        nature_stats["avg_pnl_pct"] = old_avg_pnl + (pnl_pct - old_avg_pnl) / t_n
        old_avg_dur = nature_stats.get("avg_duration_s", 0)
        nature_stats["avg_duration_s"] = old_avg_dur + (outcome.duration_seconds - old_avg_dur) / t_n

        # ADX / TrendState 趋势环境学习统计
        adx_val = _safe(outcome.metadata.get("adx_at_entry", 0)) if outcome.metadata else 0
        if adx_val > 0:
            adx_bucket = "adx_strong" if adx_val >= 40 else ("adx_moderate" if adx_val >= 25 else ("adx_weak" if adx_val >= 15 else "adx_none"))
            if adx_bucket not in regime_perf:
                regime_perf[adx_bucket] = {"trades": 0, "wins": 0, "total_pnl": 0.0, "avg_pnl_pct": 0.0}
            ab = regime_perf[adx_bucket]
            ab["trades"] += 1
            if is_win:
                ab["wins"] += 1
            ab["total_pnl"] = _safe(ab.get("total_pnl", 0)) + effective_pnl
            t_ab = ab["trades"]
            ab["avg_pnl_pct"] = _safe(ab.get("avg_pnl_pct", 0)) + (pnl_pct - _safe(ab.get("avg_pnl_pct", 0))) / t_ab

        mem.performance_by_regime = regime_perf

        # F1-1: 在线交易写入成功/失败模式到 StrategyMemory
        pattern_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": outcome.symbol,
            "regime": outcome.regime_at_entry or "unknown",
            "pnl": effective_pnl,
            "pnl_pct": pnl_pct,
            "side": outcome.side,
            "source": outcome.source,
            "trade_nature": outcome.trade_nature or "",
            "r_multiple": outcome.metadata.get("r_multiple") if outcome.metadata else None,
        }
        if is_win:
            patterns = mem.successful_patterns or []
            patterns.append(pattern_entry)
            mem.successful_patterns = patterns[-50:]  # 保留最近50条
        elif effective_pnl < 0:
            patterns = mem.failed_patterns or []
            pattern_entry["reason"] = (outcome.metadata or {}).get("exit_reason", "")
            patterns.append(pattern_entry)
            mem.failed_patterns = patterns[-50:]

        mem.updated_at = datetime.now(timezone.utc)

        # ── P2-2: 自动生成关键教训（每10笔或亏损时触发） ──
        if (n % 10 == 0) or (effective_pnl < 0 and n >= 5):
            _lessons = self._generate_key_lessons(mem, outcome)
            if _lessons:
                # 统一去重键合并（此前 extend 无去重会产生重复教训）
                from backend.services.lesson_utils import merge_lessons
                mem.key_lessons = merge_lessons(mem.key_lessons, _lessons, cap=20)

        db.flush()

    @staticmethod
    def _generate_key_lessons(mem, outcome: TradeOutcome) -> list:
        """P2-2: 从策略记忆中自动提炼关键教训

        基于最近的盈亏模式、regime表现、ADX环境生成结构化教训。
        每条教训包含: type, symbol, tier, regime, lesson, severity
        """
        lessons = []
        ts = datetime.now(timezone.utc).isoformat()
        n = mem.total_trades or 0
        wr = _safe(mem.win_rate)

        # ── 1. 本次亏损诊断 ──
        if outcome.pnl < 0:
            regime = outcome.regime_at_entry or "unknown"
            nature = outcome.trade_nature or "swing"
            pnl_pct = _safe(outcome.pnl_pct)
            meta = outcome.metadata if isinstance(outcome.metadata, dict) else {}
            close_reason = meta.get("close_reason", "")

            severity = "high" if pnl_pct < -0.02 else "medium"
            reason_map = {
                "stop_loss": f"{outcome.symbol}止损触发({pnl_pct:+.2%})，检查入场点位和SL设置",
                "force_close": f"{outcome.symbol}强平({pnl_pct:+.2%})，杠杆过高或方向错误",
                "ai_cut_loss": f"{outcome.symbol}AI主动止损({pnl_pct:+.2%})，方向判断可能错误",
            }
            _default_msg = f"{outcome.symbol}[{nature}]亏损{pnl_pct:+.2%}({regime}市场)，需复盘入场逻辑"
            _lesson_text = ""
            for kw, msg in reason_map.items():
                if kw in (close_reason or "").lower():
                    _lesson_text = msg
                    break
            if not _lesson_text:
                _lesson_text = _default_msg

            lessons.append({
                "type": "loss_analysis",
                "symbol": outcome.symbol,
                "tier": outcome.tier or nature,
                "regime": regime,
                "lesson": _lesson_text,
                "severity": severity,
                "pnl_pct": pnl_pct,
                "ts": ts,
            })
            if meta.get("thesis_id"):
                lessons.append({
                    "type": "mlto_thesis",
                    "symbol": outcome.symbol,
                    "tier": outcome.tier or nature,
                    "lesson": (
                        f"[MLTO] {outcome.symbol} thesis 平仓 PnL={pnl_pct:+.2%} "
                        f"reason={close_reason or 'unknown'} "
                        f"hub_entry={meta.get('hub_adjusted_at_entry', 'n/a')}"
                    )[:220],
                    "severity": severity,
                    "thesis_id": meta.get("thesis_id"),
                    "ts": ts,
                })

        # ── 2. 胜率预警（每10笔检查） ──
        if n >= 10 and wr < 0.35:
            lessons.append({
                "type": "win_rate_warning",
                "symbol": outcome.symbol if n < 20 else "*",
                "tier": "all",
                "regime": "*",
                "lesson": f"近{n}笔胜率仅{wr:.0%}(<35%)，策略可能失效——考虑暂停或调整参数",
                "severity": "critical",
                "ts": ts,
            })

        # ── 2b. B方案：退出质量教训（峰值利润保留率） ──
        meta = outcome.metadata if isinstance(outcome.metadata, dict) else {}
        retention = outcome.retention_ratio
        if retention is None:
            retention = meta.get("retention_ratio")
        try:
            retention_f = float(retention) if retention is not None else None
        except Exception:
            retention_f = None
        if retention_f is not None and retention_f < 0.50 and (outcome.peak_pnl_pct or meta.get("peak_pnl_pct")):
            peak_pct = float(outcome.peak_pnl_pct or meta.get("peak_pnl_pct") or 0.0)
            exit_pct = float(outcome.exit_pnl_pct or meta.get("exit_pnl_pct") or outcome.pnl_pct or 0.0)
            lessons.append({
                "type": "exit_quality",
                "symbol": outcome.symbol,
                "tier": outcome.tier or outcome.trade_nature or "unknown",
                "regime": outcome.regime_at_exit or outcome.regime_at_entry or "unknown",
                "lesson": (
                    f"{outcome.symbol}峰值利润保留率仅{retention_f:.0%} "
                    f"(peak={peak_pct:+.1%}, exit={exit_pct:+.1%})，"
                    "后续同类仓位应更早上移SL或收紧trailing"
                ),
                "severity": "high" if retention_f < 0.30 else "medium",
                "retention_ratio": retention_f,
                "peak_pnl_pct": peak_pct,
                "exit_pnl_pct": exit_pct,
                "exit_channel": outcome.exit_channel or meta.get("close_reason", ""),
                "ts": ts,
            })

        # ── 3. 连续亏损预警 ──
        failed = mem.failed_patterns or []
        if len(failed) >= 3:
            recent_fails = failed[-3:]
            if all(f.get("pnl", 0) < 0 for f in recent_fails):
                symbols = list(set(f.get("symbol", "?") for f in recent_fails))
                total_loss = sum(f.get("pnl", 0) for f in recent_fails)
                lessons.append({
                    "type": "consecutive_losses",
                    "symbol": ",".join(symbols),
                    "tier": "all",
                    "regime": recent_fails[-1].get("regime", "?"),
                    "lesson": f"连续{len(recent_fails)}笔亏损(合计{total_loss:+.2f})，当前市场环境可能不适合该策略",
                    "severity": "high",
                    "ts": ts,
                })

        # ── 4. Regime 特定表现 ──
        regime_perf = mem.performance_by_regime or {}
        for reg, stats in regime_perf.items():
            if reg.startswith("nature_") or reg.startswith("adx_"):
                continue
            t = stats.get("trades", 0)
            if t >= 5:
                w = stats.get("wins", 0)
                reg_wr = w / max(t, 1)
                if reg_wr < 0.30:
                    lessons.append({
                        "type": "regime_weakness",
                        "symbol": outcome.symbol,
                        "tier": "all",
                        "regime": reg,
                        "lesson": f"在{reg}市场表现极差(胜率{reg_wr:.0%}，{t}笔)，建议避开此环境",
                        "severity": "high",
                        "ts": ts,
                    })
                elif reg_wr > 0.65:
                    lessons.append({
                        "type": "regime_strength",
                        "symbol": outcome.symbol,
                        "tier": "all",
                        "regime": reg,
                        "lesson": f"在{reg}市场表现出色(胜率{reg_wr:.0%}，{t}笔)，可加大仓位",
                        "severity": "info",
                        "ts": ts,
                    })

        return lessons

    # ══════════════════════════════════════════════════
    #  亏损追踪 & 自适应
    # ══════════════════════════════════════════════════

    def _track_loss_streak(self, outcome: TradeOutcome):
        """追踪连续亏损"""
        key = outcome.strategy_id or outcome.template_id
        if not key:
            return
        if outcome.pnl <= 0:
            self._loss_streaks[key] = self._loss_streaks.get(key, 0) + 1
        else:
            self._loss_streaks[key] = 0

    def _permanently_disable_strategy(self, db, outcome, streak: int):
        """永久禁用极端亏损策略（≥50次连亏）。"""
        if not outcome.strategy_id:
            return
        from backend.database.models import AIStrategy
        strategy = db.query(AIStrategy).filter(
            AIStrategy.strategy_id == outcome.strategy_id
        ).first()
        if not strategy:
            return
        strategy.is_active = "false"
        genome = getattr(strategy, "genome", None) or {}
        genome["permanently_disabled"] = True
        genome["disable_reason"] = f"连续亏损{streak}次永久禁用"
        strategy.genome = dict(genome)
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(strategy, "genome")
        db.flush()
        self._loss_streaks.pop(outcome.strategy_id, None)

    def _check_adaptation_needed(self, db: Session, outcome: TradeOutcome):
        """连续亏损时触发减仓/暂停（绝不收紧止损，避免死亡螺旋）

        Graduated response:
        - 7 losses: mild reduction (70% position, 85% leverage)
        - 10 losses: moderate reduction (50% position, 70% leverage)
        - 13 losses: severe reduction (30% position, 55% leverage)
        - 15+ losses: pause strategy
        - 50+ losses: permanent disable (策略本身无价值，不再恢复)
        """
        key = outcome.strategy_id or outcome.template_id
        streak = self._loss_streaks.get(key, 0)
        if streak < ADAPT_LOSS_STREAK:
            return

        # 极端连亏（≥50次）：永久禁用，策略本身无价值
        if streak >= 50:
            logger.error(
                f"[UnifiedLearning] {key} 连续亏损 {streak} 次，永久禁用（策略失效）"
            )
            self._permanently_disable_strategy(db, outcome, streak)
            return

        logger.warning(
            f"[UnifiedLearning] {key} 连续亏损 {streak} 次，触发保护性调整"
        )

        if not outcome.strategy_id:
            return

        from backend.database.models import AIStrategy
        strategy = db.query(AIStrategy).filter(
            AIStrategy.strategy_id == outcome.strategy_id
        ).first()
        if not strategy:
            return

        genome = getattr(strategy, "genome", None) or {}
        if not genome:
            return

        old_position = genome.get("max_position_size", 0.20)
        old_cooldown = genome.get("min_trade_interval", 120)
        old_leverage = genome.get("default_leverage", 8.0)

        if streak >= 15:
            # 连亏15次：暂停策略，标记需要重新进化
            strategy.status = "paused"
            # 2026-06-19: 统一注册到 SymbolLockRegistry
            try:
                from backend.services.symbol_lock_registry import lock_registry
                lock_registry.lock(
                    strategy.primary_symbol or "", strategy_id=str(strategy.strategy_id),
                    reason_code="consec_loss", by="unified_learning",
                )
            except Exception:
                pass
            logger.warning(f"[UnifiedLearning] {outcome.strategy_id} 连亏{streak}次，暂停策略")
            self._loss_streaks[key] = 0
            db.flush()
            return

        if streak >= 13:
            # 连亏13次：仓位降到30%，杠杆降到55%（下限5x），大幅增加冷却期
            genome["max_position_size"] = max(0.03, old_position * 0.30)
            genome["default_leverage"] = max(5.0, old_leverage * 0.55)
            genome["min_trade_interval"] = min(7200, old_cooldown * 3)
        elif streak >= 10:
            # 连亏10次：仓位降到50%，杠杆降到70%（下限5x），增加冷却期
            genome["max_position_size"] = max(0.03, old_position * 0.50)
            genome["default_leverage"] = max(5.0, old_leverage * 0.70)
            genome["min_trade_interval"] = min(3600, old_cooldown * 2)
        elif streak >= ADAPT_LOSS_STREAK:
            # 连亏7次：温和减仓到70%，杠杆降到85%（下限5x），适度增加冷却期
            genome["max_position_size"] = max(0.03, old_position * 0.70)
            genome["default_leverage"] = max(5.0, old_leverage * 0.85)
            genome["min_trade_interval"] = min(1800, int(old_cooldown * 1.5))

        # 绝不收紧止损 —— 止损保持不变或适度放宽
        # 绝不放大止盈 —— 止盈保持不变

        strategy.genome = dict(genome)
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(strategy, "genome")
        db.flush()

        logger.info(
            f"[UnifiedLearning] {outcome.strategy_id} 保护性调整(连亏{streak}): "
            f"仓位 {old_position:.2f}->{genome['max_position_size']:.2f}, "
            f"杠杆 {old_leverage:.1f}->{genome['default_leverage']:.1f}, "
            f"冷却 {old_cooldown}->{genome['min_trade_interval']}"
        )
        self._loss_streaks[key] = 0

    # ══════════════════════════════════════════════════
    #  偏离检测（过拟合预警）
    # ══════════════════════════════════════════════════

    def _check_divergence(self, db: Session, outcome: TradeOutcome):
        """检查实盘表现是否偏离回测预期"""
        if outcome.source != "live" or not outcome.template_id:
            return

        from backend.database.models import StrategyRegimeScore

        backtest = db.query(StrategyRegimeScore).filter(
            StrategyRegimeScore.template_id == outcome.template_id,
            StrategyRegimeScore.regime == outcome.regime_at_entry,
            StrategyRegimeScore.source == "backtest",
        ).first()

        live = db.query(StrategyRegimeScore).filter(
            StrategyRegimeScore.template_id == outcome.template_id,
            StrategyRegimeScore.regime == outcome.regime_at_entry,
            StrategyRegimeScore.source == "live",
        ).first()

        if not backtest or not live:
            return
        if (backtest.sample_count or 0) < MIN_SAMPLES_FOR_DIVERGENCE:
            return
        if (live.sample_count or 0) < MIN_SAMPLES_FOR_DIVERGENCE:
            return

        bt_score = _safe(backtest.composite_score)
        lv_score = _safe(live.composite_score)

        if bt_score > 0 and lv_score < bt_score * DIVERGENCE_THRESHOLD:
            logger.warning(
                f"[UnifiedLearning] 过拟合预警: {outcome.template_id} "
                f"regime={outcome.regime_at_entry} "
                f"backtest_score={bt_score:.3f} vs live_score={lv_score:.3f}"
            )
            self._flag_overfit(db, outcome.template_id, outcome.regime_at_entry)

    def _flag_overfit(self, db: Session, template_id: str, regime: str):
        """标记过拟合（降低回测数据的衰减因子）+ 触发紧急重进化"""
        from backend.database.models import StrategyRegimeScore

        backtest = db.query(StrategyRegimeScore).filter(
            StrategyRegimeScore.template_id == template_id,
            StrategyRegimeScore.regime == regime,
            StrategyRegimeScore.source == "backtest",
        ).first()
        if backtest:
            backtest.decay_factor = max(0.1, _safe(backtest.decay_factor, 1.0) * 0.7)
            db.flush()
            logger.info(
                f"[UnifiedLearning] 标记过拟合: {template_id}/{regime} "
                f"回测衰减因子={backtest.decay_factor:.2f}"
            )

        # 触发紧急重进化
        try:
            from backend.services.evolution_scheduler import evolution_scheduler
            evolution_scheduler.trigger_emergency_evolution(
                template_id=template_id,
                reason=f"过拟合预警: regime={regime}, 回测与实盘严重偏离",
            )
            logger.info(f"[UnifiedLearning] 已触发 {template_id} 紧急重进化")
        except Exception as evo_err:
            logger.warning(f"[UnifiedLearning] 触发紧急重进化失败: {evo_err}")

    # ══════════════════════════════════════════════════
    #  交易智慧效果评估
    # ══════════════════════════════════════════════════

    def _evaluate_wisdom_effectiveness(self, db: Session, outcome: TradeOutcome):
        """交易完成后评估注入智慧的有效性

        [v6 4.2] 双通道：
        1. 旧链：AIDecisionLog.wisdom_applied（ai_decision_service 决策）
        2. 生产链：mlto_thesis.wisdom_ids_json（qual_layer 注入）——平仓时
           从 thesis 读回注入的智慧 id，按 PnL 评估（含噪音过滤）。
        """
        if outcome.source != "live":
            return

        close_reason = ""
        if isinstance(outcome.metadata, dict):
            close_reason = outcome.metadata.get("close_reason") or outcome.exit_channel or ""

        decision_log_id = outcome.metadata.get("decision_log_id") if isinstance(outcome.metadata, dict) else None

        # 生产链：thesis 注入的智慧验证（mlto_thesis 在 analytics 库）
        thesis_id = outcome.metadata.get("thesis_id") if isinstance(outcome.metadata, dict) else None
        if thesis_id:
            try:
                from backend.database.connection import AnalyticsSessionLocal
                from backend.services.wisdom_tracker import wisdom_tracker
                _adb = AnalyticsSessionLocal()
                try:
                    from backend.services.mlto.db_models import MltoThesis
                    row = _adb.query(MltoThesis).filter(
                        MltoThesis.thesis_id == thesis_id
                    ).first()
                    if row:
                        from backend.services.mlto.thesis_store import _parse_wisdom_ids
                        wids = _parse_wisdom_ids(getattr(row, "wisdom_ids_json", None))
                        if wids:
                            _res = wisdom_tracker.evaluate_wisdom_result(
                                db, wids, outcome.pnl, outcome.pnl_pct,
                                close_reason=close_reason,
                            )
                            if _res:
                                logger.info(
                                    "[UnifiedLearning] thesis %s 智慧验证: "
                                    "wids=%s pnl=%.2f pct=%.4f signal=%s skipped=%s",
                                    thesis_id, wids, outcome.pnl, outcome.pnl_pct,
                                    _res.get("signal"), _res.get("skipped"),
                                )
                finally:
                    _adb.close()
            except Exception as e:
                logger.debug(f"[UnifiedLearning] thesis 智慧评估跳过: {e}")
            return  # thesis 链已覆盖验证，避免旧链重复

        if not decision_log_id:
            return

        try:
            from backend.services.wisdom_tracker import wisdom_tracker
            wisdom_tracker.evaluate_trade_result(
                db, decision_log_id, outcome.pnl, outcome.pnl_pct,
            )
        except Exception as e:
            logger.debug(f"[UnifiedLearning] 智慧效果评估跳过: {e}")

    # ══════════════════════════════════════════════════
    #  提示词进化触发
    # ══════════════════════════════════════════════════

    def _check_prompt_evolution_trigger(self, db: Session, outcome: TradeOutcome):
        """每积累 N 笔实盘交易自动触发一次提示词评估"""
        if outcome.source not in ("live", "paper"):
            return

        key = outcome.strategy_id or outcome.template_id
        if not key:
            return

        self._trade_counters[key] = self._trade_counters.get(key, 0) + 1
        streak = self._loss_streaks.get(key, 0)
        trigger_interval = self._prompt_evo_interval
        if outcome.source == "paper":
            # paper 也参与复盘，但门槛高于 live，避免模拟样本过快改写策略判断。
            trigger_interval = max(self._prompt_evo_interval, 30)

        should_trigger = (
            self._trade_counters[key] >= trigger_interval
            or streak >= ADAPT_LOSS_STREAK
        )
        if not should_trigger:
            return

        self._trade_counters[key] = 0
        reason = "定期" if streak < ADAPT_LOSS_STREAK else f"连亏{streak}次紧急"

        try:
            from backend.config.settings import PROMPT_EVOLUTION_ENABLED
            if not PROMPT_EVOLUTION_ENABLED:
                logger.debug(
                    "[UnifiedLearning] PROMPT_EVOLUTION_ENABLED=false，仅 Hermes L2 可改 task prompt"
                )
                return
            from backend.services.strategy_learning_service import strategy_learning
            if outcome.strategy_id:
                strategy_learning.run_periodic_review(outcome.strategy_id, days=14)
                logger.info(f"[UnifiedLearning] {reason}触发策略复盘(含Prompt): {outcome.strategy_id}")
        except Exception as e:
            logger.warning(f"[UnifiedLearning] 提示词进化触发失败: {e}")

    # ══════════════════════════════════════════════════
    #  数据衰减（定期调用）
    # ══════════════════════════════════════════════════

    def decay_old_scores(self, db: Session, decay_rate: float = 0.98):
        """对所有绩效矩阵记录施加时间衰减"""
        from backend.database.models import StrategyRegimeScore

        try:
            scores = db.query(StrategyRegimeScore).all()
            for s in scores:
                s.decay_factor = max(0.01, _safe(s.decay_factor, 1.0) * decay_rate)
            db.commit()
            logger.info(f"[UnifiedLearning] 数据衰减完成: {len(scores)} 条记录")
        except Exception as e:
            logger.error(f"[UnifiedLearning] 数据衰减失败: {e}")
            db.rollback()

    # ══════════════════════════════════════════════════
    #  查询接口
    # ══════════════════════════════════════════════════

    def get_regime_score(
        self, db: Session, template_id: str, regime: str, source: str
    ) -> Optional[Any]:
        """查询特定模板在特定环境下的绩效"""
        from backend.database.models import StrategyRegimeScore
        return db.query(StrategyRegimeScore).filter(
            StrategyRegimeScore.template_id == template_id,
            StrategyRegimeScore.regime == regime,
            StrategyRegimeScore.source == source,
        ).first()

    def get_best_templates_for_regime(
        self, db: Session, regime: str, top_n: int = 5
    ) -> List[Dict]:
        """查询当前环境下得分最高的模板（三源加权）"""
        from backend.database.models import StrategyRegimeScore

        scores = db.query(StrategyRegimeScore).filter(
            StrategyRegimeScore.regime == regime,
            StrategyRegimeScore.sample_count >= 5,
        ).all()

        template_agg: Dict[str, float] = {}
        for s in scores:
            w = SOURCE_WEIGHTS.get(s.source, 0.3)
            decay = _safe(s.decay_factor, 1.0)
            composite = _safe(s.composite_score)
            template_agg[s.template_id] = template_agg.get(s.template_id, 0) + composite * w * decay

        ranked = sorted(template_agg.items(), key=lambda x: -x[1])[:top_n]
        return [{"template_id": tid, "weighted_score": score} for tid, score in ranked]

    # ══════════════════════════════════════════════════
    #  AI学习系统整合扩展: DRL/Kelly追踪 + genome安全修改
    # ══════════════════════════════════════════════════

    def update_drl_performance(self, db: Session, prediction: dict):
        """
        更新DRL表现追踪

        Args:
            prediction: {
                'symbol': str,
                'predicted_direction': float,
                'actual_direction': float,
                'predicted_size': float,
                'actual_pnl': float,
                'regime': str,
                'model_version': str,
            }
        """
        try:
            from backend.database.models import DRLPerformance

            predicted_dir = prediction.get('predicted_direction', 0)
            actual_dir = prediction.get('actual_direction', 0)
            is_correct = (predicted_dir * actual_dir > 0) if actual_dir != 0 else False

            perf = DRLPerformance(
                timestamp=datetime.now(timezone.utc),
                symbol=prediction.get('symbol', ''),
                predicted_direction=predicted_dir,
                actual_direction=actual_dir,
                predicted_size=prediction.get('predicted_size', 0),
                actual_pnl=prediction.get('actual_pnl', 0),
                regime=prediction.get('regime', ''),
                is_correct=is_correct,
                model_version=prediction.get('model_version', ''),
            )
            db.add(perf)
            db.flush()
        except Exception as e:
            logger.warning(f"[UnifiedLearning] DRL表现追踪失败: {e}")

    def calculate_multi_symbol_risk(self, db: Session) -> Dict:
        """计算多币种风险相关性（委托给PortfolioRiskAggregator）"""
        try:
            from backend.services.rl.portfolio_risk_aggregator import portfolio_risk_aggregator
            # 获取活跃交易对
            from backend.database.models import Position
            symbols = [
                p.symbol for p in db.query(Position).filter(
                    Position.status == "open"
                ).distinct(Position.symbol).all()
            ]
            if len(symbols) < 2:
                return {'correlation_risk': 0.0, 'symbols': symbols}
            risk = portfolio_risk_aggregator.check_correlation_risk(symbols)
            return {'correlation_risk': risk, 'symbols': symbols}
        except Exception as e:
            logger.warning(f"[UnifiedLearning] 多币种风险计算失败: {e}")
            return {'correlation_risk': 0.0, 'symbols': []}

    def trigger_coordinated_optimization(self, db: Session, reason: str):
        """
        触发协调优化（同时优化所有子系统）

        使用 StateConsistencyManager 加锁，防止并发修改。
        """
        from backend.services.rl.time_window_coordinator import state_consistency_manager

        tx_id = state_consistency_manager.begin_transaction(
            systems=['evolution', 'drl', 'kelly'],
            timeout=60.0,
        )
        try:
            logger.info(f"[UnifiedLearning] 协调优化开始: reason={reason}, tx={tx_id}")

            # 1. 标记进化需要触发
            try:
                from backend.services.evolution_scheduler import evolution_scheduler
                evolution_scheduler.trigger_emergency_evolution(
                    template_id=None,
                    reason=f"协调优化: {reason}",
                )
            except Exception as e:
                logger.warning(f"[UnifiedLearning] 协调优化-进化触发失败: {e}")

            # 2. 标记DRL需要重训练（通过SystemCoordinatorState）
            try:
                from backend.database.models import SystemCoordinatorState
                state = db.query(SystemCoordinatorState).first()
                if state:
                    state.last_drl_training_at = None  # 标记需要重训练
                    db.flush()
            except Exception as e:
                logger.warning(f"[UnifiedLearning] 协调优化-DRL标记失败: {e}")

            committed = state_consistency_manager.commit_if_valid(tx_id)
            if not committed:
                logger.warning(f"[UnifiedLearning] 协调优化提交失败（版本冲突），已回滚")
        except Exception as e:
            state_consistency_manager.rollback(tx_id)
            logger.error(f"[UnifiedLearning] 协调优化异常: {e}")

    # ══════════════════════════════════════════════════
    #  genome安全修改（解决并发竞态缺陷#6）
    # ══════════════════════════════════════════════════

    _genome_locks: Dict[str, threading.Lock] = {}

    def _safe_modify_genome(self, db: Session, strategy_id: str, modifier_fn):
        """
        安全修改genome — 防止UnifiedLearningService和StrategyEvolver并发写入

        Args:
            db: 数据库会话
            strategy_id: 策略ID
            modifier_fn: 修改函数 modifier_fn(genome: dict) -> None（就地修改）
        """
        from backend.database.models import AIStrategy

        if strategy_id not in self._genome_locks:
            self._genome_locks[strategy_id] = threading.Lock()

        with self._genome_locks[strategy_id]:
            try:
                # v3 整改：在 PostgreSQL 上附加 SELECT ... FOR UPDATE 行级锁，
                # SQLite 自动忽略，行为完全兼容。
                q = db.query(AIStrategy).filter(
                    AIStrategy.strategy_id == strategy_id
                )
                is_pg = False
                try:
                    dialect_name = db.get_bind().dialect.name if db.get_bind() else ""
                    is_pg = str(dialect_name).lower() == "postgresql"
                except Exception:
                    is_pg = False

                if is_pg:
                    try:
                        q = q.with_for_update(of=AIStrategy, nowait=False)
                    except Exception:
                        # 某些 SQLAlchemy 版本 `of` 参数不兼容，降级为纯 with_for_update()
                        q = db.query(AIStrategy).filter(
                            AIStrategy.strategy_id == strategy_id
                        ).with_for_update()

                strategy = q.first()

                if not strategy or not strategy.genome:
                    return

                genome = dict(strategy.genome)
                modifier_fn(genome)  # 应用修改

                strategy.genome = genome
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(strategy, "genome")
                db.flush()
            except Exception as e:
                logger.warning(f"[UnifiedLearning] genome安全修改失败: {e}")

    # ══════════════════════════════════════════════════
    #  DRL表现日聚合归档
    # ══════════════════════════════════════════════════

    def archive_drl_performance(self, db: Session, days_to_keep: int = 30):
        """
        归档DRL表现数据：30天前明细聚合为日汇总

        定时调用（建议每天执行一次）。
        """
        try:
            from backend.database.models import DRLPerformance, DRLPerformanceDaily
            from sqlalchemy import case, func

            cutoff = datetime.now(timezone.utc) - __import__('datetime').timedelta(days=days_to_keep)
            correctness_score = case(
                (DRLPerformance.is_correct.is_(True), 1.0),
                (DRLPerformance.is_correct.is_(False), 0.0),
                else_=None,
            )
            correct_count = case(
                (DRLPerformance.is_correct.is_(True), 1),
                else_=0,
            )

            # 按日聚合
            daily_agg = db.query(
                DRLPerformance.symbol,
                func.date(DRLPerformance.timestamp).label('date'),
                DRLPerformance.model_version,
                func.avg(correctness_score).label('avg_accuracy'),
                func.avg(DRLPerformance.actual_pnl).label('avg_pnl'),
                func.count(DRLPerformance.id).label('trade_count'),
                func.sum(correct_count).label('correct_count'),
            ).filter(
                DRLPerformance.timestamp < cutoff,
            ).group_by(
                DRLPerformance.symbol,
                func.date(DRLPerformance.timestamp),
                DRLPerformance.model_version,
            ).all()

            # 写入日聚合表
            for row in daily_agg:
                existing = db.query(DRLPerformanceDaily).filter(
                    DRLPerformanceDaily.symbol == row.symbol,
                    DRLPerformanceDaily.date == row.date,
                    DRLPerformanceDaily.model_version == row.model_version,
                ).first()

                if existing:
                    existing.avg_accuracy = row.avg_accuracy
                    existing.avg_pnl = row.avg_pnl
                    existing.trade_count = row.trade_count
                    existing.correct_count = row.correct_count
                else:
                    db.add(DRLPerformanceDaily(
                        date=row.date,
                        symbol=row.symbol,
                        model_version=row.model_version,
                        avg_accuracy=row.avg_accuracy or 0.0,
                        avg_pnl=row.avg_pnl or 0.0,
                        trade_count=row.trade_count or 0,
                        correct_count=row.correct_count or 0,
                    ))

            # 删除已归档的明细
            db.query(DRLPerformance).filter(
                DRLPerformance.timestamp < cutoff,
            ).delete()

            db.commit()
            logger.info(f"[UnifiedLearning] DRL表现归档完成: {len(daily_agg)}条日聚合")
        except Exception as e:
            logger.error(f"[UnifiedLearning] DRL表现归档失败: {e}")
            db.rollback()


# 全局单例
unified_learning = UnifiedLearningService()
