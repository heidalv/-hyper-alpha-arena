"""
RebateArbitrageEngine — 返利/积分套利主引擎

核心调度器，协调扫描、风控、执行、结算流程。
提供 scan_all_strategies / execute_strategy / close_position 等主入口。

Phase C Enhancement:
- 真实下单执行（通过 ExchangeManager + place_order）
- 数据库持久化（RebatePositionDB / RebateOrderDB）
- 启动恢复（从 DB 加载 active positions）
- 单腿失败回滚
- 真实风控上下文（从 DB 查询历史数据）
"""

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .capital_coordinator import CapitalAllocationCoordinator, capital_coordinator
from .models import (
    CapitalAllocation,
    RebateExecutionResult,
    RebatePosition,
    RebatePositionStatus,
    RebateStrategyType,
    StrategyEvaluation,
)
from .position_monitor import RebateArbPositionMonitor, rebate_position_monitor
from .risk_gate import RebateRiskGate, rebate_risk_gate
from .strategies import ALL_STRATEGIES
from .wash_trade_avoider import WashTradeAvoider, wash_trade_avoider

logger = logging.getLogger(__name__)


def _position_risk_exposure_usd(position: RebatePosition) -> float:
    """风控 R5/R6 用敞口：S8 等杠杆方向仓按保证金计，对冲/无杠杆策略仍用名义。"""
    notional = float(position.side_a_size or 0) + float(position.side_b_size or 0)
    sid = position.strategy_type.value.upper()
    if sid == "S8":
        meta = position.metadata if isinstance(position.metadata, dict) else {}
        margin = float(meta.get("margin_usd") or 0)
        if margin <= 0 and notional > 0:
            lev = float(
                meta.get("leverage")
                or (meta.get("side_a") or {}).get("leverage")
                or 10
            )
            margin = notional / max(lev, 1)
        return margin if margin > 0 else notional
    return notional


