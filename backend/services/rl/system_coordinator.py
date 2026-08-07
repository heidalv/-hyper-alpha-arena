"""
SystemCoordinator — 三系统统一协调器

协调进化系统、DRL强化学习、Kelly仓位管理三个子系统。
作为 full_auto_trading_service 的唯一集成点，降低耦合度。

设计原则:
- full_auto_trading_service 仅依赖 SystemCoordinator，不直接依赖DRL/Kelly/Evolution
- 所有整合通过 Feature Flag 控制，可随时关闭
- 参数仲裁优先级: 风控 > Kelly > DRL > 进化
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class DRLAdvice:
    """DRL建议"""
    action: str = "hold"       # hold / long / short
    direction: float = 0.0     # -1~1
    size: float = 0.0          # 0~1
    confidence: float = 0.0    # 0~1
    source: str = "disabled"   # disabled / shadow / drl


@dataclass
class CoordinationAction:
    """协调动作"""
    trigger_evolution: bool = False
    trigger_drl_retrain: bool = False
    trigger_kelly_update: bool = False
    reasons: List[str] = field(default_factory=list)


class SystemCoordinator:
    """
    三系统统一协调器（单例）

    协调机制:
    1. 进化触发: 连续亏损N次 或 Sharpe低于阈值
    2. DRL重训练: 决策准确率下降 或 新币种加入 或 参数漂移
    3. Kelly自适应: 胜率/盈亏比显著变化
    """

    _instance = None
    _lock = threading.Lock()

    TRIGGERS = {
        'evolution': {'loss_streak': 5, 'sharpe_threshold': 0.3, 'regime_change': True},
        'drl_retrain': {'accuracy_drop': 0.15, 'new_symbol': True, 'param_change': True},
        'kelly_adjust': {'win_rate_change': 0.1, 'avg_ratio_change': 0.3},
    }

    # 参数仲裁优先级: 风控 > Kelly > DRL > 进化
    PARAM_PRIORITY = ['risk_gate', 'kelly', 'drl', 'evolution']

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
        self._rl_optimizer = None
        self._kelly_sizer = None
        self._risk_aggregator = None
        self._rl_position_sizer = None
        self._drl_model_version = "v0"
        self._last_drl_training_params: Dict[str, Any] = {}
        logger.info("[SystemCoordinator] 三系统协调器初始化完成")

    def _get_rl_optimizer(self):
        """v4 P0-4：委托给 rl_singleton，与 rl_routes 共享同一 PPO 实例。"""
        if self._rl_optimizer is None:
            try:
                from backend.services.rl.rl_singleton import get_rl_optimizer as _singleton
                self._rl_optimizer = _singleton()
            except Exception as e:
                logger.warning(f"[Coordinator] Cannot load RLPolicyOptimizer: {e}")
        return self._rl_optimizer

    def _get_kelly_sizer(self):
        if self._kelly_sizer is None:
            try:
                from backend.services.rl import KellyPositionSizer
                self._kelly_sizer = KellyPositionSizer()
            except Exception as e:
                logger.warning(f"[Coordinator] Cannot load KellyPositionSizer: {e}")
        return self._kelly_sizer

    def _get_risk_aggregator(self):
        if self._risk_aggregator is None:
            try:
                from backend.services.rl import PortfolioRiskAggregator
                self._risk_aggregator = PortfolioRiskAggregator()
            except Exception as e:
                logger.warning(f"[Coordinator] Cannot load PortfolioRiskAggregator: {e}")
        return self._risk_aggregator

    def _get_rl_position_sizer(self):
        """S5: SARSA RL 仓位管理器（懒加载）"""
        if self._rl_position_sizer is None:
            try:
                from backend.services.rl_position_sizer import get_rl_position_sizer
                self._rl_position_sizer = get_rl_position_sizer()
            except Exception as e:
                logger.warning(f"[Coordinator] Cannot load RlPositionSizer: {e}")
        return self._rl_position_sizer

    # ══════════════════════════════════════════════════
    #  协调检查
    # ══════════════════════════════════════════════════

    def check_and_coordinate(self, db: Session) -> CoordinationAction:
        """检查所有触发条件并返回协调动作"""
        from backend.config.settings import ENABLE_COORDINATOR

        if not ENABLE_COORDINATOR:
            return CoordinationAction()

        action = CoordinationAction()
        try:
            # 检查进化触发条件
            if self._should_trigger_evolution(db):
                action.trigger_evolution = True
                action.reasons.append("进化触发条件满足")

            # DRL 已下线（2026-06-11）：不再触发重训。
            # 历史原因：1722 条影子预测 is_correct 从未回填，准确率统计失真；
            # 且无已训练模型。历史数据保留在 drl_performance 表。

            # 检查Kelly更新条件
            if self._should_update_kelly(db):
                action.trigger_kelly_update = True
                action.reasons.append("Kelly更新条件满足")

        except Exception as e:
            logger.error(f"[Coordinator] 协调检查失败: {e}")

        return action

    def log_action(
        self,
        db: Session,
        action: "CoordinationAction",
        triggered_jobs: Optional[List[str]] = None,
        skipped_reasons: Optional[List[str]] = None,
    ) -> None:
        """把每次 check_and_coordinate 的结果落到 coordinator_actions（P1-3）。

        LearningLoop._tick_coordinator 在分发完 job 之后调一次，方便复盘。
        P0-3: 增加DB锁重试机制，最多3次指数退避。
        """
        import time as _time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                from backend.database.models import CoordinatorAction as _CA
                row = _CA(
                    action={
                        "trigger_evolution": bool(action.trigger_evolution),
                        "trigger_drl_retrain": bool(action.trigger_drl_retrain),
                        "trigger_kelly_update": bool(action.trigger_kelly_update),
                        "reasons": list(action.reasons or []),
                    },
                    triggered_jobs=list(triggered_jobs or []),
                    skipped_reasons=list(skipped_reasons or []),
                )
                db.add(row)
                db.commit()
                logger.info(
                    "[Coordinator] action 已落库: evo=%s drl=%s kelly=%s jobs=%s",
                    bool(action.trigger_evolution),
                    bool(action.trigger_drl_retrain),
                    bool(action.trigger_kelly_update),
                    list(triggered_jobs or []),
                )
                return  # 成功，退出
            except Exception as e:
                err_str = str(e)
                is_db_lock = "database is locked" in err_str
                try:
                    db.rollback()
                except Exception:
                    pass
                if is_db_lock and attempt < max_retries - 1:
                    wait_sec = 1.0 * (2 ** attempt)  # 1s, 2s, 4s
                    logger.warning(
                        f"[Coordinator] DB锁, 重试 {attempt+2}/{max_retries} "
                        f"(等待{wait_sec:.0f}s): {e}"
                    )
                    _time.sleep(wait_sec)
                    continue
                # 最后一次失败或非锁错误 → 记录
                logger.error(
                    f"[Coordinator] coordinator_actions 写入失败: {e}", exc_info=True
                )
                return

    # 冷却窗口（P1-4）
    EVOLUTION_COOLDOWN_SECONDS: int = 24 * 3600   # 紧急进化 24h
    DRL_RETRAIN_COOLDOWN_SECONDS: int = 2 * 3600  # DRL 重训 2h

    # 连亏触发阈值（P2-5 验收）
    LOSS_STREAK_TRIGGER: int = 3

    def _should_trigger_evolution(self, db: Session) -> bool:
        """检查是否需要触发进化（叠加 24h 冷却，P1-4）。

        触发条件（满足任一且未处于冷却中）：
        - 存在样本数 ≥10 的活跃策略 Sharpe 低于 `sharpe_threshold`
        - 最近 `LOSS_STREAK_TRIGGER`（默认 3）笔 `StrategyTrade.status==closed` 全部亏损（pnl ≤0）
        """
        try:
            from backend.database.models import StrategyRegimeScore, SystemCoordinatorState, StrategyTrade

            triggered = False
            reason = ""

            low_sharpe = db.query(StrategyRegimeScore).filter(
                StrategyRegimeScore.source == "live",
                StrategyRegimeScore.sharpe < self.TRIGGERS['evolution']['sharpe_threshold'],
                StrategyRegimeScore.sample_count >= 10,
            ).first()
            if low_sharpe is not None:
                triggered = True
                reason = "low_sharpe"

            if not triggered:
                recent_trades = (
                    db.query(StrategyTrade)
                    .filter(StrategyTrade.status == "closed")
                    .order_by(StrategyTrade.closed_at.desc())
                    .limit(self.LOSS_STREAK_TRIGGER)
                    .all()
                )
                if len(recent_trades) >= self.LOSS_STREAK_TRIGGER and all(
                    (t.pnl is not None and float(t.pnl) <= 0.0) for t in recent_trades
                ):
                    triggered = True
                    reason = f"loss_streak>={self.LOSS_STREAK_TRIGGER}"

            if not triggered:
                return False

            state = db.query(SystemCoordinatorState).first()
            if state and state.last_evolution_at:
                last = state.last_evolution_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - last).total_seconds() < self.EVOLUTION_COOLDOWN_SECONDS:
                    return False
            logger.info(f"[Coordinator] _should_trigger_evolution=True reason={reason}")
            return True
        except Exception as e:
            logger.debug(f"[Coordinator] _should_trigger_evolution 异常: {e}")
            return False

    def _should_retrain_drl(self, db: Session) -> bool:
        """DRL 已下线（2026-06-11）：恒返回 False。

        历史问题：影子预测的 is_correct 从未回填，此处的准确率判断把
        NULL 当 False，准确率恒为 0，会误触发重训。下线后保留方法签名
        以兼容外部调用。
        """
        return False

    def _should_update_kelly(self, db: Session) -> bool:
        """检查是否需要更新Kelly统计（P0-2 修复）

        - ENABLE_KELLY_POSITION 关闭 → 不触发
        - SystemCoordinatorState.last_kelly_update_at 为空 → 首次必触发
        - 距离上次更新 >30min → 触发
        """
        try:
            from backend.config.settings import ENABLE_KELLY_POSITION
            if not ENABLE_KELLY_POSITION:
                return False
            from backend.database.models import SystemCoordinatorState
            state = db.query(SystemCoordinatorState).first()
            if state is None or state.last_kelly_update_at is None:
                return True
            last = state.last_kelly_update_at
            # 兼容 naive datetime：按 UTC 解释
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - last).total_seconds() > 1800
        except Exception as e:
            logger.debug(f"[Coordinator] _should_update_kelly 异常: {e}")
            return False

    # ══════════════════════════════════════════════════
    #  DRL建议
    # ══════════════════════════════════════════════════

    def get_drl_advice(self, symbol: str, context: Any) -> DRLAdvice:
        """
        获取DRL建议（供TradingDecisionInterface调用）。

        新语义（P1-fix：让 shadow 能独立于 integration flag 采集样本）：

        - 关闭全部 DRL：`ENABLE_DRL_INTEGRATION=false` AND `DRL_SHADOW_MODE=false`
        - Shadow 观测：`DRL_SHADOW_MODE=true`（无论 integration 开关如何）
            - 仍然构建 observation，调用模型或 momentum baseline，把预测落入
              `drl_performance`，**但返回的 DRLAdvice 是 disabled**，不影响决策
        - 实际接管：`ENABLE_DRL_INTEGRATION=true` AND `DRL_SHADOW_MODE=false`
        """
        from backend.config.settings import ENABLE_DRL_INTEGRATION, DRL_SHADOW_MODE

        if not ENABLE_DRL_INTEGRATION and not DRL_SHADOW_MODE:
            return DRLAdvice(source="disabled")

        try:
            obs = self._build_observation(symbol, context)
            if obs is None:
                return DRLAdvice(source="disabled")

            optimizer = self._get_rl_optimizer()
            has_model = bool(
                optimizer is not None
                and getattr(optimizer, "is_available", False)
                and getattr(optimizer, "model", None) is not None
            )

            if has_model:
                direction, size = optimizer.predict(obs)
                pred_source = "model"
            else:
                # —— Baseline fallback：冷启动阶段就先采样，否则 DRL 永远训不出 v1 ——
                direction, size = self._momentum_baseline(obs)
                pred_source = "baseline"

            if abs(direction) < 0.2 or size < 0.1:
                action = "hold"
            elif direction > 0:
                action = "long"
            else:
                action = "short"
            confidence = min(abs(direction) * size, 1.0)

            # Shadow 始终采样，无论 integration 是否开启
            if DRL_SHADOW_MODE or not ENABLE_DRL_INTEGRATION:
                self._log_shadow_advice(
                    symbol, float(direction), float(size), action, float(confidence),
                    pred_source=pred_source,
                )
                # Shadow / integration off 时返回 disabled，不影响决策
                if not ENABLE_DRL_INTEGRATION or DRL_SHADOW_MODE:
                    return DRLAdvice(
                        action=action, direction=float(direction), size=float(size),
                        confidence=float(confidence),
                        source=f"shadow_{pred_source}",
                    )

            return DRLAdvice(
                action=action, direction=float(direction), size=float(size),
                confidence=float(confidence), source="drl",
            )

        except Exception as e:
            logger.warning(f"[Coordinator] DRL建议获取失败: {e}")
            return DRLAdvice(source="disabled")

    @staticmethod
    def _momentum_baseline(obs) -> tuple:
        """冷启动基线：对 observation（近 10 个收益率）做简单 momentum 评分。
        返回 (direction∈[-1,1], size∈[0,1])，行为与真实 PPO 输出同形。
        """
        try:
            import numpy as np
            arr = np.asarray(obs, dtype=float)
            if arr.size == 0:
                return 0.0, 0.0
            mean = float(np.mean(arr))
            std = float(np.std(arr) + 1e-9)
            direction = float(np.clip(mean / std, -1.0, 1.0))
            # 波动率越高，建议仓位越小（反向相关）
            size = float(np.clip(0.4 - std * 5, 0.0, 0.5))
            return direction, size
        except Exception:
            return 0.0, 0.0

    def _build_observation(self, symbol: str, context: Any) -> Optional[Any]:
        """构建DRL观察值"""
        try:
            import numpy as np
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import CryptoKline
            import pandas as pd

            db = MarketSessionLocal()
            try:
                klines = db.query(CryptoKline).filter(
                    CryptoKline.symbol == symbol,
                    CryptoKline.period == "1h",
                ).order_by(CryptoKline.timestamp.desc()).limit(100).all()

                if len(klines) < 50:
                    return None

                # 简单观察值：最近价格变化率
                closes = [float(k.close_price) for k in reversed(klines)]
                returns = pd.Series(closes).pct_change().dropna().values
                if len(returns) < 10:
                    return None

                # 取最近10个收益率作为观察值
                obs = np.array(returns[-10:], dtype=np.float32)
                # Pad to fixed size if needed
                if len(obs) < 10:
                    obs = np.pad(obs, (0, 10 - len(obs)))
                return obs
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[Coordinator] 观察值构建失败: {e}")
            return None

    def _log_shadow_advice(self, symbol: str, direction: float, size: float,
                           action: str, confidence: float,
                           pred_source: str = "model"):
        """记录影子模式建议。pred_source ∈ {"model","baseline"}。"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import DRLPerformance

            db = SessionLocal()
            try:
                regime_tag = f"shadow_{pred_source}" if pred_source else "shadow"
                perf = DRLPerformance(
                    timestamp=datetime.now(timezone.utc),
                    symbol=symbol,
                    predicted_direction=float(direction),
                    predicted_size=float(size),
                    regime=regime_tag,
                    model_version=(
                        self._drl_model_version
                        if pred_source != "baseline"
                        else "baseline"
                    ),
                )
                db.add(perf)
                db.commit()
            finally:
                db.close()
        except Exception as e:
            # 原为 debug，多次调用失败会完全静默。升 warning 方便观察
            logger.warning(f"[Coordinator] 影子建议记录失败: {e}")

    # ══════════════════════════════════════════════════
    #  Kelly仓位
    # ══════════════════════════════════════════════════

    def get_kelly_position_limit(self, symbol: str, equity: float) -> float:
        """获取Kelly仓位上限"""
        from backend.config.settings import ENABLE_KELLY_POSITION

        if not ENABLE_KELLY_POSITION:
            from backend.config.settings import PORTFOLIO_MAX_SINGLE_POSITION
            return PORTFOLIO_MAX_SINGLE_POSITION

        sizer = self._get_kelly_sizer()
        if sizer is None:
            return 0.25

        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import StrategyTrade

            db = SessionLocal()
            try:
                from sqlalchemy import Text, cast, or_
                # 深挖第 3 轮 (2026-05-08)：排除历史 legacy_dirty 污染数据
                decision_context_text = cast(StrategyTrade.decision_context, Text)
                trades = db.query(StrategyTrade).filter(
                    StrategyTrade.symbol == symbol,
                    StrategyTrade.pnl.isnot(None),
                    or_(
                        StrategyTrade.decision_context.is_(None),
                        ~decision_context_text.like('%"legacy_dirty": true%'),
                    ),
                ).order_by(StrategyTrade.closed_at.desc()).limit(100).all()

                trade_history = [{"pnl": t.pnl} for t in trades] if trades else None
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
                raise
            finally:
                db.close()

            result = sizer.calculate(equity=equity, trade_history=trade_history)
            # Kelly无历史数据时返回0，此时使用默认上限
            if result.adjusted_fraction <= 0:
                from backend.config.settings import PORTFOLIO_MAX_SINGLE_POSITION
                return PORTFOLIO_MAX_SINGLE_POSITION
            return result.adjusted_fraction

        except Exception as e:
            logger.warning(f"[Coordinator] Kelly计算失败: {e}")
            return 0.25

    # ══════════════════════════════════════════════════
    #  组合风控
    # ══════════════════════════════════════════════════

    def check_portfolio_risk(self, context: Any) -> Any:
        """组合风控检查（P0-5 真实接入 aggregate）

        逻辑：
        1) `ENABLE_PORTFOLIO_RISK=False` → 透传 passed=True。
        2) 读最近 MultiSymbolKelly 行（由 LearningLoop 定期写入）得到
           summary 的 total_risk / correlation_risk / 目标 symbol 的 adjusted_fraction。
        3) 相关性 > `PORTFOLIO_MAX_CORRELATION_RISK`：
           - 硬阻塞模式(`PORTFOLIO_RISK_HARD_BLOCK=true`) → passed=False
           - 夹紧模式（默认）                               → passed=True + 降级 risk_level
        4) total_risk > PORTFOLIO_MAX_RISK 同理，用 adjusted_pct 向 TDI 返回夹紧值。
        """
        from backend.services.trading_decision_interface import RiskVerdict

        try:
            from backend.config import settings as _s
            if not getattr(_s, "ENABLE_PORTFOLIO_RISK", False):
                return RiskVerdict(passed=True, risk_level="moderate")
        except Exception:
            return RiskVerdict(passed=True, risk_level="moderate")

        # 读最近一次组合聚合结果
        summary = self._load_latest_portfolio_summary()
        if summary is None:
            # 数据未就绪：保守通过，记录一次日志
            logger.debug("[Coordinator] 组合风控：尚无 MultiSymbolKelly 快照，透传通过")
            return RiskVerdict(passed=True, risk_level="moderate")

        total_risk = float(summary.get("total_risk") or 0.0)
        corr_risk = float(summary.get("correlation_risk") or 0.0)
        symbol = getattr(context, "symbol", None)
        symbol_adj = summary.get("symbol_adjusted", {}).get(symbol) if symbol else None

        try:
            from backend.config import settings as _s
            max_corr = float(getattr(_s, "PORTFOLIO_MAX_CORRELATION_RISK", 0.75))
            max_total = float(getattr(_s, "PORTFOLIO_MAX_RISK", 0.30))
            hard_block = bool(getattr(_s, "PORTFOLIO_RISK_HARD_BLOCK", False))
        except Exception:
            max_corr, max_total, hard_block = 0.75, 0.30, False

        forced: List[str] = []
        passed = True
        reason_code = ""
        reason_text = ""
        risk_level = "moderate"

        if corr_risk > max_corr:
            forced.append(f"相关性风险 {corr_risk:.2f} 超过上限 {max_corr:.2f}")
            if hard_block:
                passed = False
                reason_code = "portfolio_correlation_block"
                reason_text = forced[-1]
            risk_level = "conservative"

        if passed and total_risk > max_total:
            forced.append(f"组合总风险 {total_risk:.2f} 超过上限 {max_total:.2f}")
            if hard_block:
                passed = False
                reason_code = "portfolio_total_risk_block"
                reason_text = forced[-1]
            risk_level = "conservative"

        verdict = RiskVerdict(
            passed=passed,
            risk_level=risk_level,
            reason_code=reason_code,
            reason_text=reason_text,
            portfolio_risk=corr_risk,
            forced_adjustments=forced,
        )
        # 把 symbol 的夹紧仓位放到 metadata（TDI arbitrate 可选地读取）
        if symbol_adj is not None:
            try:
                setattr(verdict, "symbol_adjusted_fraction", float(symbol_adj))
            except Exception:
                pass
        return verdict

    def _load_latest_portfolio_summary(self) -> Optional[Dict[str, Any]]:
        """读取最近一批 MultiSymbolKelly 快照，聚合出 portfolio summary。

        注意：同一 timestamp 下多 symbol 的 correlation_with_others 都写成同一值
        （见 update_kelly_from_outcomes 的实现），此处取任一行即可。
        """
        try:
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import MultiSymbolKelly
            db = MarketSessionLocal()
            try:
                latest = db.query(MultiSymbolKelly).order_by(
                    MultiSymbolKelly.timestamp.desc()
                ).first()
                if latest is None:
                    return None
                # 把同一 timestamp 的全部行取出（±1s 容差）
                same_batch = db.query(MultiSymbolKelly).filter(
                    MultiSymbolKelly.timestamp == latest.timestamp
                ).all()
                total_risk = sum(float(r.risk_contribution or 0.0) for r in same_batch)
                corr_risk = float(latest.correlation_with_others or 0.0)
                symbol_adj = {
                    r.symbol: float(r.adjusted_size or 0.0)
                    for r in same_batch
                }
                return {
                    "total_risk": total_risk,
                    "correlation_risk": corr_risk,
                    "symbol_adjusted": symbol_adj,
                    "computed_at": latest.timestamp,
                }
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[Coordinator] 读取组合快照失败: {e}")
            return None

    # ══════════════════════════════════════════════════
    #  进化参数
    # ══════════════════════════════════════════════════

    def get_evolved_params(self, current_genome: Dict) -> Optional[Dict]:
        """已废弃（2026-06-11）：进化反哺统一走 v5_runtime_gates 通道。

        历史实现只是原样透传 genome，从未真正读取进化结果。
        保留签名兼容外部调用，恒返回 None。
        """
        return None

    def update_kelly_from_outcomes(self, db: Session, outcomes: Optional[List] = None):
        """v3 整改：从交易结果更新 Kelly 统计，写回 MultiSymbolKelly + 更新协调状态。

        设计：
          1. 若 outcomes 为空，读取最近 StrategyTrade 作为 outcomes；
          2. 按 symbol 汇总 win_rate/avg_win/avg_loss，交给 KellyPositionSizer.calculate；
          3. 顺带调用 calculate_portfolio_kelly + PortfolioRiskAggregator 做组合聚合；
          4. 写入 MultiSymbolKelly 快照 + SystemCoordinatorState.last_kelly_update_at。
        """
        from datetime import datetime, timezone
        try:
            from backend.database.models import (
                StrategyTrade as _StrategyTrade,
                MultiSymbolKelly as _MSK,
                SystemCoordinatorState as _SCS,
                Account as _Account,
            )
            from backend.services.rl.kelly_position_sizer import KellyPositionSizer
            from backend.services.rl.portfolio_risk_aggregator import portfolio_risk_aggregator
        except Exception as e:
            logger.debug(f"[Coordinator] Kelly 更新跳过（模块缺失）: {e}")
            return

        sizer = self._kelly_sizer or KellyPositionSizer()
        self._kelly_sizer = sizer

        # 取权益
        try:
            account = db.query(_Account).filter(_Account.is_active == "true").first()
            equity = float(account.current_cash) if account and account.current_cash else 10000.0
        except Exception:
            equity = 10000.0

        # 取近 300 笔已结算交易作为 outcomes
        if not outcomes:
            try:
                outcomes = db.query(_StrategyTrade).filter(
                    _StrategyTrade.pnl.isnot(None),
                    _StrategyTrade.closed_at.isnot(None),
                ).order_by(_StrategyTrade.closed_at.desc()).limit(300).all()
            except Exception as e:
                logger.debug(f"[Coordinator] 读取 StrategyTrade 失败: {e}")
                outcomes = []

        if not outcomes:
            return

        # 按 symbol 聚合
        symbol_stats: Dict[str, Dict[str, Any]] = {}
        for t in outcomes:
            s = getattr(t, "symbol", None) or "UNKNOWN"
            pnl = float(getattr(t, "pnl", 0) or 0)
            pnl_pct = float(getattr(t, "pnl_pct", 0) or 0)
            stats = symbol_stats.setdefault(s, {"wins": [], "losses": [], "history": []})
            stats["history"].append({"pnl": pnl, "pnl_pct": pnl_pct})
            if pnl > 0:
                stats["wins"].append(pnl)
            elif pnl < 0:
                stats["losses"].append(pnl)

        kelly_results: Dict[str, Any] = {}
        for symbol, stats in symbol_stats.items():
            hist = stats["history"]
            if len(hist) < 5:
                continue
            wins, losses = stats["wins"], stats["losses"]
            win_rate = len(wins) / len(hist) if hist else 0.5
            avg_win = sum(wins) / len(wins) if wins else 0.0
            avg_loss = abs(sum(losses) / len(losses)) if losses else 0.01
            try:
                res = sizer.calculate(
                    equity=equity,
                    trade_history=hist,
                    win_rate=win_rate,
                    avg_win=avg_win,
                    avg_loss=avg_loss,
                )
                kelly_results[symbol] = res
            except Exception as e:
                logger.debug(f"[Coordinator] Kelly {symbol} 计算失败: {e}")

        if not kelly_results:
            return

        # 组合聚合
        try:
            allocation = portfolio_risk_aggregator.aggregate(kelly_results, equity=equity)
        except Exception as e:
            logger.debug(f"[Coordinator] 组合聚合失败: {e}")
            allocation = None

        # 写入 multi_symbol_kelly 快照
        now_utc = datetime.now(timezone.utc)
        try:
            if allocation is not None:
                for a in allocation.allocations:
                    db.add(_MSK(
                        timestamp=now_utc,
                        symbol=a.symbol,
                        kelly_fraction=float(a.kelly_fraction or 0.0),
                        adjusted_size=float(a.adjusted_fraction or 0.0),
                        portfolio_fraction=float(getattr(a, "portfolio_fraction", 0.0) or 0.0),
                        risk_contribution=float(getattr(a, "risk_contribution", 0.0) or 0.0),
                        correlation_with_others=float(allocation.correlation_risk or 0.0),
                        calculation_window=len(outcomes),
                    ))
            else:
                for symbol, res in kelly_results.items():
                    db.add(_MSK(
                        timestamp=now_utc,
                        symbol=symbol,
                        kelly_fraction=float(getattr(res, "kelly_fraction", 0.0) or 0.0),
                        adjusted_size=float(getattr(res, "adjusted_fraction", 0.0) or 0.0),
                        portfolio_fraction=0.0,
                        risk_contribution=0.0,
                        correlation_with_others=0.0,
                        calculation_window=len(outcomes),
                    ))

            state = db.query(_SCS).first()
            if state is None:
                state = _SCS(last_kelly_update_at=now_utc)
                db.add(state)
            else:
                state.last_kelly_update_at = now_utc
            # P0-3: DB锁重试 (内联实现，避免依赖外部函数)
            import time as _ktime
            _commit_ok = False
            for _kattempt in range(3):
                try:
                    db.commit()
                    _commit_ok = True
                    break
                except Exception as _ce:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    _err_str = str(_ce)
                    if "database is locked" in _err_str and _kattempt < 2:
                        _wait = 1.0 * (2 ** _kattempt)
                        logger.warning(
                            f"[Coordinator] Kelly写库DB锁, 重试 {_kattempt+2}/3 "
                            f"(等待{_wait:.0f}s)"
                        )
                        _ktime.sleep(_wait)
                        continue
                    raise
            if not _commit_ok:
                logger.error("[Coordinator] Kelly写库失败: 3次重试均失败")
                return
            logger.info(
                f"[Coordinator] Kelly 统计已更新：symbols={len(kelly_results)} "
                f"total_risk={(allocation.total_risk if allocation else 0.0):.2%}"
            )
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning(f"[Coordinator] Kelly 写库失败: {e}")

    def arbitrate_conflicts(
        self,
        base_decision: Any,
        position_advice: Any = None,
        direction_advice: Any = None,
        risk_verdict: Any = None,
    ):
        """v3 整改：显式暴露冲突仲裁方法 — 委托给 TradingDecisionInterface.arbitrate。

        设计文档中的统一入口：协调器对外的仲裁接口。内部直接复用 TDI.arbitrate 的实现，
        避免双份维护，确保"风控 > Kelly > DRL > 进化"优先级单源。
        """
        from backend.services.trading_decision_interface import (
            trading_decision_interface, PositionAdvice, RiskVerdict,
        )
        pa = position_advice if position_advice is not None else PositionAdvice()
        da = direction_advice if direction_advice is not None else PositionAdvice()
        rv = risk_verdict if risk_verdict is not None else RiskVerdict()
        return trading_decision_interface.arbitrate(base_decision, pa, da, rv)

    # ══════════════════════════════════════════════════
    #  参数漂移检测
    # ══════════════════════════════════════════════════

    def check_param_drift(self, current_params: Dict, threshold: float = 0.3) -> bool:
        """
        检测进化参数与DRL训练环境的漂移

        漂移度量: L2距离 / 参数维度
        """
        if not self._last_drl_training_params or not current_params:
            return False

        drift = 0.0
        count = 0
        for key in set(current_params) & set(self._last_drl_training_params):
            c = current_params[key]
            t = self._last_drl_training_params[key]
            if isinstance(c, (int, float)) and isinstance(t, (int, float)) and abs(t) > 1e-10:
                drift += ((c - t) / t) ** 2
                count += 1

        drift = (drift / count) ** 0.5 if count > 0 else 0.0
        if drift > threshold:
            logger.info(f"[Coordinator] 参数漂移={drift:.2f}超过阈值{threshold}，DRL需重训练")
            return True
        return False

    # ══════════════════════════════════════════════════
    #  状态查询
    # ══════════════════════════════════════════════════

    def get_status(self, db: Session) -> Dict[str, Any]:
        """获取协调器完整状态

        v3 整改：kelly_available / risk_aggregator_available 的语义改为
        "flag 已启用 或 实例已惰性实例化"，避免前端在启动初期（尚未触发首次
        开仓 → sizer/aggregator 未实例化）就把开关错误显示为"关闭"。
        """
        optimizer = self._get_rl_optimizer()
        kelly_flag = self._get_flag('ENABLE_KELLY_POSITION')
        portfolio_flag = self._get_flag('ENABLE_PORTFOLIO_RISK')
        return {
            'drl_available': optimizer.is_available if optimizer else False,
            'drl_has_model': (optimizer.model is not None) if optimizer else False,
            'drl_model_version': self._drl_model_version,
            'kelly_available': kelly_flag or (self._kelly_sizer is not None),
            'risk_aggregator_available': portfolio_flag or (self._risk_aggregator is not None),
            'last_training_params': list(self._last_drl_training_params.keys()),
            'feature_flags': {
                'drl_integration': self._get_flag('ENABLE_DRL_INTEGRATION'),
                'kelly_position': kelly_flag,
                'evolution_feedback': self._get_flag('ENABLE_EVOLUTION_FEEDBACK'),
                'portfolio_risk': portfolio_flag,
                'coordinator': self._get_flag('ENABLE_COORDINATOR'),
                'drl_shadow_mode': self._get_flag('DRL_SHADOW_MODE'),
            },
        }

    @staticmethod
    def _get_flag(name: str) -> bool:
        try:
            from backend.config import settings
            return getattr(settings, name, False)
        except Exception:
            return False


# 全局单例
system_coordinator = SystemCoordinator()
