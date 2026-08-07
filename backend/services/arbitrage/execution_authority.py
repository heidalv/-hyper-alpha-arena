"""
ExecutionAuthority — 套利执行权威路由（QAA / FullAuto / API 统一入口）

执行优先级（高 → 低）：
1. FullAuto 90s tick（唯一自动执行路径）
2. REST API 手动触发（/api/arbitrage, /api/rebate）
3. QAA Agent handlers（扫描/监控只读；执行经本模块 delegate，带 source 标记）

QAA ArbitragePlugin / RebateArbPlugin 以 read_only 模式 bootstrap：
- scanner / risk / monitor / decider 可用
- executor 统一 delegate 到本模块，禁止绕过
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# QAA 插件 bootstrap 模式：read_only = 仅扫描监控，执行走 delegate
QAA_ARB_PLUGINS_MODE = "read_only"


class ExecutionSource(str, Enum):
    FULLAUTO = "fullauto"
    API = "api"
    QAA = "qaa"


class ExecutionAuthority:
    """统一套利执行入口 — FullAuto 为自动执行权威"""

    _last_v3_tick: Dict[str, Any] = {}
    _last_rebate_tick: Dict[str, Any] = {}
    _qaa_plugins_bootstrapped: bool = False

    # ── V3 套利 ──────────────────────────────────────────────

    @classmethod
    def run_v3_arbitrage_tick(
        cls,
        symbols: List[str],
        snapshot: Any,
        exchange_manager: Any = None,
        source: ExecutionSource = ExecutionSource.FULLAUTO,
    ) -> Dict[str, Any]:
        try:
            from backend.services.rebate_arb.rule_sync_gate import rule_sync_gate
            if rule_sync_gate.is_v3_blocked():
                result = {
                    "execution_source": source.value,
                    "skipped": True,
                    "blocked_by": "rule_sync_gate",
                    "reason": rule_sync_gate.block_reason(),
                }
                cls._last_v3_tick = result
                return result
        except Exception as e:
            logger.debug("[ExecAuthority] RuleSyncGate V3 check skipped: %s", e)

        from .orchestrator import arbitrage_orchestrator
        from .cross_exchange_mid_cache import mid_cache
        from .async_bridge import run_async_safe

        from .cross_exchange_ws_feed import cross_exchange_ws_feed
        hub_running = False
        try:
            from backend.services.market_data_hub import market_data_hub
            hub_running = market_data_hub.is_running
        except Exception:
            pass

        if (
            exchange_manager
            and symbols
            and not cross_exchange_ws_feed.is_running
            and not hub_running
        ):
            try:
                run_async_safe(
                    mid_cache.refresh_symbols(exchange_manager, symbols),
                    default={},
                )
            except Exception as e:
                logger.debug("[ExecAuthority] mid cache refresh: %s", e)

        result = arbitrage_orchestrator.run_tick(
            symbols=symbols,
            snapshot=snapshot,
            exchange_manager=exchange_manager,
        )
        result["execution_source"] = source.value
        cls._last_v3_tick = result
        cls._post_tick_alerts(snapshot, result)
        return result

    @classmethod
    def close_v3_position(
        cls,
        position_id: str,
        reason: str = "manual",
        source: ExecutionSource = ExecutionSource.API,
    ) -> Dict[str, Any]:
        from .orchestrator import arbitrage_orchestrator

        result = arbitrage_orchestrator._close_position(position_id, reason=reason)
        if isinstance(result, dict):
            result["execution_source"] = source.value
        return result if isinstance(result, dict) else {"success": True, "execution_source": source.value}

    # ── Rebate 套利 ──────────────────────────────────────────

    @classmethod
    def run_rebate_tick(
        cls,
        symbols: Optional[List[str]] = None,
        snapshot: Any = None,
        account_equity: Optional[float] = None,
        auto_execute: Optional[bool] = None,
        source: ExecutionSource = ExecutionSource.FULLAUTO,
    ) -> Dict[str, Any]:
        """Rebate 完整 tick：上下文 → 扫描 → 可选自动执行 → 监控退出"""
        from backend.services.rebate_arb.tick_context import build_rebate_tick_context
        from backend.services.rebate_arb.engine import rebate_arb_engine
        from backend.services.rebate_arb.rule_sync_gate import rule_sync_gate

        # M5: 记录 rebate tick 心跳（学习闭环健康检查用）
        try:
            from backend.services.rebate_arb import qaa_rebate_tick as _qrt

            _qrt._last_tick_at = time.time()
        except Exception:
            pass

        ctx = build_rebate_tick_context(
            symbols=symbols,
            snapshot=snapshot,
            account_equity=account_equity,
        )
        equity = ctx["account_equity"]
        evaluations = rebate_arb_engine.scan_all_strategies(
            incentive_data=ctx["incentive_data"],
            funding_rates=ctx["funding_rates"],
            account_equity=equity,
        )
        viable = [e for e in evaluations if e.is_viable]
        gate_state = rule_sync_gate.get_state()

        from backend.services.rebate_arb.strategy_runtime_registry import is_paper_auto_executable

        viable_for_auto = [
            e for e in viable
            if is_paper_auto_executable(e.strategy_type.value)
        ]

        result: Dict[str, Any] = {
            "execution_source": source.value,
            "account_equity": equity,
            "total_evaluated": len(evaluations),
            "viable_count": len(viable),
            "auto_executed": False,
            "exits": [],
            "hold_phases_completed": [],
            "rule_sync_gate": gate_state,
        }

        if viable:
            top = viable[0]
            result["top_strategy"] = top.strategy_type.value
            result["top_monthly_value"] = round(top.expected_monthly_value, 2)

        if viable_for_auto:
            top_auto = viable_for_auto[0]
            result["top_auto_strategy"] = top_auto.strategy_type.value
        else:
            result["top_auto_strategy"] = None

        if auto_execute is None:
            try:
                from backend.config.rebate_config_loader import rebate_config
                auto_execute = rebate_config.engine.auto_execute
            except Exception:
                auto_execute = False

        if auto_execute and viable_for_auto and equity > 0:
            from backend.services.rebate_arb.strategy_coordinator import rank_and_filter

            active_ids = []
            try:
                from backend.services.rebate_arb.position_monitor import rebate_position_monitor

                for p in rebate_position_monitor.get_active_positions():
                    sid = getattr(p.strategy_type, "value", str(p.strategy_type))
                    active_ids.append(sid)
            except Exception:
                pass

            opp_dicts = [
                {
                    "strategy_type": e.strategy_type.value,
                    "is_viable": True,
                    "expected_monthly_value": e.expected_monthly_value,
                    "required_volume_usd": e.required_volume_usd,
                    "risk_score": e.risk_score,
                    "confidence": e.confidence,
                    "details": e.details,
                }
                for e in viable_for_auto
            ]
            coord = rank_and_filter(
                opp_dicts,
                active_strategy_ids=active_ids,
                account_equity=equity,
            )
            result["coordinator"] = coord
            top_pick = coord.get("top")
            strat_id = coord.get("strategy_id") or ""
            size_usd = float(coord.get("size_usd") or 0)
            if top_pick and strat_id:
                result["top_auto_strategy"] = strat_id
                if rule_sync_gate.is_rebate_blocked(strat_id, manual=False):
                    result["auto_execute_blocked"] = True
                    result["blocked_by"] = "rule_sync_gate"
                    result["block_reason"] = rule_sync_gate.block_reason(strat_id)
                    cls._last_rebate_tick = result
                    return result
                min_size = 30.0 if rebate_arb_engine.paper_mode else 50.0
                if size_usd >= min_size:
                    opp_details = top_pick.get("details") if isinstance(top_pick.get("details"), dict) else {}
                    exec_result = cls.execute_rebate_strategy(
                        strategy_type=strat_id,
                        size_usd=size_usd,
                        opportunity=opp_details,
                        source=source,
                    )
                    result["auto_executed"] = exec_result.get("success", False)
                    result["auto_exec_result"] = exec_result

        try:
            from backend.services.rebate_arb.position_monitor import rebate_position_monitor

            exits = rebate_position_monitor.check_exits()
            for exit_info in exits:
                close_r = cls.close_rebate_position(
                    exit_info["position_id"],
                    reason=exit_info.get("reason", "auto_exit"),
                    source=source,
                )
                result["exits"].append(close_r)
        except Exception as e:
            logger.debug("[ExecAuthority] rebate exits: %s", e)

        try:
            completed = rebate_arb_engine.check_and_advance_hold_phases()
            result["hold_phases_completed"] = completed or []
        except Exception as e:
            logger.debug("[ExecAuthority] S8 hold: %s", e)

        cls._last_rebate_tick = result
        return result

    @classmethod
    def scan_rebate_strategies(
        cls,
        symbols: Optional[List[str]] = None,
        snapshot: Any = None,
        account_equity: Optional[float] = None,
        source: ExecutionSource = ExecutionSource.API,
    ) -> Dict[str, Any]:
        from backend.services.rebate_arb.tick_context import build_rebate_tick_context
        from backend.services.rebate_arb.engine import rebate_arb_engine

        ctx = build_rebate_tick_context(symbols, snapshot, account_equity)
        evaluations = rebate_arb_engine.scan_all_strategies(
            incentive_data=ctx["incentive_data"],
            funding_rates=ctx["funding_rates"],
            account_equity=ctx["account_equity"],
        )
        viable = [e for e in evaluations if e.is_viable]
        return {
            "execution_source": source.value,
            "triggered": True,
            "total_evaluated": len(evaluations),
            "viable_count": len(viable),
            "top_opportunity": {
                "strategy_type": viable[0].strategy_type.value,
                "expected_monthly_value": round(viable[0].expected_monthly_value, 2),
            } if viable else None,
        }

    @classmethod
    def execute_rebate_strategy(
        cls,
        strategy_type: str,
        size_usd: float,
        symbol: str = "",
        opportunity: Optional[Dict] = None,
        mode: Optional[str] = None,
        source: ExecutionSource = ExecutionSource.API,
    ) -> Dict[str, Any]:
        from backend.services.rebate_arb.engine import rebate_arb_engine
        from backend.services.rebate_arb.rule_sync_gate import rule_sync_gate

        if rule_sync_gate.is_rebate_blocked(strategy_type, manual=True):
            return {
                "execution_source": source.value,
                "success": False,
                "position_id": None,
                "strategy_type": strategy_type,
                "paper_mode": None,
                "blocked_by": "rule_sync_gate",
                "error": rule_sync_gate.block_reason(strategy_type),
                "rule_sync_gate": rule_sync_gate.get_state(),
            }
        if source == ExecutionSource.API and rule_sync_gate.get_state().get("is_rebate_paused"):
            rule_sync_gate.record_override(
                strategy_type=strategy_type,
                reason="manual API execute override allowed by rule_sync.allow_manual_override",
                risk_acknowledged=True,
            )

        result = rebate_arb_engine.execute_strategy(
            strategy_type=strategy_type,
            size_usd=size_usd,
            symbol=symbol,
            opportunity=opportunity or {},
            mode=mode,
        )
        return {
            "execution_source": source.value,
            "success": result.success,
            "position_id": result.position_id,
            "strategy_type": result.strategy_type.value if result.strategy_type else None,
            "paper_mode": result.paper_mode,
            "error": result.error,
        }

    @classmethod
    def close_rebate_position(
        cls,
        position_id: str,
        reason: str = "manual",
        source: ExecutionSource = ExecutionSource.API,
    ) -> Dict[str, Any]:
        from backend.services.rebate_arb.engine import rebate_arb_engine

        result = rebate_arb_engine.close_position(position_id, reason=reason)
        if isinstance(result, dict):
            result["execution_source"] = source.value
        return result

    @classmethod
    def close_all_rebate_positions(
        cls,
        reason: str = "emergency",
        source: ExecutionSource = ExecutionSource.API,
    ) -> Dict[str, Any]:
        from backend.services.rebate_arb.engine import rebate_arb_engine

        results = rebate_arb_engine.close_all_positions(reason=reason)
        return {
            "execution_source": source.value,
            "success": True,
            "closed_count": len(results),
            "results": results,
        }

    # ── QAA delegate ─────────────────────────────────────────

    @classmethod
    def route_qaa_v3_executor(cls, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """QAA V3 executor → ExecutionAuthority（带 source=qaa）"""
        if QAA_ARB_PLUGINS_MODE == "read_only" and action.startswith("execute"):
            logger.info("[ExecAuthority] QAA V3 execute delegate: %s", action)

        if action == "close_position":
            return cls.close_v3_position(
                payload.get("position_id", ""),
                reason=payload.get("reason", "qaa"),
                source=ExecutionSource.QAA,
            )
        if action in ("execute_funding", "execute_cross_exchange", "execute_basis"):
            return cls._qaa_v3_execute(action, payload)
        return {"ok": False, "error": f"unknown_action: {action}", "execution_source": ExecutionSource.QAA.value}

    @classmethod
    def _qaa_v3_execute(cls, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """QAA 单次执行 — 调用底层 handler 实现并标记来源"""
        try:
            from qaa.domains.arbitrage import handlers as arb_handlers
        except ImportError:
            return {"ok": False, "error": "qaa_not_available"}

        mode = payload.get("mode", "paper")
        dispatch = {
            "execute_funding": arb_handlers._execute_funding,
            "execute_cross_exchange": arb_handlers._execute_cross_exchange,
            "execute_basis": arb_handlers._execute_basis,
        }
        fn = dispatch.get(action)
        if fn is None:
            return {"ok": False, "error": f"unknown_action: {action}"}
        result = fn(payload, mode)
        if isinstance(result, dict):
            result["execution_source"] = ExecutionSource.QAA.value
        return result

    @classmethod
    def route_qaa_rebate_executor(cls, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """QAA Rebate executor → ExecutionAuthority"""
        strategy_map = {
            "execute_maker_hedge": "S1",
            "execute_vip_sprint": "S2",
            "execute_points_mining": "S3",
            "execute_campaign": "S4",
            "execute_funding_points": "S5",
            "execute_cross_fee": "S6",
            "execute_binance_alpha": "S7",
            "execute_asterdex_rh": "S8",
        }
        if action == "close_position":
            return cls.close_rebate_position(
                payload.get("position_id", ""),
                reason=payload.get("reason", "qaa"),
                source=ExecutionSource.QAA,
            )
        if action == "close_all":
            return cls.close_all_rebate_positions(
                reason=payload.get("reason", "qaa_emergency"),
                source=ExecutionSource.QAA,
            )
        if action in strategy_map:
            return cls.execute_rebate_strategy(
                strategy_type=strategy_map[action],
                size_usd=float(payload.get("size_usd", 0)),
                symbol=payload.get("symbol", ""),
                opportunity=payload.get("opportunity", {}),
                mode=payload.get("mode"),
                source=ExecutionSource.QAA,
            )
        return {"ok": False, "error": f"unknown_action: {action}", "execution_source": ExecutionSource.QAA.value}

    @classmethod
    def mark_qaa_plugins_bootstrapped(cls, bootstrapped: bool = True) -> None:
        cls._qaa_plugins_bootstrapped = bootstrapped

    # ── 告警 / 状态 ──────────────────────────────────────────

    @classmethod
    def _post_tick_alerts(cls, snapshot: Any, tick_result: Dict[str, Any]) -> None:
        try:
            from .arbitrage_alert_monitor import arb_alert_monitor
            from .global_capital_coordinator import global_capital_coordinator

            arb_alert_monitor.check_pool_utilization(
                global_capital_coordinator.get_status()
            )
            if tick_result.get("status") == "circuit_breaker_active":
                arb_alert_monitor.on_circuit_breaker("orchestrator tick skipped")

            rates = {}
            if snapshot:
                deriv = getattr(snapshot, "derivatives_snapshot", {}) or {}
                if isinstance(deriv, dict):
                    for sym, d in deriv.items():
                        if isinstance(d, dict):
                            r = float(d.get("funding_rate", 0) or 0)
                            if r:
                                rates[sym] = r
            if rates:
                arb_alert_monitor.check_funding_spikes(rates)
        except Exception as e:
            logger.debug("[ExecAuthority] post-tick alerts: %s", e)

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        hub_status = {}
        try:
            from backend.services.market_data_hub import market_data_hub
            hub_status = market_data_hub.get_status()
        except Exception:
            pass

        return {
            "authority": "fullauto",
            "qaa_arb_plugins_bootstrapped": cls._qaa_plugins_bootstrapped,
            "qaa_plugins_mode": QAA_ARB_PLUGINS_MODE,
            "last_v3_tick": cls._last_v3_tick,
            "last_rebate_tick": cls._last_rebate_tick,
            "market_data_hub": hub_status,
            "execution_priority": [
                ExecutionSource.FULLAUTO.value,
                ExecutionSource.API.value,
                ExecutionSource.QAA.value,
            ],
        }


execution_authority = ExecutionAuthority()