class RebateArbitrageEngine:
    """返利/积分套利主引擎 — 支持 Paper/Live 双模式真实执行"""

    # 默认值（可被 config 覆盖）
    MIN_MONTHLY_VALUE = 50.0
    MAX_POSITION_USD = 5_000.0
    MAX_TOTAL_VOLUME_7D = 50_000.0
    MAX_HOLDING_DAYS = 30
    DEFAULT_PAPER_MODE = True
    ORDER_FILL_TIMEOUT = 30.0  # 秒，等待订单成交超时
    MAX_SLIPPAGE_PCT = 0.002   # 0.2% 最大滑点容忍

    def __init__(self):
        self._active_positions: Dict[str, RebatePosition] = {}
        self._lock = threading.Lock()
        self._paper_mode = self.DEFAULT_PAPER_MODE
        self._total_rebate_pnl = 0.0
        self._scan_count = 0
        self._execution_count = 0
        self._initialized = False

        # Lazy-loaded dependencies
        self._exchange_manager = None
        self._config = None

        # Event log (thread-safe, capped at 500)
        self._event_log: List[Dict[str, Any]] = []
        self._event_log_lock = threading.Lock()

        # Load config overrides
        self._load_config()

    def _load_config(self):
        """从 YAML 配置加载引擎参数"""
        try:
            from backend.config.rebate_config_loader import rebate_config
            self._config = rebate_config
            if self._config:
                self.MIN_MONTHLY_VALUE = self._config.engine.min_monthly_value
                self.MAX_POSITION_USD = self._config.engine.max_position_usd
                self.MAX_TOTAL_VOLUME_7D = self._config.engine.max_total_volume_7d
                self.MAX_HOLDING_DAYS = self._config.engine.max_holding_days
                self.DEFAULT_PAPER_MODE = self._config.engine.paper_mode
                self._paper_mode = self.DEFAULT_PAPER_MODE
        except Exception as e:
            logger.debug(f"[RebateEngine] Config load fallback: {e}")

    # ──────────── Runtime Config Mutation ────────────

    def apply_config_patch(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """运行时修改引擎/策略/风控配置（无需重启）"""
        changes: Dict[str, Any] = {}

        # 1. Engine-level params
        engine_patch = patch.get("engine", {})
        for key in ("min_monthly_value", "max_position_usd", "max_total_volume_7d", "max_holding_days"):
            if key in engine_patch:
                old = getattr(self, key.upper(), None)
                setattr(self, key.upper(), engine_patch[key])
                changes[f"engine.{key}"] = {"old": old, "new": engine_patch[key]}

        # 2. Strategy params / enabled flags
        strategies_patch = patch.get("strategies", {})
        for sid, spatch in strategies_patch.items():
            if sid in ALL_STRATEGIES and isinstance(spatch, dict):
                params = spatch.get("params")
                if params:
                    ALL_STRATEGIES[sid].update_params(params)
                    changes[f"strategies.{sid}.params"] = params
                if "enabled" in spatch:
                    new_enabled = bool(spatch["enabled"])
                    old_enabled = self._is_strategy_enabled(sid)
                    if old_enabled != new_enabled:
                        if self._config is not None:
                            self._config.get_strategy_config(sid).enabled = new_enabled
                        changes[f"strategies.{sid}.enabled"] = new_enabled

        # 3. Risk gate overrides
        for sid, spatch in strategies_patch.items():
            if isinstance(spatch, dict) and "risk_overrides" in spatch:
                rebate_risk_gate.apply_strategy_overrides(sid, spatch["risk_overrides"])
                changes[f"strategies.{sid}.risk_overrides"] = spatch["risk_overrides"]

        risk_patch = patch.get("risk_gate", {})
        if risk_patch:
            for key, value in risk_patch.items():
                if hasattr(rebate_risk_gate, key.upper()):
                    setattr(rebate_risk_gate, key.upper(), value)
                    changes[f"risk_gate.{key}"] = value

        if not changes:
            logger.debug("[RebateEngine] Config patch no-op")
            return changes

        logger.info(f"[RebateEngine] Config patch applied: {list(changes.keys())}")
        enabled_strategies = sorted(
            key.split(".")[1]
            for key, value in changes.items()
            if key.startswith("strategies.") and key.endswith(".enabled") and value is True
        )
        disabled_strategies = sorted(
            key.split(".")[1]
            for key, value in changes.items()
            if key.startswith("strategies.") and key.endswith(".enabled") and value is False
        )
        event_data: Dict[str, Any] = {"changes": list(changes.keys())}
        if enabled_strategies:
            event_data["enabled_strategies"] = enabled_strategies
        if disabled_strategies:
            event_data["disabled_strategies"] = disabled_strategies
        self._emit_event("config_changed", event_data)
        return changes

    # ──────────── Event Log ────────────

    def _emit_event(self, event_type: str, data: Dict[str, Any] = None) -> None:
        """记录事件到日志（线程安全，上限500条）"""
        event = {
            "ts": time.time(),
            "type": event_type,
            "data": data or {},
        }
        with self._event_log_lock:
            self._event_log.append(event)
            if len(self._event_log) > 500:
                self._event_log = self._event_log[-400:]

    def get_events(self, since: float = 0.0, limit: int = 50) -> List[Dict[str, Any]]:
        """获取事件日志（since 为 unix timestamp）"""
        with self._event_log_lock:
            events = [e for e in self._event_log if e["ts"] > since]
            return events[-limit:]

    def _get_exchange_manager(self):
        """延迟加载 ExchangeManager"""
        if self._exchange_manager is None:
            try:
                from backend.services.exchange.exchange_manager import get_exchange_manager
                self._exchange_manager = get_exchange_manager()
            except Exception as e:
                logger.warning(f"[RebateEngine] ExchangeManager 加载失败: {e}")
        return self._exchange_manager

    def _get_db_session(self):
        """获取数据库 session"""
        try:
            from backend.database.connection import SessionLocal
            return SessionLocal()
        except Exception as e:
            logger.warning(f"[RebateEngine] DB session 获取失败: {e}")
            return None

    def initialize(self):
        """
        引擎初始化：从数据库恢复活跃仓位。
        应在应用启动或 Paper 会话绑定时调用。
        """
        if self._initialized:
            return

        self._load_active_positions()
        self._initialized = True
        logger.info(
            f"[RebateEngine] 初始化完成: "
            f"{len(self._active_positions)} 活跃仓位已恢复"
        )

    def reload_active_positions(self) -> int:
        """强制从 DB 重新加载活跃仓位（对账后调用）。"""
        self._initialized = False
        self.initialize()
        return len(self._active_positions)

    @property
    def paper_mode(self) -> bool:
        return self._paper_mode

    @paper_mode.setter
    def paper_mode(self, value: bool) -> None:
        self._paper_mode = value
        logger.info(f"[RebateEngine] 模式切换: {'paper' if value else 'live'}")

    def set_paper_account(self, paper_account_id: Optional[int]) -> None:
        """关联 Paper 账户并同步 capital_coordinator（同时设置引擎 mode）"""
        if paper_account_id:
            self._paper_mode = True
            capital_coordinator.set_paper_mode(True, paper_account_id)
        else:
            self._paper_mode = False
            capital_coordinator.set_paper_mode(False)
        logger.info(f"[RebateEngine] Paper 账户绑定: {paper_account_id}")

    def _get_trader_profile_for_execution(self, is_paper: bool) -> Optional[Dict[str, Any]]:
        """Paper 执行时加载绑定的专用套利交易员档案（含双模型 ID）。"""
        if not is_paper:
            return None
        account_id = capital_coordinator.get_arbitrage_paper_account_id()
        if not account_id:
            account_id = capital_coordinator.get_paper_account_id()
        if not account_id:
            return None
        try:
            from backend.database.connection import SessionLocal
            from backend.services.rebate_arb.arbitrage_paper_account_service import (
                arbitrage_paper_account_service,
            )

            db = SessionLocal()
            try:
                return arbitrage_paper_account_service._find_trader_arbitrage_profile(db, account_id)
            finally:
                db.close()
        except Exception as exc:
            logger.debug("[RebateEngine] trader profile load failed: %s", exc)
            return None

    # ══════════════════════════════════════════════════
    # 扫描
    # ══════════════════════════════════════════════════

    def scan_all_strategies(
        self,
        incentive_data: Dict[str, Any],
        funding_rates: Dict[str, float] = None,
        account_equity: float = 0.0,
        enabled_strategies: Optional[List[str]] = None,
    ) -> List[StrategyEvaluation]:
        """
        评估所有8种策略 (S1-S8)

        Args:
            incentive_data: 各交易所激励数据
            funding_rates: 资金费率数据 (S5需要)
            account_equity: 账户权益

        Returns:
            按月期望价值降序排列的策略评估列表
        """
        funding_rates = funding_rates or {}
        self._scan_count += 1

        # [2026-07-06 完善] 把"每场所资金费矩阵"注入 incentive_data，让 delta-neutral(SDN)
        # 等策略真拿到真实数据。scan 的 funding_rates 形状在本环境不稳定（空/扁平），
        # 统一在此归一为 {exchange:{symbol:rate}}，缺失时回落到 perp_funding 真实快照。
        incentive_data = self._inject_funding_matrix(incentive_data, funding_rates)

        allowed = {sid.upper() for sid in enabled_strategies} if enabled_strategies else None
        results = []
        for strategy_id, strategy in ALL_STRATEGIES.items():
            try:
                if allowed is not None and strategy_id.upper() not in allowed:
                    continue
                if not self._is_strategy_enabled(strategy_id):
                    continue
                # [2026-07-06 病灶B 自检] 策略对应的积分项目若已结束/转质押/仅监控，
                # 则告警并跳过、不占配额（避免继续"刷已于 3/29 结束的 Aster Stage 6"）。
                if not self._is_strategy_program_active(strategy_id):
                    continue
                # S1/S5 已下线且不在 ALL_STRATEGIES，无需特殊分支
                eval_result = strategy.evaluate(incentive_data, account_equity)
                results.append(eval_result)
            except Exception as e:
                logger.warning(f"[RebateEngine] 策略 {strategy_id} 评估异常: {e}")
                results.append(StrategyEvaluation(
                    strategy_type=RebateStrategyType(strategy_id),
                    is_viable=False,
                    details={"error": str(e)},
                ))

        results.sort(key=lambda x: x.expected_monthly_value, reverse=True)

        viable_count = sum(1 for r in results if r.is_viable)
        logger.info(
            f"[RebateEngine] 扫描完成 #{self._scan_count}: "
            f"{viable_count}/{len(results)} 策略可行"
        )

        return results

    @staticmethod
    def _inject_funding_matrix(
        incentive_data: Dict[str, Any],
        funding_rates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """归一每场所资金费矩阵到 incentive_data['funding_rates']（{exchange:{symbol:rate}}）。

        优先级：
          1. 传入的 funding_rates 若本身已是嵌套 {exchange:{symbol:rate}} → 直接用；
          2. 否则（空 / 扁平 {symbol:rate}）→ 回落到 perp_funding 真实最新快照。
        不修改调用方原 dict（浅拷贝后加键），且失败时静默保留原始 funding_rates。
        """
        try:
            nested = isinstance(funding_rates, dict) and any(
                isinstance(v, dict) for v in funding_rates.values()
            )
            matrix: Dict[str, Any]
            if nested:
                matrix = funding_rates  # 已是 {exchange:{symbol:rate}}
            else:
                from backend.services.rebate_arb.funding_rate_provider import (
                    latest_funding_by_venue,
                )

                matrix = latest_funding_by_venue()
            merged = dict(incentive_data or {})
            merged["funding_rates"] = matrix
            # 保留扁平版本，避免依赖旧形状的策略（如 S5 语义）读不到
            if not nested and funding_rates:
                merged.setdefault("funding_rates_flat", funding_rates)
            return merged
        except Exception as e:  # 数据源异常绝不能中断扫描
            logger.debug("[RebateEngine] inject funding matrix fallback: %s", e)
            merged = dict(incentive_data or {})
            merged.setdefault("funding_rates", funding_rates)
            return merged

    def _is_strategy_enabled(self, strategy_id: str) -> bool:
        """Respect YAML/runtime enabled flags during scanning."""
        try:
            if self._config is None:
                self._load_config()
            if self._config is not None:
                return bool(self._config.get_strategy_config(strategy_id).enabled)
        except Exception as e:
            logger.debug("[RebateEngine] enabled check fallback for %s: %s", strategy_id, e)
        return True

    def _is_strategy_program_active(self, strategy_id: str) -> bool:
        """[2026-07-06 病灶B] 策略对应的离线积分项目是否仍值得刷。

        无对应项目的策略（纯资金费/VIP 等）不受此约束返回 True；对应项目已结束/
        转质押/仅监控时返回 False，并每 ~50 次扫描告警一次（避免刷屏）。
        """
        try:
            from backend.services.rebate_arb.program_registry import (
                is_strategy_program_active,
                strategy_program_status,
            )

            if is_strategy_program_active(strategy_id):
                return True
            if self._scan_count % 50 == 1:
                logger.warning(
                    "[RebateEngine] 策略 %s 对应积分项目状态=%s（非 active），"
                    "本轮跳过、不占配额",
                    strategy_id,
                    strategy_program_status(strategy_id),
                )
            return False
        except Exception as e:
            logger.debug("[RebateEngine] program-active check fallback for %s: %s", strategy_id, e)
            return True

    def get_top_opportunities(
        self,
        incentive_data: Dict[str, Any],
        funding_rates: Dict[str, float] = None,
        account_equity: float = 0.0,
        top_n: int = 3,
    ) -> List[Dict[str, Any]]:
        """获取Top-N机会（过滤最低价值门槛后的结果）"""
        evaluations = self.scan_all_strategies(incentive_data, funding_rates, account_equity)

        opportunities = []
        for ev in evaluations:
            if ev.is_viable and ev.expected_monthly_value >= self.MIN_MONTHLY_VALUE:
                opportunities.append({
                    "strategy_type": ev.strategy_type.value,
                    "expected_monthly_value": ev.expected_monthly_value,
                    "required_volume_usd": ev.required_volume_usd,
                    "risk_score": ev.risk_score,
                    "confidence": ev.confidence,
                    "volume_value_ratio": ev.volume_value_ratio,
                    "details": ev.details,
                })

        return opportunities[:top_n]

    # ══════════════════════════════════════════════════
    # 执行
    # ══════════════════════════════════════════════════

    def execute_strategy(
        self,
        strategy_type: str,
        size_usd: float,
        symbol: str = "",
        opportunity: Optional[Dict] = None,
        mode: Optional[str] = None,
    ) -> RebateExecutionResult:
        """
        执行指定策略

        Paper 模式: 模拟下单，仅记录仓位
        Live 模式: 通过 ExchangeManager 真实下单

        Args:
            strategy_type: 策略类型 (S1-S8)
            size_usd: 交易金额
            symbol: 交易对
            opportunity: 机会详情
            mode: "paper" / "live"，None 使用当前模式

        Returns:
            RebateExecutionResult
        """
        is_paper = (mode or ("paper" if self._paper_mode else "live")) == "paper"

        # 限制单仓位金额
        size_usd = min(size_usd, self.MAX_POSITION_USD)

        # 1. 风控前置检查
        risk_context = self._build_risk_context()
        risk_result = rebate_risk_gate.check_pre_trade(
            strategy_type=strategy_type,
            exchange=opportunity.get("source_exchange", "") if opportunity else "",
            size_usd=size_usd,
            account_equity=self._get_account_equity(),
            context=risk_context,
        )
        if not risk_result.passed:
            self._emit_event("execution_failed", {"strategy": strategy_type, "reason": f"风控拦截: {risk_result.reason}"})
            return RebateExecutionResult(
                success=False,
                error=f"风控拦截: {risk_result.reason}",
                paper_mode=is_paper,
            )

        # 2. 刷量检查（S8 需等待 plan 生成后按开+平名义量校验）
        exchange = opportunity.get("source_exchange", "") if opportunity else ""
        if strategy_type != "S8":
            wash_result = wash_trade_avoider.check_all(
                exchange=exchange,
                proposed_size=size_usd,
                account_equity=self._get_account_equity(),
            )
            if not wash_result.is_safe:
                self._emit_event("execution_failed", {"strategy": strategy_type, "reason": f"刷量规避: {wash_result.recommendation}"})
                return RebateExecutionResult(
                    success=False,
                    error=f"刷量规避: {wash_result.recommendation}",
                    paper_mode=is_paper,
                )

        # 3. 构建执行计划（先于占资，便于 AI 定规模/选币）
        strategy = ALL_STRATEGIES.get(strategy_type)
        if not strategy:
            return RebateExecutionResult(
                success=False,
                error=f"未知策略: {strategy_type}",
                paper_mode=is_paper,
            )

        try:
            trader_profile = self._get_trader_profile_for_execution(is_paper)
            s8_target_margin: Optional[float] = None
            if strategy_type == "S8" and hasattr(strategy, "build_ai_enhanced_plan"):
                from .strategies.s8_asterdex_rh import S8AsterdexRhStrategy

                # S8 单仓策略：已有活跃仓时直接静默跳过本轮——
                # 不再调用 LLM 选币/规划（省 DeepSeek 调用），
                # 也不发 execution_failed 事件（持仓 4h 期间每轮都发会刷屏制造恐慌）
                from backend.services.rebate_arb.arbitrage_paper_account_service import (
                    arbitrage_paper_account_service,
                )

                s8_exchange = (
                    (opportunity.get("source_exchange") if isinstance(opportunity, dict) else "")
                    or "asterdex"
                )
                if is_paper and arbitrage_paper_account_service.has_active_directional_hold(
                    s8_exchange, "S8", ""
                ):
                    return RebateExecutionResult(
                        success=False,
                        error="S8 单仓策略：已有活跃持仓，等待平仓后再开（本轮静默跳过）",
                        paper_mode=is_paper,
                    )

                paper_id = capital_coordinator.get_arbitrage_paper_account_id() if is_paper else None
                resolved = S8AsterdexRhStrategy.resolve_target_margin(
                    account_equity=self._get_account_equity(),
                    paper_account_id=paper_id,
                    exchange="asterdex",
                    leverage=getattr(strategy, "DEFAULT_LEVERAGE", 10),
                )
                s8_target_margin = float(resolved.get("margin_usd") or 0)
                if s8_target_margin > 0:
                    # size_usd 始终表示名义价值；保证金只由 notional / leverage 得出。
                    size_usd = s8_target_margin * float(resolved.get("leverage") or getattr(strategy, "DEFAULT_LEVERAGE", 10) or 1)
                plan = strategy.build_ai_enhanced_plan(
                    size_usd=size_usd,
                    paper_mode=is_paper,
                    trader_profile=trader_profile,
                    target_margin_usd=s8_target_margin,
                    account_equity=self._get_account_equity(),
                    paper_account_id=paper_id,
                )
                if plan.get("skip"):
                    logger.info(
                        f"[RebateEngine] S8 AI skip: {plan.get('skip_reason', 'danger')}"
                    )
                    return RebateExecutionResult(
                        success=False,
                        error=f"AI风控跳过: {plan.get('skip_reason', '')}",
                        paper_mode=is_paper,
                    )
                symbol = plan.get("side_a", {}).get("symbol", symbol)
            else:
                if strategy_type in ("S2", "S4"):
                    raw = strategy.build_execution_plan(
                        size_usd=size_usd, symbol=symbol, paper_mode=is_paper
                    )
                    from backend.services.rebate_arb.volume_program_executor import normalize_volume_plan

                    plan = normalize_volume_plan(raw, size_usd)
                elif strategy_type == "SDN":
                    # [2026-07-06 完善] 把 evaluate 选定的真实最优组合(combo)透传给执行计划，
                    # 否则 SDN 会退回占位场所(长 hyperliquid/空 binance)、执行到错误的腿。
                    combo = None
                    if isinstance(opportunity, dict):
                        combo = opportunity.get("combo") or (
                            opportunity.get("details") or {}
                        ).get("combo")
                    plan = strategy.build_execution_plan(
                        size_usd=size_usd,
                        symbol=symbol,
                        paper_mode=is_paper,
                        combo=combo,
                    )
                else:
                    plan = strategy.build_execution_plan(
                        size_usd=size_usd, symbol=symbol, paper_mode=is_paper
                    )
        except Exception as e:
            return RebateExecutionResult(
                success=False,
                error=f"执行计划构建失败: {e}",
                paper_mode=is_paper,
            )

        source_exchange = (plan.get("side_a") or {}).get("exchange", exchange)
        plan_symbol = symbol or (plan.get("side_a") or {}).get("symbol", "")
        notional_usd = self._plan_notional_usd(plan, fallback=size_usd)
        margin_usd = self._plan_margin_usd(plan, fallback=size_usd)

        # M9: 进化提案 Paper 自动应用 — 按策略缩放保证金（live 不生效）
        if is_paper:
            try:
                from backend.services.rebate_arb.proposal_auto_applier import (
                    get_paper_multiplier,
                )
                _evo_mult = get_paper_multiplier(strategy_type)
                if abs(_evo_mult - 1.0) > 1e-6 and margin_usd > 0:
                    margin_usd = round(margin_usd * _evo_mult, 2)
                    notional_usd = round(notional_usd * _evo_mult, 2)
                    self._sync_plan_sizes(plan, margin_usd, notional_usd)
                    logger.info(
                        f"[RebateEngine] 进化提案缩放 {strategy_type}: x{_evo_mult} "
                        f"→ 保证金${margin_usd:,.2f}"
                    )
            except Exception as _evo_err:
                logger.debug(f"[RebateEngine] 进化缩放跳过: {_evo_err}")

        if strategy_type == "S8":
            planned_round_volume = float((plan.get("rh_metrics") or {}).get("round_volume_usd") or (notional_usd * 2))
            wash_result = wash_trade_avoider.check_all(
                exchange=source_exchange,
                proposed_size=planned_round_volume,
                account_equity=self._get_account_equity(),
            )
            if isinstance(plan, dict):
                plan["wash_trade_check"] = {
                    "is_safe": wash_result.is_safe,
                    "risk_score": wash_result.risk_score,
                    "recommendation": wash_result.recommendation,
                    "proposed_round_volume_usd": round(planned_round_volume, 2),
                    "layer_results": wash_result.layer_results,
                }
            if not wash_result.is_safe:
                self._emit_event("execution_failed", {"strategy": strategy_type, "reason": f"刷量规避: {wash_result.recommendation}"})
                return RebateExecutionResult(
                    success=False,
                    error=f"刷量规避: {wash_result.recommendation}",
                    paper_mode=is_paper,
                )

        # 3.5 Paper：同所方向策略禁止重复持仓 + 交易所子配额校验
        if is_paper and source_exchange:
            hold_model = (plan.get("hold_phase") or plan.get("hold_model") or "")
            is_directional = strategy_type == "S8" or bool(hold_model)
            if is_directional:
                from backend.services.rebate_arb.arbitrage_paper_account_service import (
                    arbitrage_paper_account_service,
                )

                # S8 是单所方向仓，安全稳定优先：Asterdex 任意 S8 活跃仓存在时不叠仓。
                dedupe_symbol = "" if strategy_type == "S8" else plan_symbol
                if arbitrage_paper_account_service.has_active_directional_hold(
                    source_exchange, strategy_type, dedupe_symbol
                ):
                    # 单仓限制是正常保护行为，不是故障——用 skipped 事件，避免前端误报"执行失败"
                    self._emit_event(
                        "execution_skipped",
                        {
                            "strategy": strategy_type,
                            "reason": f"单仓保护：已有活跃持仓（{source_exchange}），等待平仓后再开",
                        },
                    )
                    return RebateExecutionResult(
                        success=False,
                        error=f"已有活跃持仓，等待平仓后再开: {plan_symbol} @ {source_exchange}",
                        paper_mode=is_paper,
                    )

            account_id = capital_coordinator.get_arbitrage_paper_account_id()
            if account_id:
                db_cap = self._get_db_session()
                if db_cap:
                    try:
                        from backend.services.rebate_arb.arbitrage_paper_account_service import (
                            arbitrage_paper_account_service,
                        )

                        cap_info = arbitrage_paper_account_service.compute_max_open_size(
                            db_cap, account_id, source_exchange, strategy_type, margin_usd
                        )
                        if cap_info.get("allowed_usd", 0) <= 0:
                            reason = cap_info.get("reason") or "配额不足"
                            self._emit_event(
                                "execution_failed",
                                {"strategy": strategy_type, "reason": reason},
                            )
                            return RebateExecutionResult(
                                success=False,
                                error=reason,
                                paper_mode=is_paper,
                            )
                        lev = float((plan.get("side_a") or {}).get("leverage") or 1)
                        margin_usd = float(cap_info["allowed_usd"])
                        notional_usd = round(margin_usd * max(lev, 1), 2)
                        self._sync_plan_sizes(plan, margin_usd, notional_usd)
                    except Exception as exc:
                        logger.warning("[RebateEngine][Paper] quota check failed: %s", exc)
                        self._emit_event(
                            "execution_failed",
                            {"strategy": strategy_type, "reason": str(exc)},
                        )
                        return RebateExecutionResult(
                            success=False,
                            error=f"Paper 配额校验失败: {exc}",
                            paper_mode=is_paper,
                        )
                    finally:
                        try:
                            db_cap.close()
                        except Exception:
                            pass

        if notional_usd <= 0:
            return RebateExecutionResult(
                success=False,
                error="有效开仓名义为 0",
                paper_mode=is_paper,
            )

        # 3.8 R5/R6 增量敞口复核 — 与存量敞口同单位
        # （S8 杠杆方向仓按保证金计，其余按双腿名义合计，对齐 _position_risk_exposure_usd）
        side_b_notional = float((plan.get("side_b") or {}).get("size_usd") or 0)
        incremental_exposure = (
            margin_usd if strategy_type == "S8" else notional_usd + side_b_notional
        )
        inc_check = rebate_risk_gate.check_incremental_exposure(
            strategy_type=strategy_type,
            exchange=source_exchange,
            exposure_usd=incremental_exposure,
            account_equity=self._get_account_equity(),
            context=risk_context,
        )
        if not inc_check.passed:
            self._emit_event(
                "execution_failed",
                {"strategy": strategy_type, "reason": f"风控拦截({inc_check.rule_id}): {inc_check.reason}"},
            )
            return RebateExecutionResult(
                success=False,
                error=f"风控拦截({inc_check.rule_id}): {inc_check.reason}",
                paper_mode=is_paper,
            )

        # 4. 资金分配（S8 按保证金占用）
        capital_result = capital_coordinator.request_capital(
            pool="rebate_points_arb",
            amount_usd=margin_usd,
            strategy_id=strategy_type,
        )
        if not capital_result["granted"]:
            self._emit_event("execution_failed", {"strategy": strategy_type, "reason": f"资金不足: 可用${capital_result['remaining']:,.0f}"})
            return RebateExecutionResult(
                success=False,
                error=f"资金不足: 可用${capital_result['remaining']:,.0f}",
                paper_mode=is_paper,
            )

        # 5. 创建仓位
        position_id = f"rebate_{strategy_type}_{uuid.uuid4().hex[:8]}"
        target_exchange = (plan.get("side_b") or {}).get("exchange")

        position = RebatePosition(
            position_id=position_id,
            strategy_type=RebateStrategyType(strategy_type),
            source_exchange=source_exchange,
            target_exchange=target_exchange,
            symbol=plan_symbol,
            side_a_size=notional_usd,
            # 双腿策略对冲腿名义（修复旧版从未赋值导致 B 腿不平仓/敞口统计失效）
            side_b_size=float((plan.get("side_b") or {}).get("size_usd") or 0.0),
            entry_time=time.time(),
            max_hold_seconds=self.MAX_HOLDING_DAYS * 86400,
            status=RebatePositionStatus.ACTIVE,
            paper_mode=is_paper,
            metadata=plan,
        )
        if isinstance(position.metadata, dict):
            position.metadata["margin_usd"] = round(margin_usd, 2)

        # 5.5 执行前置步骤 (S8: USDF铸造等)；usdf_mint_required=true 时失败阻断
        pre_steps = plan.get("pre_steps", [])
        if pre_steps and not self._execute_pre_steps(position, pre_steps, is_paper):
            capital_coordinator.release_capital("rebate_points_arb", margin_usd, strategy_type)
            return RebateExecutionResult(
                success=False,
                error="前置步骤失败（USDF 铸造），已按配置阻断开仓",
                paper_mode=is_paper,
            )

        # 6. 执行下单
        order_results = self._execute_orders(position, plan, is_paper)

        if not order_results["success"]:
            capital_coordinator.release_capital("rebate_points_arb", margin_usd, strategy_type)
            return RebateExecutionResult(
                success=False,
                error=f"下单失败: {order_results.get('error', 'unknown')}",
                paper_mode=is_paper,
            )

        self._sync_position_from_entry_fills(position, order_results, plan)
        if float(position.side_a_size or 0) <= 0:
            capital_coordinator.release_capital("rebate_points_arb", margin_usd, strategy_type)
            return RebateExecutionResult(
                success=False,
                error="Paper 成交名义为 0，已取消仓位",
                paper_mode=is_paper,
            )

        # 更新仓位价格
        position.entry_price_a = order_results.get("price_a", 0.0)
        position.entry_price_b = order_results.get("price_b", 0.0)

        # 6.5 设置持仓阶段 (S8: 需持仓≥60min触发2x时间加成)
        hold_phase = plan.get("hold_phase")
        if hold_phase:
            position.metadata["execution_phase"] = "holding"
            position.metadata["hold_start_time"] = time.time()
            position.metadata["hold_target_time"] = time.time() + hold_phase.get("total_seconds", 3900)
            position.metadata["close_plan"] = plan.get("close_plan")
            position.metadata["post_steps"] = plan.get("post_steps", [])
            # Live 真实拉取开仓前积分快照，平仓后才能算准积分增量
            # （旧版固定写 0 导致 Live 积分增量 = 账户总积分，永远不准）
            rh_before = 0.0
            if not is_paper:
                try:
                    mgr = self._get_exchange_manager()
                    snap_client = mgr.get_client(source_exchange or "asterdex") if mgr else None
                    if snap_client is not None and hasattr(snap_client, "get_points_snapshot"):
                        from backend.services.arbitrage.async_bridge import run_async

                        snap = run_async(snap_client.get_points_snapshot())
                        rh_before = float(getattr(snap, "points_balance", 0) or 0)
                        position.metadata["rh_snapshot_before_source"] = "live_api"
                    else:
                        position.metadata["rh_snapshot_before_source"] = "unavailable"
                        logger.warning(
                            "[RebateEngine] Live 开仓积分快照不可用（无适配器），增量将不准 (pos=%s)",
                            position_id,
                        )
                except Exception as snap_exc:
                    position.metadata["rh_snapshot_before_source"] = f"error:{snap_exc}"
                    logger.warning(
                        "[RebateEngine] Live 开仓积分快照拉取失败: %s (pos=%s)", snap_exc, position_id
                    )
            position.metadata["rh_snapshot_before"] = rh_before
            logger.info(
                f"[RebateEngine] S8 hold phase: 等待 {hold_phase.get('total_seconds', 3900)}s "
                f"后自动平仓 (pos={position_id})"
            )

        # 7. 注册仓位
        with self._lock:
            self._active_positions[position_id] = position
            self._execution_count += 1

        rebate_position_monitor.add_position(position)

        # 8. 持久化到数据库
        self._persist_position(position, order_results)

        if is_paper:
            if isinstance(position.metadata, dict):
                position.metadata["paper_entry_fills"] = order_results.get("paper_entry_fills", {})
                position.metadata["paper_open_cost"] = order_results.get("paper_cost_summary", {})
                if order_results.get("paper_market_quotes"):
                    position.metadata["paper_entry_quotes"] = order_results["paper_market_quotes"]
                entry_fills = order_results.get("paper_entry_fills") or {}
                if entry_fills:
                    first = next(iter(entry_fills.values()), {})
                    position.metadata["funding_rate_at_entry"] = float(first.get("funding_rate") or 0)
            self._apply_paper_open_accounting(position, order_results)

        # 9. 记录交易（刷量规避 — 按名义成交量）
        wash_trade_avoider.record_trade(
            source_exchange,
            notional_usd,
            strategy_type=strategy_type,
            metadata={"margin_usd": margin_usd, "round_leg": "open"},
        )
        if target_exchange:
            wash_trade_avoider.record_trade(target_exchange, notional_usd)

        # 字段对齐前端 formatRebateEventMessage：strategy_type/symbol/side 必填
        self._emit_event("position_opened", {
            "position_id": position_id,
            "strategy": strategy_type,
            "strategy_type": strategy_type,
            "symbol": plan_symbol,
            "side": (plan.get("side_a") or {}).get("side", ""),
            "size_usd": notional_usd, "margin_usd": margin_usd, "paper": is_paper,
        })

        logger.info(
            f"[RebateEngine] 执行 #{self._execution_count}: "
            f"{strategy_type} 保证金${margin_usd:,.2f} 名义${notional_usd:,.0f} "
            f"{'[PAPER]' if is_paper else '[LIVE]'} "
            f"pos={position_id}"
        )

        return RebateExecutionResult(
            success=True,
            position_id=position_id,
            strategy_type=RebateStrategyType(strategy_type),
            side_a_order=order_results.get("order_a"),
            side_b_order=order_results.get("order_b"),
            paper_mode=is_paper,
        )

    # ══════════════════════════════════════════════════
    # 平仓
    # ══════════════════════════════════════════════════

    def _paper_close_already_done(self, position: RebatePosition) -> bool:
        meta = position.metadata if isinstance(position.metadata, dict) else {}
        return bool(meta.get("paper_close_fills"))

    def close_position(self, position_id: str, reason: str = "manual") -> Dict[str, Any]:
        """关闭指定仓位（含真实平仓下单）"""
        with self._lock:
            position = self._active_positions.get(position_id)
            if not position:
                return {"success": False, "error": f"仓位不存在: {position_id}"}
            position.status = RebatePositionStatus.CLOSING

        # S8 close_plan 已执行过 Paper 平仓时，勿再次 simulate（否则会重复记 paper_pnl）
        if position.paper_mode and self._paper_close_already_done(position):
            close_result = {"success": True, "paper": True, "skipped": "already_closed_via_close_plan"}
            logger.info(
                "[RebateEngine][Paper] 跳过重复平仓 simulate: pos=%s reason=%s",
                position_id, reason,
            )
        else:
            close_result = self._execute_close_orders(position)

        # 标记关闭
        with self._lock:
            position.status = RebatePositionStatus.CLOSED

        # 释放资金
        release_margin = self._position_margin_usd(position)
        capital_coordinator.release_capital(
            pool="rebate_points_arb",
            amount_usd=release_margin,
            strategy_id=position.strategy_type.value,
        )

        if position.paper_mode:
            account_id = capital_coordinator.get_arbitrage_paper_account_id()
            if account_id:
                db_rel = self._get_db_session()
                if db_rel:
                    try:
                        from backend.database.connection import sqlite_write_commit
                        from backend.services.rebate_arb.arbitrage_paper_account_service import (
                            arbitrage_paper_account_service,
                        )

                        arbitrage_paper_account_service.release_paper_margin(
                            db_rel,
                            account_id,
                            position.source_exchange or "",
                            release_margin,
                            position_id=position.position_id,
                            strategy_type=position.strategy_type.value,
                            pnl_delta=0.0,
                            note=f"平仓释放 {position.symbol}",
                        )
                        sqlite_write_commit(db_rel, label="paper_margin_release")
                    except Exception as exc:
                        logger.warning("[RebateEngine][Paper] margin release failed: %s", exc)
                        try:
                            db_rel.rollback()
                        except Exception:
                            pass
                    finally:
                        try:
                            db_rel.close()
                        except Exception:
                            pass

        # 更新监控
        rebate_position_monitor.close_position(position_id, reason)

        # 累计收益
        self._total_rebate_pnl += position.current_pnl + position.accumulated_rebate

        # 持久化到 DB
        self._update_position_db(position, reason)
        self._log_performance(position, reason)

        try:
            from backend.services.rebate_arb.rebate_outcome_bridge import record_rebate_close_outcome

            record_rebate_close_outcome(position, reason)
        except Exception as exc:
            logger.debug("[RebateEngine] outcome bridge: %s", exc)

        logger.info(
            f"[RebateEngine] 平仓: {position_id} 原因={reason} "
            f"PnL=${position.current_pnl:.2f} "
            f"返利=${position.accumulated_rebate:.2f}"
        )

        self._emit_event("position_closed", {
            "position_id": position_id, "reason": reason,
            "pnl": position.current_pnl, "rebate": position.accumulated_rebate,
            "points": position.accumulated_points,
        })

        return {
            "success": True,
            "position_id": position_id,
            "pnl": position.current_pnl,
            "rebate": position.accumulated_rebate,
            "points": position.accumulated_points,
            "reason": reason,
            "close_orders": close_result,
        }

    def close_all_positions(self, reason: str = "emergency") -> List[Dict[str, Any]]:
        """紧急平仓所有仓位"""
        results = []
        with self._lock:
            position_ids = list(self._active_positions.keys())

        for pid in position_ids:
            result = self.close_position(pid, reason=reason)
            results.append(result)

        logger.warning(
            f"[RebateEngine] 紧急平仓: {len(results)} 个仓位, 原因={reason}"
        )
        return results

    # ══════════════════════════════════════════════════
    # 结算
    # ══════════════════════════════════════════════════

    def update_volume_and_settle(self, incentive_data: Dict[str, Any]) -> None:
        """更新交易量并结算返利"""
        with self._lock:
            active_positions = [
                pos for pos in self._active_positions.values()
                if pos.status == RebatePositionStatus.ACTIVE
            ]

        for pos in active_positions:
            # 检查超时
            hold_duration = time.time() - pos.entry_time
            if hold_duration > pos.max_hold_seconds:
                logger.info(f"[RebateEngine] 仓位超时: {pos.position_id}")
                self.close_position(pos.position_id, reason="max_hold_exceeded")

    # ══════════════════════════════════════════════════
    # 状态查询
    # ══════════════════════════════════════════════════

    def get_performance_summary(self) -> Dict[str, Any]:
        """获取绩效汇总"""
        monitor_status = rebate_position_monitor.get_status()
        monitor_perf = rebate_position_monitor.get_performance_summary()

        return {
            "engine_mode": "paper" if self._paper_mode else "live",
            "scan_count": self._scan_count,
            "execution_count": self._execution_count,
            "active_positions": monitor_status.get("active_positions", 0),
            "total_rebate_pnl": self._total_rebate_pnl,
            "capital_available": capital_coordinator.get_rebate_available(),
            "capital_utilization": capital_coordinator.get_all_utilization(),
            "monitor": monitor_status,
            "performance": monitor_perf,
        }

    def get_status(self) -> Dict[str, Any]:
        """获取引擎状态"""
        with self._lock:
            active = [
                p for p in self._active_positions.values()
                if p.status == RebatePositionStatus.ACTIVE
            ]
        return {
            "mode": "paper" if self._paper_mode else "live",
            "active_positions": len(active),
            "total_rebate_pnl": self._total_rebate_pnl,
            "scan_count": self._scan_count,
            "execution_count": self._execution_count,
            "initialized": self._initialized,
        }

    def get_all_positions(self) -> List[RebatePosition]:
        """获取所有仓位快照（含 active/closing/closed）"""
        with self._lock:
            return list(self._active_positions.values())

    # ══════════════════════════════════════════════════
    # 下单执行（核心新增）
    # ══════════════════════════════════════════════════

    def _execute_orders(
        self, position: RebatePosition, plan: Dict[str, Any], is_paper: bool
    ) -> Dict[str, Any]:
        """
        执行订单（Paper模式模拟，Live模式真实下单）

        Returns:
            {
                "success": bool,
                "order_a": {...},  # A腿订单结果
                "order_b": {...},  # B腿订单结果
                "price_a": float,
                "price_b": float,
                "error": str,
            }
        """
        side_a = plan.get("side_a")
        side_b = plan.get("side_b")

        if is_paper:
            return self._paper_execute(position, side_a, side_b)

        # ── Live 模式 ──
        return self._live_execute(position, side_a, side_b)

    def _paper_execute(
        self,
        position: RebatePosition,
        side_a: Optional[Dict],
        side_b: Optional[Dict],
    ) -> Dict[str, Any]:
        """Paper模式：真实市价 + 滑点 + 手续费 + 返佣模拟"""
        from .rebate_paper_simulator import (
            build_order_from_fill,
            simulate_leg_fill,
            summarize_fills,
        )

        result: Dict[str, Any] = {
            "success": True,
            "order_a": None,
            "order_b": None,
            "price_a": 0.0,
            "price_b": 0.0,
            "paper_entry_fills": {},
        }
        fills = []
        trade_nature = "intraday"

        def _simulate_leg(leg: Dict, leg_key: str) -> Optional[Dict[str, Any]]:
            from .rebate_paper_market import resolve_paper_market

            symbol = leg.get("symbol", "")
            exchange = leg.get("exchange", "")
            quote = resolve_paper_market(symbol, exchange)
            fill = simulate_leg_fill(
                exchange=exchange,
                side=leg.get("side", "buy"),
                order_type=leg.get("type", "market"),
                size_usd=float(leg.get("size_usd", 0) or 0),
                trade_nature=trade_nature,
                is_close=False,
                market=quote,
                symbol=symbol,
            )
            if fill is None:
                return None
            fills.append(fill)
            result["paper_entry_fills"][leg_key] = fill.to_dict()
            if quote:
                result.setdefault("paper_market_quotes", {})[leg_key] = quote.to_dict()
            return build_order_from_fill(
                leg, fill, order_id=f"paper_{uuid.uuid4().hex[:8]}"
            )

        if side_a:
            order_a = _simulate_leg(side_a, "a")
            if order_a is None:
                symbol_a = side_a.get("symbol", "")
                return {
                    "success": False,
                    "error": f"无法获取 Paper 模拟价格: {symbol_a}",
                    "price_a": 0.0,
                    "order_a": None,
                    "order_b": None,
                    "price_b": 0.0,
                }
            result["order_a"] = order_a
            result["price_a"] = order_a["filled_price"]

        if side_b:
            order_b = _simulate_leg(side_b, "b")
            if order_b is None:
                symbol_b = side_b.get("symbol", "")
                # [2026-07-06 完善] 双腿准原子性：长腿(side_a)已成交但对冲腿(side_b)失败时，
                # 不能留下裸单向敞口——立即回滚 side_a（反向平仓）并把回滚成本诚实计入结果，
                # 让 Paper PnL 反映"失败对冲需付出的解绑代价"，而非假装无事发生。
                rollback_info = None
                if result.get("order_a") and side_a:
                    from .rebate_paper_market import resolve_paper_market

                    close_side = "sell" if side_a.get("side", "buy") == "buy" else "buy"
                    rollback_fill = simulate_leg_fill(
                        exchange=side_a.get("exchange", ""),
                        side=close_side,
                        order_type="market",  # 回滚求快，走 taker
                        size_usd=float(side_a.get("size_usd", 0) or 0),
                        trade_nature="intraday",
                        is_close=True,
                        market=resolve_paper_market(
                            side_a.get("symbol", ""), side_a.get("exchange", "")
                        ),
                        symbol=side_a.get("symbol", ""),
                    )
                    if rollback_fill is not None:
                        fills.append(rollback_fill)
                        rollback_info = rollback_fill.to_dict()
                        logger.warning(
                            "[RebateEngine][Paper] 对冲腿失败，已回滚长腿 %s：回滚成本已计入 "
                            "(pos=%s)",
                            side_a.get("symbol", ""),
                            position.position_id,
                        )
                return {
                    "success": False,
                    "error": f"无法获取 Paper 模拟价格: {symbol_b}",
                    "price_a": result.get("price_a", 0),
                    "order_a": result.get("order_a"),
                    "order_b": None,
                    "price_b": 0.0,
                    "rolled_back": rollback_info is not None,
                    "rollback_fill": rollback_info,
                    "paper_cost_summary": summarize_fills(fills) if fills else {},
                }
            result["order_b"] = order_b
            result["price_b"] = order_b["filled_price"]

        if fills:
            result["paper_cost_summary"] = summarize_fills(fills)
            logger.info(
                f"[RebateEngine][Paper] 开仓成交: fee=${result['paper_cost_summary']['fee_paid']:.4f} "
                f"rebate=${result['paper_cost_summary']['rebate_received']:.4f} "
                f"slip=${result['paper_cost_summary']['slippage_cost_usd']:.4f} "
                f"(pos={position.position_id})"
            )

        return result

    @staticmethod
    def _get_simulated_price(symbol: str, exchange: str = "") -> float:
        """获取真实市价用于 paper 模拟（含 bid/ask 中间价 / mark）"""
        try:
            from .rebate_paper_market import resolve_paper_market

            quote = resolve_paper_market(symbol, exchange)
            if quote and quote.mid > 0:
                return float(quote.mark or quote.mid)
        except Exception:
            pass

        raw = (symbol or "").strip()
        base = raw.split("/")[0].split("-")[0].upper() if raw else ""
        candidates: list[str] = []
        for sym in (raw, base, f"{base}/USDT", f"{base}-USDT"):
            if sym and sym not in candidates:
                candidates.append(sym)

        for sym in candidates:
            try:
                from backend.services.price_cache import price_cache
                cached = price_cache.get(sym, "CRYPTO", "mainnet")
                if cached and cached > 0:
                    return float(cached)
            except Exception:
                pass
            try:
                from backend.services.price_cache import get_cached_price
                price = get_cached_price(sym, "CRYPTO", "mainnet")
                if price and price > 0:
                    return float(price)
            except Exception:
                pass

        try:
            from backend.services.strategy_coordinator import StrategyCoordinator
            price = StrategyCoordinator._get_realtime_price_robust(base or raw, exchange or "binance")
            if price and price > 0:
                return float(price)
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _plan_margin_usd(plan: Dict[str, Any], fallback: float = 0.0) -> float:
        side_a = plan.get("side_a") or {}
        notional = float(side_a.get("size_usd") or fallback or 0)
        lev = float(side_a.get("leverage") or 1)
        if notional > 0 and lev > 0:
            return notional / lev
        margin = float(plan.get("margin_usd") or side_a.get("margin_usd") or 0)
        if margin > 0:
            return margin
        return notional if notional > 0 else float(fallback or 0)

    @staticmethod
    def _plan_notional_usd(plan: Dict[str, Any], fallback: float = 0.0) -> float:
        side_a = plan.get("side_a") or {}
        notional = float(side_a.get("size_usd") or 0)
        if notional > 0:
            return notional
        return float(fallback or 0)

    @staticmethod
    def _sync_plan_sizes(plan: Dict[str, Any], margin_usd: float, notional_usd: float) -> None:
        plan["margin_usd"] = round(margin_usd, 2)
        side_a = plan.setdefault("side_a", {})
        side_a["margin_usd"] = round(margin_usd, 2)
        side_a["size_usd"] = round(notional_usd, 2)
        close_plan = plan.get("close_plan")
        if isinstance(close_plan, dict):
            close_plan["margin_usd"] = round(margin_usd, 2)
            close_plan["size_usd"] = round(notional_usd, 2)
        for step in plan.get("pre_steps") or []:
            if isinstance(step, dict) and step.get("action") == "mint_usdf":
                step["amount_usd"] = round(margin_usd, 2)

    @staticmethod
    def _position_margin_usd(position: RebatePosition) -> float:
        meta = position.metadata if isinstance(position.metadata, dict) else {}
        margin = float(meta.get("margin_usd") or 0)
        if margin > 0:
            return margin
        notional = float(position.side_a_size or 0)
        sid = position.strategy_type.value if position.strategy_type else ""
        if sid == "S8" and notional > 0:
            lev = float(
                meta.get("leverage")
                or (meta.get("side_a") or {}).get("leverage")
                or 10
            )
            return notional / max(lev, 1)
        return notional

    def _sync_position_from_entry_fills(
        self,
        position: RebatePosition,
        order_results: Dict[str, Any],
        plan: Dict[str, Any],
    ) -> None:
        """从 Paper 成交明细同步名义、数量、杠杆到仓位。"""
        from .rebate_paper_simulator import PaperLegFill

        meta = dict(position.metadata or {})
        entry_fills = order_results.get("paper_entry_fills") or {}
        side_a = plan.get("side_a") or {}
        primary_key = "a" if "a" in entry_fills else next(iter(entry_fills.keys()), None)

        if primary_key and primary_key in entry_fills:
            try:
                fill = PaperLegFill(**entry_fills[primary_key])
                if fill.size_usd > 0:
                    position.side_a_size = round(fill.size_usd, 2)
                if fill.size_coins > 0:
                    meta["size_coins"] = round(fill.size_coins, 8)
                    meta["size_coins_display"] = f"{fill.size_coins:.6f} {(position.symbol or '').split('/')[0]}"
                meta["leverage"] = side_a.get("leverage")
                meta["order_type"] = fill.order_type
                meta["is_maker"] = fill.is_maker
            except Exception as exc:
                logger.debug("[RebateEngine] sync fills skip: %s", exc)

        # 同步 B 腿成交名义（双腿对冲策略），保持平仓与敞口统计有据可依
        if "b" in entry_fills:
            try:
                fill_b = PaperLegFill(**entry_fills["b"])
                if fill_b.size_usd > 0:
                    position.side_b_size = round(fill_b.size_usd, 2)
            except Exception as exc:
                logger.debug("[RebateEngine] sync B-leg fill skip: %s", exc)

        order_a = order_results.get("order_a") or {}
        if order_a.get("size") and not meta.get("size_coins"):
            meta["size_coins"] = round(float(order_a["size"]), 8)
        position.symbol = side_a.get("symbol", position.symbol) or position.symbol
        position.metadata = meta

    def _apply_paper_open_accounting(
        self, position: RebatePosition, order_results: Dict[str, Any]
    ) -> None:
        """Paper 开仓后扣减手续费并写入套利 Paper 流水。"""
        account_id = capital_coordinator.get_arbitrage_paper_account_id()
        if not account_id:
            return

        db = self._get_db_session()
        if not db:
            return

        try:
            from backend.database.connection import sqlite_write_commit
            from backend.services.rebate_arb.arbitrage_paper_account_service import (
                arbitrage_paper_account_service,
            )
            from .rebate_paper_simulator import PaperLegFill

            entry_fills = order_results.get("paper_entry_fills") or {}
            for leg_key, fill_dict in entry_fills.items():
                fill = PaperLegFill(**fill_dict)
                arbitrage_paper_account_service.record_paper_leg_fill(
                    db,
                    account_id,
                    fill.exchange,
                    position_id=position.position_id,
                    strategy_type=position.strategy_type.value,
                    phase="open",
                    fee_paid=fill.fee_paid,
                    rebate_received=fill.rebate_received,
                    slippage_cost=fill.slippage_cost_usd,
                    note=f"开仓 {str(leg_key).upper()}腿",
                    metadata={
                        "leg": leg_key,
                        "filled_price": fill.filled_price,
                        "size_coins": fill.size_coins,
                        "slippage_rate": fill.slippage_rate,
                    },
                )
            if float(position.side_a_size or 0) > 0:
                freeze_amt = self._position_margin_usd(position)
                arbitrage_paper_account_service.freeze_paper_margin(
                    db,
                    account_id,
                    position.source_exchange or "",
                    freeze_amt,
                    position_id=position.position_id,
                    strategy_type=position.strategy_type.value,
                    note=f"开仓冻结 {position.symbol}",
                )
            sqlite_write_commit(db, label="paper_open_accounting")
        except Exception as exc:
            logger.warning("[RebateEngine][Paper] 开仓记账失败: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass

    def _paper_close_execute(
        self,
        position: RebatePosition,
        close_specs: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Paper 平仓：反向成交 + 滑点/手续费，更新仓位 PnL。"""
        from .rebate_paper_simulator import (
            PaperLegFill,
            build_order_from_fill,
            calc_leg_round_trip_pnl,
            simulate_leg_fill,
        )

        meta = position.metadata if isinstance(position.metadata, dict) else {}
        entry_raw = meta.get("paper_entry_fills") or {}
        if not entry_raw:
            return {"success": True, "paper": True, "skipped": "no_entry_fills"}

        if meta.get("paper_close_fills"):
            summary = meta.get("paper_close_summary") or {}
            return {
                "success": True,
                "paper": True,
                "skipped": "already_closed",
                "pnl": float(summary.get("net_pnl") or position.current_pnl or 0),
                "rebate": float(summary.get("total_rebate") or position.accumulated_rebate or 0),
            }

        # 默认平掉所有已开仓腿
        if not close_specs:
            plan = meta
            close_specs = []
            for entry_key in ("a", "b"):
                if entry_key not in entry_raw:
                    continue
                entry_fill = PaperLegFill(**entry_raw[entry_key])
                side_info = plan.get(f"side_{entry_key}", {})
                close_specs.append({
                    "leg_key": f"close_{entry_key}",
                    "entry_key": entry_key,
                    "exchange": entry_fill.exchange or side_info.get("exchange", ""),
                    "symbol": side_info.get("symbol", position.symbol),
                    "side": "sell" if entry_fill.side == "buy" else "buy",
                    "type": side_info.get("type", "market"),
                    "size_usd": entry_fill.size_usd,
                })

        close_orders: Dict[str, Any] = {}
        total_net_pnl = 0.0
        total_rebate = 0.0
        total_gross = 0.0
        close_fill_dicts: Dict[str, Any] = {}

        for spec in close_specs:
            entry_key = spec.get("entry_key", "a")
            entry_dict = entry_raw.get(entry_key)
            if not entry_dict:
                continue

            entry_fill = PaperLegFill(**entry_dict)
            symbol = spec.get("symbol") or position.symbol
            exchange = spec.get("exchange", entry_fill.exchange)
            from .rebate_paper_market import resolve_paper_market

            quote = resolve_paper_market(symbol, exchange)
            if quote is None or quote.mid <= 0:
                return {
                    "success": False,
                    "error": f"无法获取 Paper 平仓价格: {symbol}",
                    "paper": True,
                }

            exit_fill = simulate_leg_fill(
                exchange=exchange,
                side=spec.get("side", "sell"),
                order_type=spec.get("type", "market"),
                size_usd=float(spec.get("size_usd", entry_fill.size_usd) or entry_fill.size_usd),
                trade_nature="intraday",
                is_close=True,
                market=quote,
                symbol=symbol,
            )
            if exit_fill is None:
                return {"success": False, "error": "paper_close_simulation_failed", "paper": True}

            leg_pnl = calc_leg_round_trip_pnl(entry_fill, exit_fill)
            total_net_pnl += leg_pnl["net_pnl"]
            total_gross += leg_pnl["gross_pnl"]
            total_rebate += leg_pnl["total_rebate"]

            leg_key = spec.get("leg_key", f"close_{entry_key}")
            close_leg = {
                "exchange": spec.get("exchange", entry_fill.exchange),
                "symbol": symbol,
                "side": spec.get("side", "sell"),
                "type": spec.get("type", "market"),
                "size_usd": exit_fill.size_usd,
            }
            close_orders[leg_key] = build_order_from_fill(
                close_leg,
                exit_fill,
                order_id=f"paper_close_{uuid.uuid4().hex[:8]}",
            )
            close_fill_dicts[leg_key] = exit_fill.to_dict()

            self._apply_paper_close_leg_accounting(
                position,
                exit_fill,
                gross_pnl=leg_pnl["gross_pnl"],
                leg_key=leg_key,
            )

        # [2026-07-06 完善] delta-neutral 仓：两腿价格波动相互抵消，close 的 total_net_pnl
        # 只含手续费/返佣，缺了持仓期资金费价差这一经济核心。此处按 funding_meta 累计资金费盈亏。
        funding_pnl = 0.0
        funding_meta = meta.get("funding_meta") if isinstance(meta, dict) else None
        if meta.get("delta_neutral") and isinstance(funding_meta, dict):
            try:
                from backend.services.rebate_arb.funding_rate_provider import hold_funding_pnl

                notional = float(position.side_a_size or 0)
                elapsed = max(0.0, time.time() - float(position.entry_time or time.time()))
                funding_pnl = hold_funding_pnl(
                    float(funding_meta.get("net_funding_per_day", 0.0)),
                    notional,
                    elapsed,
                )
                total_net_pnl += funding_pnl
                logger.info(
                    "[RebateEngine][Paper] delta-neutral 资金费累计: net/day=%.6f 持仓%.1fh "
                    "→ funding_pnl=$%.4f (pos=%s)",
                    float(funding_meta.get("net_funding_per_day", 0.0)),
                    elapsed / 3600.0,
                    funding_pnl,
                    position.position_id,
                )
            except Exception as exc:
                logger.debug("[RebateEngine][Paper] 资金费累计跳过: %s", exc)

        position.current_pnl = total_net_pnl
        position.accumulated_rebate = total_rebate
        meta["paper_close_fills"] = close_fill_dicts
        meta["paper_close_summary"] = {
            "gross_pnl": total_gross,
            "net_pnl": total_net_pnl,
            "total_rebate": total_rebate,
            "funding_pnl": round(funding_pnl, 6),
        }
        position.metadata = meta

        logger.info(
            f"[RebateEngine][Paper] 平仓完成: gross=${total_gross:.4f} "
            f"net=${total_net_pnl:.4f} rebate=${total_rebate:.4f} "
            f"(pos={position.position_id})"
        )

        return {
            "success": True,
            "paper": True,
            "close_orders": close_orders,
            "pnl": total_net_pnl,
            "rebate": total_rebate,
        }

    def _apply_paper_close_leg_accounting(
        self,
        position: RebatePosition,
        exit_fill: Any,
        *,
        gross_pnl: float,
        leg_key: str,
    ) -> None:
        """Paper 平仓单腿记账：扣平仓费 + 计入价格盈亏。"""
        account_id = capital_coordinator.get_arbitrage_paper_account_id()
        if not account_id:
            return

        db = self._get_db_session()
        if not db:
            return

        try:
            from backend.database.connection import sqlite_write_commit
            from backend.services.rebate_arb.arbitrage_paper_account_service import (
                arbitrage_paper_account_service,
            )

            arbitrage_paper_account_service.record_paper_leg_fill(
                db,
                account_id,
                exit_fill.exchange,
                position_id=position.position_id,
                strategy_type=position.strategy_type.value,
                phase="close",
                fee_paid=exit_fill.fee_paid,
                rebate_received=exit_fill.rebate_received,
                slippage_cost=exit_fill.slippage_cost_usd,
                pnl_delta=gross_pnl,
                note=f"平仓 {leg_key}",
                metadata={
                    "leg": leg_key,
                    "filled_price": exit_fill.filled_price,
                    "slippage_rate": exit_fill.slippage_rate,
                },
            )
            sqlite_write_commit(db, label="paper_close_accounting")
        except Exception as exc:
            logger.warning("[RebateEngine][Paper] 平仓记账失败: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass

    def _live_execute(
        self,
        position: RebatePosition,
        side_a: Optional[Dict],
        side_b: Optional[Dict],
    ) -> Dict[str, Any]:
        """Live模式：通过交易所API真实下单"""
        from backend.services.arbitrage.async_bridge import run_async
        from backend.services.exchange.base_exchange_client import (
            ExchangeOrder,
            OrderSide,
            OrderType,
        )

        mgr = self._get_exchange_manager()
        if mgr is None:
            return {"success": False, "error": "exchange_manager_unavailable"}

        result = {"success": True, "order_a": None, "order_b": None, "price_a": 0.0, "price_b": 0.0}

        # ── 执行 A 腿 ──
        if side_a:
            leg_a_result = self._execute_single_leg(
                mgr, side_a, "A", position.position_id, run_async
            )
            if not leg_a_result["success"]:
                return {
                    "success": False,
                    "error": f"A腿失败: {leg_a_result.get('error', '')}",
                }
            result["order_a"] = leg_a_result
            result["price_a"] = leg_a_result.get("filled_price", 0.0)

        # ── 执行 B 腿 ──
        if side_b:
            leg_b_result = self._execute_single_leg(
                mgr, side_b, "B", position.position_id, run_async
            )
            if not leg_b_result["success"]:
                # B 腿失败 → 回滚 A 腿
                logger.warning(
                    f"[RebateEngine] B腿失败，回滚A腿: {position.position_id}"
                )
                self._rollback_leg(mgr, side_a, result.get("order_a"), run_async)
                return {
                    "success": False,
                    "error": f"B腿失败(已回滚A): {leg_b_result.get('error', '')}",
                }
            result["order_b"] = leg_b_result
            result["price_b"] = leg_b_result.get("filled_price", 0.0)

        return result

    def _execute_single_leg(
        self, mgr, leg: Dict, leg_name: str, position_id: str, run_async_fn
    ) -> Dict[str, Any]:
        """执行单腿订单"""
        from backend.services.exchange.base_exchange_client import (
            ExchangeOrder,
            OrderSide,
            OrderType,
        )

        exchange_name = leg.get("exchange", "")
        symbol = leg.get("symbol", "")
        side = leg.get("side", "buy")
        order_type = leg.get("type", "market")
        size_usd = leg.get("size_usd", 0)

        # 获取交易所客户端
        client = mgr.get_client(exchange_name)
        if client is None:
            return {"success": False, "error": f"no_client_{exchange_name}"}

        # 获取最新价格计算数量
        try:
            orderbook = run_async_fn(client.get_orderbook(symbol, depth=5))
            if side == "buy":
                ref_price = orderbook.get("asks", [[0]])[0][0] if orderbook.get("asks") else 0
            else:
                ref_price = orderbook.get("bids", [[0]])[0][0] if orderbook.get("bids") else 0

            if ref_price <= 0:
                return {"success": False, "error": f"无法获取 {symbol} 价格"}

            size = size_usd / ref_price
        except Exception as e:
            return {"success": False, "error": f"获取价格失败: {e}"}

        # 构建订单
        order = ExchangeOrder(
            order_id=f"rebate_{leg_name}_{position_id}_{int(time.time())}",
            symbol=symbol,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            order_type=OrderType.LIMIT if order_type == "limit" else OrderType.MARKET,
            size=size,
            price=ref_price if order_type == "limit" else None,
        )

        # 下单
        try:
            order_result = run_async_fn(client.place_order(order))
        except Exception as e:
            return {"success": False, "error": f"下单异常: {e}"}

        if not order_result:
            return {"success": False, "error": "下单返回空结果"}

        # 提取成交信息
        exchange_order_id = order_result.get("id", order_result.get("order_id", ""))
        filled_price = order_result.get("average", order_result.get("price", ref_price)) or ref_price
        filled_size = order_result.get("filled", size)
        fee_paid = order_result.get("fee", {}).get("cost", 0.0) if isinstance(order_result.get("fee"), dict) else 0.0
        status = order_result.get("status", "filled")

        # Limit 单需要等待成交
        if order_type == "limit" and status not in ("filled", "closed"):
            fill_timeout = None
            if leg.get("taker_fallback"):
                fill_timeout = float(leg.get("taker_fallback_seconds") or self.ORDER_FILL_TIMEOUT)
            fill_result = self._wait_for_fill(
                client, exchange_order_id, symbol, run_async_fn, timeout=fill_timeout
            )
            if fill_result:
                filled_price = fill_result.get("average", filled_price)
                filled_size = fill_result.get("filled", filled_size)
                fee_paid = fill_result.get("fee", {}).get("cost", 0.0) if isinstance(fill_result.get("fee"), dict) else fee_paid
                status = fill_result.get("status", status)

            if status not in ("filled", "closed"):
                # 取消未成交订单
                try:
                    run_async_fn(client.cancel_order(exchange_order_id, symbol))
                except Exception:
                    pass

                # stage6 Maker 优先：挂单超时后按配置回退 Taker 市价单
                if leg.get("taker_fallback"):
                    logger.info(
                        "[RebateEngine] %s腿限价单超时，回退Taker市价: %s %s",
                        leg_name, symbol, side,
                    )
                    fallback_order = ExchangeOrder(
                        order_id=f"rebate_{leg_name}_tkfb_{position_id}_{int(time.time())}",
                        symbol=symbol,
                        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        size=size,
                        price=None,
                    )
                    try:
                        fb_result = run_async_fn(client.place_order(fallback_order))
                    except Exception as e:
                        return {"success": False, "error": f"Taker回退下单异常: {e}"}
                    if not fb_result:
                        return {"success": False, "error": "Taker回退下单返回空结果"}
                    exchange_order_id = fb_result.get("id", fb_result.get("order_id", exchange_order_id))
                    filled_price = fb_result.get("average", fb_result.get("price", ref_price)) or ref_price
                    filled_size = fb_result.get("filled", size)
                    fee_paid = fb_result.get("fee", {}).get("cost", 0.0) if isinstance(fb_result.get("fee"), dict) else 0.0
                    status = fb_result.get("status", "filled")
                    order_type = "market"  # 实际成交为 Taker，回填订单类型
                else:
                    return {"success": False, "error": f"限价单超时未成交: {status}"}

        return {
            "success": True,
            "order_id": exchange_order_id,
            "exchange": exchange_name,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "size": filled_size,
            "size_usd": size_usd,
            "filled_price": filled_price,
            "fee_paid": fee_paid,
            "status": status,
        }

    def _wait_for_fill(
        self, client, order_id: str, symbol: str, run_async_fn,
        timeout: Optional[float] = None,
    ) -> Optional[Dict]:
        """等待限价单成交（轮询，默认最多 ORDER_FILL_TIMEOUT 秒）"""
        deadline = time.time() + float(timeout or self.ORDER_FILL_TIMEOUT)
        poll_interval = 1.0

        while time.time() < deadline:
            try:
                # 大多数 CCXT 适配器支持 fetch_order
                order_info = run_async_fn(
                    client._exchange.fetch_order(order_id, symbol)
                ) if hasattr(client, '_exchange') else None

                if order_info and order_info.get("status") in ("filled", "closed"):
                    return order_info
            except Exception:
                pass

            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 5.0)

        return None

    def _rollback_leg(self, mgr, leg: Optional[Dict], order_result: Optional[Dict], run_async_fn):
        """回滚已执行的A腿（反向平仓）"""
        if not leg or not order_result or not order_result.get("success"):
            return

        from backend.services.exchange.base_exchange_client import (
            ExchangeOrder,
            OrderSide,
            OrderType,
        )

        exchange_name = order_result.get("exchange", leg.get("exchange", ""))
        symbol = order_result.get("symbol", leg.get("symbol", ""))
        size = order_result.get("size", 0)
        original_side = order_result.get("side", leg.get("side", "buy"))

        client = mgr.get_client(exchange_name)
        if client is None or size <= 0:
            logger.error(f"[RebateEngine] 回滚失败: 无法获取客户端 {exchange_name}")
            return

        # 反向下单
        rollback_side = OrderSide.SELL if original_side == "buy" else OrderSide.BUY
        rollback_order = ExchangeOrder(
            order_id=f"rollback_{int(time.time())}",
            symbol=symbol,
            side=rollback_side,
            order_type=OrderType.MARKET,
            size=size,
            reduce_only=True,
        )

        try:
            run_async_fn(client.place_order(rollback_order))
            logger.info(f"[RebateEngine] 回滚成功: {exchange_name} {symbol} {size}")
        except Exception as e:
            logger.error(f"[RebateEngine] 回滚下单异常: {e}")

    def _execute_close_orders(self, position: RebatePosition) -> Dict[str, Any]:
        """执行平仓订单（关闭仓位时调用）"""
        if position.paper_mode:
            return self._paper_close_execute(position)

        from backend.services.arbitrage.async_bridge import run_async
        from backend.services.exchange.base_exchange_client import (
            ExchangeOrder,
            OrderSide,
            OrderType,
        )

        mgr = self._get_exchange_manager()
        if mgr is None:
            logger.warning(f"[RebateEngine] 平仓时无法获取 ExchangeManager")
            return {"success": False, "error": "exchange_manager_unavailable"}

        results = {}
        plan = position.metadata or {}
        symbol = position.symbol

        # 平仓 A 腿
        if position.source_exchange and position.side_a_size > 0:
            client = mgr.get_client(position.source_exchange)
            if client:
                side_a_info = plan.get("side_a", {})
                original_side = side_a_info.get("side", "buy")
                close_side = OrderSide.SELL if original_side == "buy" else OrderSide.BUY

                try:
                    # 用 entry_price 估算数量
                    size = position.side_a_size / max(position.entry_price_a, 1.0)
                    close_order = ExchangeOrder(
                        order_id=f"close_A_{position.position_id}",
                        symbol=symbol,
                        side=close_side,
                        order_type=OrderType.MARKET,
                        size=size,
                        reduce_only=True,
                    )
                    close_result = run_async(client.place_order(close_order))
                    results["close_a"] = close_result
                except Exception as e:
                    logger.error(f"[RebateEngine] 平仓A腿异常: {e}")
                    results["close_a_error"] = str(e)

        # 平仓 B 腿
        if position.target_exchange and position.side_b_size > 0:
            client = mgr.get_client(position.target_exchange)
            if client:
                side_b_info = plan.get("side_b", {})
                original_side = side_b_info.get("side", "sell")
                close_side = OrderSide.BUY if original_side == "sell" else OrderSide.SELL

                try:
                    size = position.side_b_size / max(position.entry_price_b, 1.0)
                    close_order = ExchangeOrder(
                        order_id=f"close_B_{position.position_id}",
                        symbol=symbol,
                        side=close_side,
                        order_type=OrderType.MARKET,
                        size=size,
                        reduce_only=True,
                    )
                    close_result = run_async(client.place_order(close_order))
                    results["close_b"] = close_result
                except Exception as e:
                    logger.error(f"[RebateEngine] 平仓B腿异常: {e}")
                    results["close_b_error"] = str(e)

        results["success"] = "close_a_error" not in results and "close_b_error" not in results
        return results

    # ══════════════════════════════════════════════════
    # S8 生命周期方法: pre_steps / hold / close_plan / post_steps
    # ══════════════════════════════════════════════════

    @staticmethod
    def _usdf_mint_required() -> bool:
        """S8 配置项 usdf_mint_required：铸造失败是否阻断开仓（默认不阻断，回退 USDT）"""
        try:
            from backend.config.rebate_config_loader import rebate_config

            return bool(
                rebate_config.get_strategy_config("S8").params.get("usdf_mint_required", False)
            )
        except Exception:
            return False

    def _execute_pre_steps(
        self, position: RebatePosition, pre_steps: List[Dict[str, Any]], is_paper: bool
    ) -> bool:
        """
        执行前置步骤 (S8: USDF铸造)

        默认 best-effort：失败回退 USDT 保证金（损失 USDF 资产积分但不丢轮次），
        同时发出 usdf_mint_failed 告警事件；配置 usdf_mint_required=true 时阻断开仓。

        Returns:
            False 表示关键前置步骤失败且配置为阻断，调用方应取消本次开仓。
        """
        def _mint_failed(reason: str) -> bool:
            position.metadata["usdf_minted"] = False
            position.metadata["pre_step_errors"] = [reason]
            required = self._usdf_mint_required()
            self._emit_event(
                "usdf_mint_failed",
                {
                    "position_id": position.position_id,
                    "strategy": position.strategy_type.value,
                    "reason": reason,
                    "blocked": required,
                    "fallback": "usdt_margin" if not required else None,
                },
            )
            logger.warning(
                "[RebateEngine] USDF mint failed: %s | %s (pos=%s)",
                reason,
                "阻断开仓" if required else "回退 USDT 保证金（损失资产积分）",
                position.position_id,
            )
            return not required

        for step in pre_steps:
            action = step.get("action")

            if action == "mint_usdf":
                amount = step.get("amount_usd", position.side_a_size)
                skip_if_sufficient = step.get("skip_if_sufficient", False)

                if is_paper:
                    # Paper模式: 模拟USDF铸造
                    position.metadata["usdf_minted"] = True
                    position.metadata["usdf_amount"] = amount
                    logger.info(
                        f"[RebateEngine][Paper] USDF mint simulated: ${amount:.0f} "
                        f"(pos={position.position_id})"
                    )
                    continue

                # Live模式: 调用真实API
                mgr = self._get_exchange_manager()
                if mgr is None:
                    if not _mint_failed("exchange_manager_unavailable"):
                        return False
                    continue

                client = mgr.get_client("asterdex")
                if client is None or not hasattr(client, "mint_usdf"):
                    if not _mint_failed("adapter_no_mint_usdf"):
                        return False
                    continue

                try:
                    from backend.services.arbitrage.async_bridge import run_async
                    result = run_async(client.mint_usdf(amount, skip_if_sufficient))

                    if result.get("success"):
                        position.metadata["usdf_minted"] = True
                        position.metadata["usdf_amount"] = result.get("minted", amount)
                        logger.info(
                            f"[RebateEngine] USDF minted: ${result.get('minted', amount):.0f} "
                            f"(pos={position.position_id})"
                        )
                    else:
                        if not _mint_failed(result.get("error", "unknown")):
                            return False
                except Exception as e:
                    if not _mint_failed(str(e)):
                        return False

            elif action == "ensure_cross_margin":
                # Stage 6 资产积分要求全仓（cross-margin）模式
                symbol = step.get("symbol", position.symbol)
                exchange = step.get("exchange", "asterdex")

                if is_paper:
                    position.metadata["margin_mode"] = "cross"
                    logger.info(
                        f"[RebateEngine][Paper] cross-margin simulated: {symbol} "
                        f"(pos={position.position_id})"
                    )
                    continue

                mgr = self._get_exchange_manager()
                client = mgr.get_client(exchange) if mgr else None
                if client is None:
                    logger.warning(
                        "[RebateEngine] ensure_cross_margin: no client for %s，"
                        "继续开仓但资产积分可能不计 (pos=%s)",
                        exchange, position.position_id,
                    )
                    continue

                try:
                    from backend.services.arbitrage.async_bridge import run_async

                    if hasattr(client, "set_margin_mode"):
                        run_async(client.set_margin_mode("cross", symbol))
                        position.metadata["margin_mode"] = "cross"
                        logger.info(
                            f"[RebateEngine] cross-margin set: {symbol} "
                            f"(pos={position.position_id})"
                        )
                    elif hasattr(client, "_exchange") and hasattr(client._exchange, "set_margin_mode"):
                        run_async(client._exchange.set_margin_mode("cross", symbol))
                        position.metadata["margin_mode"] = "cross"
                    else:
                        logger.warning(
                            "[RebateEngine] adapter 不支持 set_margin_mode，"
                            "请手动确认 asterdex 为全仓模式 (pos=%s)",
                            position.position_id,
                        )
                except Exception as e:
                    # 已是全仓模式时多数交易所会报错，按 best-effort 处理
                    logger.info(
                        "[RebateEngine] set_margin_mode 跳过（可能已是全仓）: %s (pos=%s)",
                        e, position.position_id,
                    )

            else:
                logger.debug(f"[RebateEngine] Unknown pre_step action: {action}")

        return True

    def check_and_advance_hold_phases(self) -> List[str]:
        """
        检查所有 HOLDING 阶段仓位, 到期则触发自动平仓

        由 90s tick 循环调用。当 time.time() >= hold_target_time 时:
        1. 执行 close_plan (Taker平仓)
        2. 执行 post_steps (Rh积分快照)
        3. 关闭仓位 (释放资金)

        Returns:
            已完成平仓的 position_id 列表
        """
        completed_ids = []
        now = time.time()

        with self._lock:
            positions_snapshot = list(self._active_positions.values())

        for position in positions_snapshot:
            meta = position.metadata if isinstance(position.metadata, dict) else {}

            # 只处理处于 holding 阶段的仓位
            if meta.get("execution_phase") != "holding":
                continue

            hold_target = meta.get("hold_target_time", 0)
            if hold_target <= 0 or now < hold_target:
                continue

            # 到期前刷新 MTM，避免用陈旧盈亏做退出决策
            try:
                from backend.services.rebate_arb.rebate_position_mtm import refresh_position_mtm

                refresh_position_mtm(position)
            except Exception:
                pass

            pnl = float(position.current_pnl or 0)
            from backend.services.rebate_arb.s8_param_learner import (
                PAPER_HOLD_EXTEND_SECONDS,
                PAPER_HOLD_MAX_EXTENSIONS,
                PAPER_PROFIT_MIN_MARGIN_PCT_TO_CLOSE,
                resolve_position_margin_usd,
            )

            margin = resolve_position_margin_usd(position)
            # 小盈利不到门槛 → 延长持仓，避免「赚一点就跑」
            if pnl > 0 and margin > 0:
                profit_margin_pct = pnl / margin
                if profit_margin_pct < PAPER_PROFIT_MIN_MARGIN_PCT_TO_CLOSE:
                    ext = int(meta.get("hold_extend_count") or 0)
                    if ext < PAPER_HOLD_MAX_EXTENSIONS:
                        meta["hold_extend_count"] = ext + 1
                        meta["hold_target_time"] = now + PAPER_HOLD_EXTEND_SECONDS
                        position.metadata = meta
                        logger.info(
                            "[RebateEngine] S8 盈利偏小(%.2f%% margin)，延长持仓 %ss "
                            "(pos=%s ext=%s/%s)",
                            profit_margin_pct * 100,
                            PAPER_HOLD_EXTEND_SECONDS,
                            position.position_id,
                            ext + 1,
                            PAPER_HOLD_MAX_EXTENSIONS,
                        )
                        continue

            # 亏损单到期：直接平仓，不再延长（止损由 check_exits 保证金 4% 先行触发）
            # 到期 → 执行平仓流程
            elapsed = now - meta.get("hold_start_time", now)
            logger.info(
                f"[RebateEngine] S8 hold完成: pos={position.position_id} "
                f"elapsed={elapsed:.0f}s pnl={pnl:.2f}, 触发平仓"
            )

            # 1. 执行 close_plan
            close_result = self._execute_close_plan(position)
            if not close_result.get("success"):
                logger.error(
                    f"[RebateEngine] S8 close_plan失败: {close_result.get('error')} "
                    f"(pos={position.position_id})"
                )
                position.metadata["close_error"] = close_result.get("error")
                position.metadata["execution_phase"] = "close_failed"
                self._emit_event(
                    "execution_failed",
                    {
                        "strategy": position.strategy_type.value,
                        "position_id": position.position_id,
                        "reason": f"S8 平仓失败: {close_result.get('error')}",
                    },
                )
                continue

            # 2. 执行 post_steps
            self._execute_post_steps(position)

            # 3. 关闭仓位
            self.close_position(position.position_id, reason="hold_phase_complete")
            completed_ids.append(position.position_id)

        return completed_ids

    def _execute_close_plan(self, position: RebatePosition) -> Dict[str, Any]:
        """
        执行S8策略的平仓计划 (Taker Market Sell)

        从 position.metadata["close_plan"] 读取平仓指令并执行
        """
        close_plan = position.metadata.get("close_plan") if isinstance(position.metadata, dict) else None

        if not close_plan:
            # 无close_plan, 回退到通用平仓逻辑
            logger.warning(f"[RebateEngine] No close_plan for {position.position_id}, using default close")
            return self._execute_close_orders(position)

        position.metadata["execution_phase"] = "closing"

        if position.paper_mode:
            close_specs = [{
                "leg_key": "close",
                "entry_key": "a",
                "exchange": close_plan.get("exchange", position.source_exchange),
                "symbol": position.symbol,
                "side": close_plan.get("side", "sell"),
                "type": close_plan.get("type", "market"),
                "size_usd": close_plan.get("size_usd", position.side_a_size),
            }]
            return self._paper_close_execute(position, close_specs=close_specs)

        # Live模式: 通过 _execute_single_leg 执行
        from backend.services.arbitrage.async_bridge import run_async

        mgr = self._get_exchange_manager()
        if mgr is None:
            return {"success": False, "error": "exchange_manager_unavailable"}

        # 构建平仓leg (反向Taker)
        close_leg = {
            "exchange": close_plan.get("exchange", position.source_exchange),
            "symbol": position.symbol,
            "side": close_plan.get("side", "sell"),
            "type": close_plan.get("type", "market"),
            "size_usd": close_plan.get("size_usd", position.side_a_size),
        }

        result = self._execute_single_leg(
            mgr, close_leg, "CLOSE", position.position_id, run_async
        )

        if result.get("success"):
            logger.info(
                f"[RebateEngine] S8 close order filled: "
                f"price={result.get('filled_price', 0):.4f} "
                f"(pos={position.position_id})"
            )

        return result

    def _execute_post_steps(self, position: RebatePosition) -> bool:
        """
        执行后置步骤: Rh积分快照

        记录平仓后的积分状态, 用于计算本轮积分获取量
        """
        post_steps = position.metadata.get("post_steps", []) if isinstance(position.metadata, dict) else []

        for step in post_steps:
            action = step.get("action")

            if action == "snapshot_rh_points":
                if position.paper_mode:
                    # Paper模式: 基于公式模拟积分增量
                    meta = position.metadata if isinstance(position.metadata, dict) else {}
                    metrics = meta.get("rh_metrics") if isinstance(meta.get("rh_metrics"), dict) else {}
                    simulated_rh = float(metrics.get("estimated_rh") or meta.get("estimated_round_rh") or 0)
                    if simulated_rh <= 0:
                        volume = position.side_a_size * 2  # 开+平名义
                        base_rate = 0.0001
                        sym_boost = float(meta.get("symbol_boost") or (meta.get("multiplier_stack") or {}).get("symbol_boost") or 1.0)
                        combined_mult = 80.0 * sym_boost
                        simulated_rh = volume * base_rate * combined_mult
                    already_accrued = float(position.accumulated_points or 0)
                    # MTM 持仓期间已按 estimated_round_rh×时间进度写入 accumulated_points；
                    # 平仓时落到整轮最终值，避免 += simulated_rh 造成约 2 倍重复计分。
                    position.metadata["rh_snapshot_after"] = simulated_rh
                    position.metadata["rh_earned_this_round"] = round(simulated_rh, 4)
                    position.metadata["rh_delta_source"] = "paper_optimizer_estimate"
                    position.accumulated_points = round(max(simulated_rh, already_accrued), 2)
                    position.metadata["execution_phase"] = "completed"
                    logger.info(
                        f"[RebateEngine][Paper] Rh points settled: round={simulated_rh:.2f} "
                        f"(mtm_accrued={already_accrued:.2f}, total={position.accumulated_points:.2f}, "
                        f"pos={position.position_id})"
                    )
                    continue

                # Live模式: 从API获取真实积分
                mgr = self._get_exchange_manager()
                if mgr is None:
                    position.metadata["execution_phase"] = "completed"
                    continue

                client = mgr.get_client(step.get("exchange", "asterdex"))
                if client is None or not hasattr(client, "get_points_snapshot"):
                    position.metadata["execution_phase"] = "completed"
                    continue

                try:
                    from backend.services.arbitrage.async_bridge import run_async
                    snapshot = run_async(client.get_points_snapshot())
                    rh_after = snapshot.points_balance
                    rh_before = position.metadata.get("rh_snapshot_before", 0.0)
                    rh_delta = max(rh_after - rh_before, 0.0)

                    position.metadata["rh_snapshot_after"] = rh_after
                    position.metadata["rh_earned_this_round"] = rh_delta
                    position.accumulated_points += rh_delta
                    position.metadata["execution_phase"] = "completed"

                    logger.info(
                        f"[RebateEngine] Rh points: before={rh_before:.1f} → after={rh_after:.1f} "
                        f"(+{rh_delta:.2f}, pos={position.position_id})"
                    )
                except Exception as e:
                    logger.warning(f"[RebateEngine] Post-step snapshot failed: {e}")
                    position.metadata["execution_phase"] = "completed"

            else:
                logger.debug(f"[RebateEngine] Unknown post_step action: {action}")

        if not post_steps:
            position.metadata["execution_phase"] = "completed"

        return True

    # ══════════════════════════════════════════════════
    # 数据库持久化
    # ══════════════════════════════════════════════════

    def _persist_position(self, position: RebatePosition, order_results: Dict[str, Any]):
        """将新仓位及其订单持久化到数据库"""
        db = self._get_db_session()
        if not db:
            return

        try:
            from backend.database.models import RebatePositionDB, RebateOrderDB
            from backend.database.connection import sqlite_write_commit

            # 创建仓位记录
            pos_db = RebatePositionDB(
                position_id=position.position_id,
                strategy_type=position.strategy_type.value,
                source_exchange=position.source_exchange,
                target_exchange=position.target_exchange,
                symbol=position.symbol,
                side_a_size=position.side_a_size,
                side_b_size=position.side_b_size,
                entry_price_a=position.entry_price_a,
                entry_price_b=position.entry_price_b,
                current_pnl=0.0,
                accumulated_rebate=0.0,
                accumulated_points=0.0,
                entry_time=position.entry_time,
                max_hold_seconds=position.max_hold_seconds,
                status=position.status.value,
                paper_mode=position.paper_mode,
                metadata_json=json.dumps(position.metadata, default=str),
            )
            db.add(pos_db)

            # 创建 A 腿订单记录
            order_a = order_results.get("order_a")
            if order_a:
                db.add(RebateOrderDB(
                    position_id=position.position_id,
                    exchange=order_a.get("exchange", position.source_exchange),
                    leg="A",
                    exchange_order_id=order_a.get("order_id", ""),
                    symbol=order_a.get("symbol", position.symbol),
                    side=order_a.get("side", "buy"),
                    order_type=order_a.get("type", "market"),
                    size=order_a.get("size", 0) or (position.side_a_size / max(position.entry_price_a, 1)),
                    price=order_a.get("filled_price", 0.0),
                    filled_size=order_a.get("size", 0) or 0,
                    filled_price=order_a.get("filled_price", 0.0),
                    status=order_a.get("status", "filled"),
                    fee_paid=order_a.get("fee_paid", 0.0),
                    rebate_received=order_a.get("rebate_received", 0.0),
                ))

            # 创建 B 腿订单记录
            order_b = order_results.get("order_b")
            if order_b:
                db.add(RebateOrderDB(
                    position_id=position.position_id,
                    exchange=order_b.get("exchange", position.target_exchange or ""),
                    leg="B",
                    exchange_order_id=order_b.get("order_id", ""),
                    symbol=order_b.get("symbol", position.symbol),
                    side=order_b.get("side", "sell"),
                    order_type=order_b.get("type", "market"),
                    size=order_b.get("size", 0) or 0,
                    price=order_b.get("filled_price", 0.0),
                    filled_size=order_b.get("size", 0) or 0,
                    filled_price=order_b.get("filled_price", 0.0),
                    status=order_b.get("status", "filled"),
                    fee_paid=order_b.get("fee_paid", 0.0),
                    rebate_received=order_b.get("rebate_received", 0.0),
                ))

            sqlite_write_commit(db, label="rebate_position_create")
        except Exception as e:
            logger.warning(f"[RebateEngine] 持久化仓位失败: {e}")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _update_position_db(self, position: RebatePosition, close_reason: str = ""):
        """更新已关闭仓位的 DB 状态"""
        db = self._get_db_session()
        if not db:
            return

        try:
            from backend.database.models import RebatePositionDB
            from backend.database.connection import sqlite_write_commit

            pos_db = db.query(RebatePositionDB).filter(
                RebatePositionDB.position_id == position.position_id
            ).first()

            if pos_db:
                pos_db.status = position.status.value
                pos_db.current_pnl = position.current_pnl
                pos_db.accumulated_rebate = position.accumulated_rebate
                pos_db.accumulated_points = position.accumulated_points
                pos_db.close_time = time.time()
                sqlite_write_commit(db, label="rebate_position_close")
        except Exception as e:
            logger.warning(f"[RebateEngine] 更新仓位DB失败: {e}")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _log_performance(self, position: RebatePosition, close_reason: str):
        """记录仓位绩效到 performance_logs 表"""
        db = self._get_db_session()
        if not db:
            return

        try:
            from backend.database.models import RebatePerformanceLogDB, RebateTradeOutcomeDB
            from backend.database.connection import sqlite_write_commit
            from backend.services.rebate_arb.schema import ensure_rebate_schema

            from backend.services.rebate_arb.points_aggregation import is_trade_performance_log

            ensure_rebate_schema()
            hold_hours = (time.time() - position.entry_time) / 3600.0
            net_value = float(position.current_pnl or 0) + float(position.accumulated_rebate or 0)

            existing = db.query(RebatePerformanceLogDB).filter(
                RebatePerformanceLogDB.position_id == position.position_id,
            ).order_by(RebatePerformanceLogDB.id.desc()).first()
            if existing and is_trade_performance_log(existing):
                logger.info(
                    "[RebateEngine] 绩效日志已存在，跳过重复写入: pos=%s",
                    position.position_id,
                )
                return

            log_entry = RebatePerformanceLogDB(
                position_id=position.position_id,
                strategy_type=position.strategy_type.value,
                total_pnl=position.current_pnl,
                total_rebate=position.accumulated_rebate,
                total_points=position.accumulated_points,
                hold_hours=hold_hours,
                close_reason=close_reason,
            )
            db.add(log_entry)
            db.add(RebateTradeOutcomeDB(
                position_id=position.position_id,
                strategy_type=position.strategy_type.value,
                symbol=position.symbol,
                mode="paper" if position.paper_mode else "live",
                pnl=float(position.current_pnl or 0),
                rebate=float(position.accumulated_rebate or 0),
                points=float(position.accumulated_points or 0),
                net_value=net_value,
                risk_score=float(position.metadata.get("risk_score", 0.0) if position.metadata else 0.0),
                hold_hours=hold_hours,
                outcome_json=json.dumps({
                    "close_reason": close_reason,
                    "source_exchange": position.source_exchange,
                    "target_exchange": position.target_exchange,
                    "metadata": position.metadata,
                }, ensure_ascii=False, default=str),
            ))
            sqlite_write_commit(db, label="rebate_performance_log")

            # M8: S8 平仓后触发参数学习回流（后台线程，不阻塞引擎）
            if position.strategy_type.value == "S8":
                try:
                    from backend.services.rebate_arb.s8_param_learner import recompute_async
                    recompute_async()
                except Exception as _learn_err:
                    logger.debug(f"[RebateEngine] S8 参数学习触发跳过: {_learn_err}")
        except Exception as e:
            logger.warning(f"[RebateEngine] 绩效日志写入失败: {e}")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass

        # M5: S8/S3 平仓结果接入统一学习闭环（方向胜率纳入 AI 反馈）
        try:
            self._dispatch_learning_outcome(position, close_reason)
        except Exception as e:
            logger.warning(f"[RebateEngine] 学习闭环 dispatch 失败: {e}")

    def _dispatch_learning_outcome(self, position: RebatePosition, close_reason: str):
        """
        将 S8/S3 方向仓平仓结果写入统一学习闭环。

        - StrategyTrade 落库（strategy_trades 表）→ 方向胜率统计直接可用
          （full_auto_trading_service._get_direction_win_rate 按 side+pnl 聚合）
        - process_outcome 内部自动调度全部学习后端（定期复盘/模式挖掘等）
        仅处理方向型策略（S8/S3）；对冲/刷量型策略无方向信号可学。
        """
        sid = position.strategy_type.value
        if sid not in ("S8", "S3"):
            return

        meta = position.metadata if isinstance(position.metadata, dict) else {}
        ai_sig = meta.get("ai_signal") or {}
        side = str((meta.get("side_a") or {}).get("side") or "").lower()
        if not side:
            direction = str(ai_sig.get("ai_direction") or "").lower()
            side = "sell" if direction == "bearish" else "buy"

        margin_usd = float(meta.get("margin_usd") or 0)
        pnl = float(position.current_pnl or 0)
        pnl_pct = (pnl / margin_usd * 100) if margin_usd > 0 else 0.0
        hold_seconds = max(int(time.time() - position.entry_time), 0)

        entry_price = 0.0
        try:
            entry_fills = meta.get("paper_entry_fills") or {}
            entry_price = float((entry_fills.get("a") or {}).get("filled_price") or 0)
        except Exception:
            pass

        from backend.database.connection import SessionLocal
        from backend.services.unified_learning_service import TradeOutcome, unified_learning

        outcome = TradeOutcome(
            source="paper" if position.paper_mode else "live",
            strategy_id=f"rebate_{sid}",
            symbol=position.symbol,
            side=side,
            trade_nature="swing",
            entry_price=entry_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            duration_seconds=hold_seconds,
            confidence=float(ai_sig.get("ai_confidence") or 0),
            position_size=float(position.side_a_size or 0),
            metadata={
                "rebate_strategy": sid,
                "close_reason": close_reason,
                "net_value": pnl + float(position.accumulated_rebate or 0),
                "points": float(position.accumulated_points or 0),
                "rebate": float(position.accumulated_rebate or 0),
                "ai_direction": ai_sig.get("ai_direction"),
                "rh_optimization_mode": meta.get("rh_optimization_mode"),
                # 幂等键：防补偿重放重复落 StrategyTrade
                "paper_position_id": position.position_id,
            },
        )

        db = SessionLocal()
        try:
            unified_learning.process_outcome(db, outcome)
            # L2 收敛: process_outcome 内部已自动调度全部学习后端，不再手动 dispatch
            logger.info(
                "[RebateEngine] 学习闭环已接收 %s 平仓: pos=%s side=%s pnl=%.4f",
                sid, position.position_id, side, pnl,
            )
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _load_active_positions(self):
        """从数据库恢复活跃仓位到内存"""
        db = self._get_db_session()
        if not db:
            return

        with self._lock:
            self._active_positions.clear()
        try:
            with rebate_position_monitor._lock:
                rebate_position_monitor._positions.clear()
        except Exception:
            pass

        try:
            from backend.database.models import RebatePositionDB

            rows = db.query(RebatePositionDB).filter(
                RebatePositionDB.status.in_(["active", "closing"])
            ).all()

            for row in rows:
                try:
                    metadata = json.loads(row.metadata_json) if row.metadata_json else {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                position = RebatePosition(
                    position_id=row.position_id,
                    strategy_type=RebateStrategyType(row.strategy_type),
                    source_exchange=row.source_exchange,
                    target_exchange=row.target_exchange,
                    symbol=row.symbol,
                    side_a_size=row.side_a_size or 0.0,
                    side_b_size=row.side_b_size or 0.0,
                    entry_price_a=row.entry_price_a or 0.0,
                    entry_price_b=row.entry_price_b or 0.0,
                    current_pnl=row.current_pnl or 0.0,
                    accumulated_rebate=row.accumulated_rebate or 0.0,
                    accumulated_points=row.accumulated_points or 0.0,
                    entry_time=row.entry_time,
                    max_hold_seconds=row.max_hold_seconds or 86400 * 30,
                    status=RebatePositionStatus(row.status),
                    paper_mode=row.paper_mode if row.paper_mode is not None else True,
                    metadata=metadata,
                )

                with self._lock:
                    self._active_positions[position.position_id] = position

                # 注册到监控
                rebate_position_monitor.add_position(position)

            logger.info(f"[RebateEngine] 从DB恢复 {len(rows)} 个活跃仓位")
        except Exception as e:
            logger.warning(f"[RebateEngine] 加载仓位失败: {e}")
        finally:
            try:
                db.close()
            except Exception:
                pass

    # ══════════════════════════════════════════════════
    # 风控上下文（从 DB 查询真实数据）
    # ══════════════════════════════════════════════════

    def _build_risk_context(self) -> Dict[str, Any]:
        """构建风控上下文 — 从数据库查询真实历史数据"""
        context = {
            "daily_volumes": {},
            "weekly_volumes": {},
            "active_days_this_week": 0,
            "wash_trade_score": 0.0,
            "exchange_exposure": {},
            "total_rebate_exposure": 0.0,
            "volume_value_ratio": 0.0,
            "campaign_deadline_days": 999,
            "daily_loss_pct": 0.0,
            "fee_change_pct": 0.0,
        }

        # 从内存计算当前敞口
        with self._lock:
            active_positions = [
                p for p in self._active_positions.values()
                if p.status == RebatePositionStatus.ACTIVE
            ]

        context["total_rebate_exposure"] = sum(
            _position_risk_exposure_usd(p) for p in active_positions
        )

        # 按交易所分组敞口
        exchange_exposure: Dict[str, float] = {}
        for p in active_positions:
            exp = _position_risk_exposure_usd(p)
            exchange_exposure[p.source_exchange] = (
                exchange_exposure.get(p.source_exchange, 0) + exp
            )
            if p.target_exchange and float(p.side_b_size or 0) > 0:
                # 对冲第二腿仍按名义计入目标所（S1 等）
                exchange_exposure[p.target_exchange] = (
                    exchange_exposure.get(p.target_exchange, 0) + float(p.side_b_size or 0)
                )
        context["exchange_exposure"] = exchange_exposure

        try:
            from backend.services.rebate_arb.capital_coordinator import capital_coordinator
            if self._paper_mode and capital_coordinator.get_arbitrage_paper_account_id():
                context["paper_verification"] = True
        except Exception:
            pass

        # 从 DB 查询真实交易量数据
        # 注意: rebate_orders.created_at 为 DB 本地墙钟时间（server_default CURRENT_TIMESTAMP,
        # 服务器时区 Asia/Shanghai），因此对比基准统一用 datetime.fromtimestamp（本地时间）。
        db = self._get_db_session()
        if db:
            try:
                from backend.database.models import RebateOrderDB
                from sqlalchemy import func as sa_func

                now = time.time()
                day_ago = now - 86400
                week_ago = now - 7 * 86400

                # 日交易量（按交易所分组）
                daily_rows = db.query(
                    RebateOrderDB.exchange,
                    sa_func.sum(RebateOrderDB.filled_size * RebateOrderDB.filled_price)
                ).filter(
                    RebateOrderDB.created_at >= datetime.fromtimestamp(day_ago)
                ).group_by(RebateOrderDB.exchange).all()

                for exch, vol in daily_rows:
                    context["daily_volumes"][exch] = vol or 0.0

                # 周交易量
                weekly_rows = db.query(
                    RebateOrderDB.exchange,
                    sa_func.sum(RebateOrderDB.filled_size * RebateOrderDB.filled_price)
                ).filter(
                    RebateOrderDB.created_at >= datetime.fromtimestamp(week_ago)
                ).group_by(RebateOrderDB.exchange).all()

                for exch, vol in weekly_rows:
                    context["weekly_volumes"][exch] = vol or 0.0

                # 活跃天数（本周有订单的天数）
                active_days = db.query(
                    sa_func.count(sa_func.distinct(
                        sa_func.date(RebateOrderDB.created_at)
                    ))
                ).filter(
                    RebateOrderDB.created_at >= datetime.fromtimestamp(week_ago)
                ).scalar()
                context["active_days_this_week"] = active_days or 0

                # 日亏损比例
                daily_pnl = db.query(
                    sa_func.sum(RebateOrderDB.fee_paid)
                ).filter(
                    RebateOrderDB.created_at >= datetime.fromtimestamp(day_ago)
                ).scalar() or 0.0

                equity = self._get_account_equity()
                if equity > 0:
                    context["daily_loss_pct"] = abs(daily_pnl) / equity

            except Exception as e:
                logger.debug(f"[RebateEngine] 风控上下文DB查询失败: {e}")
            finally:
                try:
                    db.close()
                except Exception:
                    pass

        # 刷量评分
        context["wash_trade_score"] = wash_trade_avoider.get_current_score() if hasattr(wash_trade_avoider, 'get_current_score') else 0.0

        return context

    def _get_account_equity(self) -> float:
        """获取账户权益"""
        try:
            status = capital_coordinator.get_status()
            return status.total_equity
        except Exception:
            return 0.0


# 模块级单例
rebate_arb_engine = RebateArbitrageEngine()
