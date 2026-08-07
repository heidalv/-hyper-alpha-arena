"""
ArbitrageOrchestrator — 套利核心协调器

协调完整的 扫描 -> 风控 -> 执行 -> 监控 管道。
被 full_auto_trading_service._run_arbitrage_tick() 调用。

设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第3节
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .unified_models import (
    ArbAccountSnapshot,
    ArbHedgePosition,
    ArbitrageCapitalPool,
    ArbitrageOpportunity,
    ArbitragePositionMetrics,
    ArbitrageStatus,
    CompositeRiskScore,
    ExecutionMode,
    StrategyType,
)
from .opportunity_scanner import opportunity_scanner
from .arbitrage_risk_chain import arbitrage_risk_chain
from .position_metrics import metrics_tracker
from .emergency_handler import emergency_handler
from .live_executor import LiveExecutor
from .funding_rate_executor import FundingRateExecutor
from .basis_arb_executor import BasisArbExecutor
from .position_persistence import (
    load_active_positions,
    save_position_close,
    save_position_open,
)
from .global_capital_coordinator import global_capital_coordinator
from .yield_metrics import normalize_score_for_sort

logger = logging.getLogger(__name__)


class ArbitrageOrchestrator:
    """
    套利核心协调器

    每个 tick 执行完整管道：
    1. 扫描 — 资金费率 + 跨交易所 + 基差
    2. 风控 — 8项检查链 + 复合评分
    3. 执行 — Paper/Live 双模式
    4. 监控 — 实时指标 + 操作建议 + 熔断器
    """

    # 资金池默认配置
    DEFAULT_MAX_POOL_PCT = 0.30     # 资金池最多占总权益30%
    DEFAULT_DAILY_LOSS_LIMIT = 0.03 # 日亏损上限3%
    DEFAULT_POSITION_SIZE_PCT = 0.20  # 单个仓位最多用资金池20%

    def __init__(self):
        self._load_config()
        default_mode = getattr(self, "_default_mode", "paper")
        self._mode = ExecutionMode.LIVE if default_mode == "live" else ExecutionMode.PAPER
        self._capital_pool = ArbitrageCapitalPool(
            max_pool_pct_of_equity=self.DEFAULT_MAX_POOL_PCT,
            daily_loss_limit_pct=self.DEFAULT_DAILY_LOSS_LIMIT,
        )
        self._active_positions: Dict[str, ArbHedgePosition] = load_active_positions()
        self._position_notional: Dict[str, float] = {}
        self._live_executor = LiveExecutor(mode=default_mode)
        self._funding_executor = FundingRateExecutor()
        self._basis_executor = BasisArbExecutor()
        self._tick_count = 0
        self._last_scan_results: List[Dict[str, Any]] = []
        self._last_risk_results: List[Dict[str, Any]] = []
        self._last_metrics: List[ArbitragePositionMetrics] = []
        self._initialized = False

    def _load_config(self) -> None:
        try:
            from backend.config.arb_config_loader import arb_config
            self.DEFAULT_MAX_POOL_PCT = arb_config.engine.max_pool_pct_of_equity
            self.DEFAULT_DAILY_LOSS_LIMIT = arb_config.engine.daily_loss_limit_pct
            self.DEFAULT_POSITION_SIZE_PCT = arb_config.engine.position_size_pct
            self._basis_scan_enabled = arb_config.scanner.basis_scan_enabled
            self._basis_threshold = arb_config.scanner.basis_entry_threshold_pct
            self._exchange_priority = list(arb_config.scanner.exchange_priority)
            self._funding_primary = arb_config.funding.primary_exchange
            self._funding_hedge = arb_config.funding.hedge_exchange
            self._default_mode = arb_config.engine.default_mode
        except Exception as e:
            logger.debug("[ArbOrchestrator] Config load fallback: %s", e)
            self._basis_scan_enabled = False
            self._basis_threshold = 0.003
            self._exchange_priority = []
            self._funding_primary = "hyperliquid"
            self._funding_hedge = "binance"
            self._default_mode = "paper"

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    @property
    def capital_pool(self) -> ArbitrageCapitalPool:
        return self._capital_pool

    @property
    def active_positions(self) -> Dict[str, ArbHedgePosition]:
        return self._active_positions

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def set_mode(self, mode: str) -> bool:
        """切换执行模式"""
        if mode == "paper":
            self._mode = ExecutionMode.PAPER
            self._live_executor.mode = "paper"
            logger.info("[ArbOrchestrator] 切换到 PAPER 模式")
            return True
        elif mode == "live":
            self._mode = ExecutionMode.LIVE
            self._live_executor.mode = "live"
            logger.warning("[ArbOrchestrator] 切换到 LIVE 模式 — 将执行真实交易！")
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """获取当前状态"""
        return {
            "mode": self._mode.value,
            "tick_count": self._tick_count,
            "active_positions": len(self._active_positions),
            "capital_pool": {
                "total": self._capital_pool.total_pool_usd,
                "allocated": self._capital_pool.allocated_usd,
                "available": self._capital_pool.available_usd,
                "utilization_pct": self._capital_pool.utilization_pct,
            },
            "circuit_breaker_active": emergency_handler.is_circuit_breaker_active(),
            "last_scan_count": len(self._last_scan_results),
        }

    def run_tick(
        self,
        symbols: List[str],
        snapshot: Any,
        exchange_manager: Any = None,
    ) -> Dict[str, Any]:
        """
        执行单次 tick: 扫描 -> 风控 -> 执行 -> 监控

        Args:
            symbols: 交易对列表
            snapshot: UnifiedSnapshot 实例
            exchange_manager: ExchangeManager 实例（用于 Live 模式和跨交易所扫描）

        Returns:
            tick 执行摘要
        """
        self._tick_count += 1
        tick_start = time.time()

        # 熔断器检查
        if emergency_handler.is_circuit_breaker_active():
            logger.info("[ArbOrchestrator] 熔断器激活，跳过本 tick")
            return {"status": "circuit_breaker_active", "tick": self._tick_count}

        # 初始化资金池
        self._init_capital_pool(snapshot)

        result = {
            "tick": self._tick_count,
            "mode": self._mode.value,
            "scanned": 0,
            "risk_passed": 0,
            "executed": 0,
            "monitored": len(self._active_positions),
            "actions": [],
        }

        try:
            # Step 1: 扫描
            opportunities = self._scan(symbols, snapshot, exchange_manager)
            result["scanned"] = len(opportunities)

            # Step 2: 风控检查
            passed_opportunities = self._risk_check(opportunities, snapshot)
            result["risk_passed"] = len(passed_opportunities)

            # Step 3: 执行
            executed = self._execute(passed_opportunities, snapshot, exchange_manager)
            result["executed"] = executed

            # Step 4: 监控
            actions = self._monitor(snapshot)
            result["actions"] = actions

        except Exception as e:
            logger.error(f"[ArbOrchestrator] Tick {self._tick_count} 异常: {e}")
            result["error"] = str(e)

        result["elapsed_ms"] = int((time.time() - tick_start) * 1000)
        return result

    # ── Step 1: 扫描 ─────────────────────────────────

    def _scan(
        self,
        symbols: List[str],
        snapshot: Any,
        exchange_manager: Any,
    ) -> List[Dict[str, Any]]:
        """扫描所有套利机会"""
        opportunities = []

        # 1a. 资金费率扫描
        try:
            funding_opps = opportunity_scanner.scan_opportunities(symbols, snapshot)
            for opp in funding_opps:
                opportunities.append({
                    "source": "funding_rate",
                    "opportunity": opp,
                    "strategy": StrategyType.FUNDING_RATE,
                    "symbol": opp.symbol,
                    "expected_annual_yield": opp.expected_annual_yield,
                    "risk_score": opp.risk_score,
                })
        except Exception as e:
            logger.debug(f"[ArbOrchestrator] 资金费率扫描异常: {e}")

        # 1b. 跨交易所扫描
        if exchange_manager is not None:
            try:
                cross_opps = self._scan_cross_exchange(symbols, exchange_manager)
                opportunities.extend(cross_opps)
            except Exception as e:
                logger.debug(f"[ArbOrchestrator] 跨交易所扫描异常: {e}")

        # 1c. 基差扫描（默认关闭 — HL 无现货）
        if getattr(self, "_basis_scan_enabled", False):
            try:
                basis_opps = self._scan_basis(symbols, snapshot, exchange_manager)
                opportunities.extend(basis_opps)
            except Exception as e:
                logger.debug(f"[ArbOrchestrator] 基差扫描异常: {e}")

        # 按统一排序分排序
        for opp in opportunities:
            opp["sort_score"] = normalize_score_for_sort(
                opp.get("source", ""), opp
            )
        opportunities.sort(key=lambda x: x.get("sort_score", 0), reverse=True)

        self._last_scan_results = opportunities

        if opportunities:
            top = opportunities[0]
            logger.info(
                f"[ArbOrchestrator] Tick {self._tick_count}: "
                f"发现 {len(opportunities)} 个机会, "
                f"最高: {top.get('symbol', '?')} "
                f"score={top.get('sort_score', 0):.4f}"
            )

        return opportunities

    def _scan_cross_exchange(
        self, symbols: List[str], exchange_manager: Any
    ) -> List[Dict[str, Any]]:
        """跨交易所价差扫描 — 全 C(n,2) 组合"""
        from itertools import combinations
        from .async_bridge import run_async_safe
        from .yield_metrics import cross_exchange_score

        results = []
        clients = exchange_manager.get_all_clients()
        if not isinstance(clients, dict) or len(clients) < 2:
            return results

        # 按优先级排序 client
        priority = getattr(self, "_exchange_priority", [])

        def _client_sort_key(item):
            key, client = item
            ex_name = getattr(getattr(client, "exchange_type", None), "value", key)
            try:
                return priority.index(ex_name)
            except ValueError:
                return len(priority)

        client_items = sorted(clients.items(), key=_client_sort_key)
        client_list = [c for _, c in client_items]

        try:
            from backend.services.exchange.cross_exchange_arb import CrossExchangeArbitrageEngine

            for client_a, client_b in combinations(client_list, 2):
                try:
                    engine = CrossExchangeArbitrageEngine(client_a, client_b)
                    spreads = run_async_safe(engine.scan_spreads(symbols), default=[])
                    entry_opps = engine.find_entry_opportunities(spreads)
                    for spread in entry_opps:
                        score = cross_exchange_score(spread.spread_pct, spread.z_score)
                        results.append({
                            "source": "cross_exchange",
                            "spread": spread,
                            "strategy": StrategyType.CROSS_EXCHANGE_SPREAD,
                            "symbol": spread.symbol,
                            "expected_annual_yield": score,
                            "sort_score": score,
                            "risk_score": 0.4,
                            "exchange_a": spread.exchange_a,
                            "exchange_b": spread.exchange_b,
                            "spread_pct": spread.spread_pct,
                            "z_score": spread.z_score,
                        })
                except Exception as e:
                    logger.debug("[ArbOrchestrator] 跨所对扫描异常: %s", e)
        except Exception as e:
            logger.debug(f"[ArbOrchestrator] 跨所扫描内部异常: {e}")

        return results

    def _scan_basis(
        self, symbols: List[str], snapshot: Any, exchange_manager: Any = None,
    ) -> List[Dict[str, Any]]:
        """真跨所基差扫描 — spot 所 vs perp 所（需 exchange_manager）"""
        from .yield_metrics import basis_convergence_score

        results = []
        threshold = getattr(self, "_basis_threshold", 0.003)

        if exchange_manager is None:
            return results

        try:
            from .async_bridge import run_async_safe
            from .basis_arb_executor import BasisSnapshot

            clients = exchange_manager.get_all_clients()
            if not isinstance(clients, dict):
                return results

            perp_exchanges = ("hyperliquid", "binance", "bybit", "okx")
            spot_exchanges = ("binance", "okx", "gateio")

            for symbol in symbols:
                spot_price = 0.0
                spot_ex = ""
                perp_price = 0.0
                perp_ex = ""

                for name, client in clients.items():
                    ex = getattr(getattr(client, "exchange_type", None), "value", name)
                    try:
                        book = run_async_safe(client.get_orderbook(symbol, depth=5), default={})
                        bids = book.get("bids", [])
                        asks = book.get("asks", [])
                        if not bids or not asks:
                            continue
                        price = (float(bids[0][0]) + float(asks[0][0])) / 2
                        if price <= 0:
                            continue
                        if ex in spot_exchanges and not spot_price:
                            spot_price = price
                            spot_ex = ex
                        if ex in perp_exchanges and not perp_price:
                            perp_price = price
                            perp_ex = ex
                    except Exception:
                        continue

                if spot_price <= 0 or perp_price <= 0 or spot_ex == perp_ex:
                    continue

                basis_pct = (perp_price - spot_price) / spot_price
                if abs(basis_pct) > threshold:
                    snap = BasisSnapshot(
                        symbol=symbol,
                        perp_price=perp_price,
                        spot_price=spot_price,
                        basis_pct=basis_pct * 100,
                    )
                    self._basis_executor.record_basis(snap)
                    score = basis_convergence_score(basis_pct)
                    results.append({
                        "source": "basis",
                        "symbol": symbol,
                        "strategy": StrategyType.SPOT_PERP_BASIS,
                        "expected_annual_yield": score,
                        "sort_score": score,
                        "risk_score": 0.3,
                        "basis_pct": basis_pct,
                        "perp_price": perp_price,
                        "spot_price": spot_price,
                        "exchange_long": spot_ex if basis_pct < 0 else perp_ex,
                        "exchange_short": perp_ex if basis_pct > 0 else spot_ex,
                    })
        except Exception as e:
            logger.debug(f"[ArbOrchestrator] 基差扫描内部异常: {e}")

        return results

    # ── Step 2: 风控检查 ─────────────────────────────

    def _risk_check(
        self,
        opportunities: List[Dict[str, Any]],
        snapshot: Any,
    ) -> List[Dict[str, Any]]:
        """对每个机会执行风控链"""
        passed = []
        account = self._build_account_snapshot(snapshot)
        existing = list(self._active_positions.values())

        for opp_data in opportunities:
            try:
                # 构建 V3 ArbitrageOpportunity（unified_models 版本）
                source = opp_data.get("source", "")
                v3_opp = self._build_v3_opportunity(opp_data)

                if v3_opp is None:
                    continue

                # 提议的仓位大小
                proposed_notional = self._capital_pool.available_usd * self.DEFAULT_POSITION_SIZE_PCT
                proposed_delta = 0.0  # 配对交易 delta 接近0

                # 获取资金费率历史
                funding_history = None
                if source == "funding_rate":
                    old_opp = opp_data.get("opportunity")
                    if old_opp and hasattr(old_opp, 'funding_snapshot'):
                        snapshot_data = old_opp.funding_snapshot
                        if snapshot_data and hasattr(snapshot_data, 'current_rate'):
                            funding_history = [snapshot_data.current_rate]

                # 执行风控链
                risk_result, risk_score = arbitrage_risk_chain.check_pre_trade(
                    pool=self._capital_pool,
                    account=account,
                    opportunity=v3_opp,
                    existing_positions=existing,
                    proposed_notional=proposed_notional,
                    proposed_delta=proposed_delta,
                    funding_history=funding_history,
                    is_cross_exchange=(source == "cross_exchange"),
                )

                if risk_result.passed:
                    opp_data["risk_score_obj"] = risk_score
                    opp_data["proposed_notional"] = proposed_notional
                    if risk_score:
                        opp_data["proposed_notional"] *= risk_score.size_multiplier
                    passed.append(opp_data)

            except Exception as e:
                logger.debug(f"[ArbOrchestrator] 风控检查异常 ({opp_data.get('symbol', '?')}): {e}")

        self._last_risk_results = passed

        if passed:
            logger.info(
                f"[ArbOrchestrator] 风控通过 {len(passed)}/{len(opportunities)} 个机会"
            )

        return passed

    # ── Step 3: 执行 ─────────────────────────────────

    def _execute(
        self,
        opportunities: List[Dict[str, Any]],
        snapshot: Any,
        exchange_manager: Any,
    ) -> int:
        """执行通过风控的机会

        单 tick 默认只执行 Top-1 机会：proposed_notional 是按扫描时点的可用资金
        计算的，同 tick 顺序开多个会导致规模假设失真（累计超出预期占用）。
        可用环境变量 ARB_V3_MAX_EXECUTIONS_PER_TICK 调整。
        """
        executed = 0
        try:
            import os

            max_per_tick = max(1, int(os.getenv("ARB_V3_MAX_EXECUTIONS_PER_TICK", "1")))
        except Exception:
            max_per_tick = 1

        for opp_data in opportunities:
            if executed >= max_per_tick:
                break
            source = opp_data.get("source", "")
            symbol = opp_data.get("symbol", "")
            notional = opp_data.get("proposed_notional", 0)

            if notional <= 0:
                continue

            pool = global_capital_coordinator.pool_for_strategy(source)
            cap_result = global_capital_coordinator.request(
                pool, notional, strategy_id=f"v3_{source}_{symbol}"
            )
            if not cap_result.get("granted"):
                logger.debug(
                    "[ArbOrchestrator] 全局资金池不足 %s/%s", pool, symbol
                )
                continue

            result = None
            if source == "funding_rate":
                result = self._execute_funding(opp_data, notional, snapshot)
            elif source == "cross_exchange":
                result = self._execute_cross_exchange(opp_data, notional, exchange_manager)
            elif source == "basis":
                result = self._execute_basis(opp_data, notional, snapshot)

            if result and result.get("ok"):
                executed += 1
                self._capital_pool.allocated_usd += notional
                self._capital_pool.available_usd -= notional
                logger.info(
                    f"[ArbOrchestrator] 执行成功: {source} {symbol} "
                    f"${notional:.0f} mode={self._mode.value}"
                )
            else:
                global_capital_coordinator.release(
                    pool, notional, strategy_id=f"v3_{source}_{symbol}"
                )

        return executed

    def _execute_funding(self, opp_data: Dict, notional: float, snapshot: Any) -> Dict:
        """执行资金费率套利"""
        old_opp = opp_data.get("opportunity")
        symbol = opp_data.get("symbol", "")

        if old_opp is None:
            return {"ok": False, "error": "no_opportunity_data"}

        direction = "short" if "short" in getattr(old_opp, 'strategy', '') else "long"
        entry_price = 0
        if hasattr(old_opp, 'funding_snapshot') and old_opp.funding_snapshot:
            # 从资金费率快照推算入场价（简化：使用市场价）
            pass

        # 获取当前价格
        markets = getattr(snapshot, 'markets', {})
        market = markets.get(symbol) if isinstance(markets, dict) else None
        entry_price = float(getattr(market, 'price', 0) or 0) if market else 0

        if entry_price <= 0:
            return {"ok": False, "error": "no_price_data"}

        if self._mode == ExecutionMode.PAPER:
            result = self._funding_executor.execute_opportunity(
                old_opp, size_usd=notional, entry_price=entry_price,
            )
            if result.success and result.position_id:
                hedge_ex = getattr(self, "_funding_hedge", "binance")
                primary_ex = getattr(self, "_funding_primary", "hyperliquid")
                self._register_position(
                    result.position_id, symbol, StrategyType.FUNDING_RATE,
                    notional, entry_price, direction,
                    exchange_long=primary_ex if direction != "short" else hedge_ex,
                    exchange_short=primary_ex if direction == "short" else hedge_ex,
                )
                return {"ok": True, "mode": "paper", "symbol": symbol, "position_id": result.position_id}
            return {"ok": False, "error": result.message}
        else:
            primary_ex = getattr(self, "_funding_primary", "hyperliquid")
            hedge_ex = getattr(self, "_funding_hedge", "binance")
            live_result = self._live_executor.execute_funding({
                "exchange": primary_ex,
                "primary_exchange": primary_ex,
                "hedge_exchange": hedge_ex,
                "symbol": symbol,
                "size_usd": notional,
                "direction": direction,
                "entry_price": entry_price,
            })
            if live_result.get("ok"):
                pos_id = live_result.get("position_id", f"live_fund_{symbol}_{int(time.time())}")
                self._register_position(
                    pos_id, symbol, StrategyType.FUNDING_RATE,
                    notional, entry_price, direction,
                    exchange_long=primary_ex if direction != "short" else hedge_ex,
                    exchange_short=primary_ex if direction == "short" else hedge_ex,
                )
            return live_result

    def _execute_cross_exchange(self, opp_data: Dict, notional: float,
                                 exchange_manager: Any) -> Dict:
        """执行跨交易所套利"""
        spread = opp_data.get("spread")
        if spread is None:
            return {"ok": False, "error": "no_spread_data"}

        direction_a = "sell" if spread.z_score > 0 else "buy"
        direction_b = "buy" if spread.z_score > 0 else "sell"

        if self._mode == ExecutionMode.PAPER:
            result = self._live_executor.execute_cross_exchange({
                "exchange_a": spread.exchange_a,
                "exchange_b": spread.exchange_b,
                "symbol": spread.symbol,
                "size_usd": notional,
                "price_a": spread.price_a,
                "price_b": spread.price_b,
                "direction_a": direction_a,
                "direction_b": direction_b,
            })
            if result.get("ok"):
                self._register_position(
                    result["position_id"], spread.symbol,
                    StrategyType.CROSS_EXCHANGE_SPREAD,
                    notional, (spread.price_a + spread.price_b) / 2,
                    "cross",
                    exchange_long=spread.exchange_a,
                    exchange_short=spread.exchange_b,
                    z_score=spread.z_score,
                    spread_pct=spread.spread_pct,
                )
            return result
        else:
            result = self._live_executor.execute_cross_exchange({
                "exchange_a": spread.exchange_a,
                "exchange_b": spread.exchange_b,
                "symbol": spread.symbol,
                "size_usd": notional,
                "price_a": spread.price_a,
                "price_b": spread.price_b,
                "direction_a": direction_a,
                "direction_b": direction_b,
            })
            if result.get("ok"):
                self._register_position(
                    result.get("position_id", f"live_cross_{spread.symbol}_{int(time.time())}"),
                    spread.symbol,
                    StrategyType.CROSS_EXCHANGE_SPREAD,
                    notional, (spread.price_a + spread.price_b) / 2,
                    "cross",
                    exchange_long=spread.exchange_a if direction_a == "buy" else spread.exchange_b,
                    exchange_short=spread.exchange_b if direction_a == "buy" else spread.exchange_a,
                    z_score=spread.z_score,
                    spread_pct=spread.spread_pct,
                )
            return result

    def _execute_basis(self, opp_data: Dict, notional: float, snapshot: Any) -> Dict:
        """执行基差套利"""
        symbol = opp_data.get("symbol", "")
        basis_pct = opp_data.get("basis_pct", 0)
        perp_price = opp_data.get("perp_price", 0)
        spot_price = opp_data.get("spot_price", 0)

        if self._mode == ExecutionMode.PAPER:
            result = self._live_executor.execute_basis({
                "exchange": "hyperliquid",
                "symbol": symbol,
                "size_usd": notional,
                "basis_pct": basis_pct,
                "perp_price": perp_price,
                "spot_price": spot_price,
            })
            if result.get("ok"):
                self._register_position(
                    result["position_id"], symbol,
                    StrategyType.SPOT_PERP_BASIS,
                    notional, perp_price or spot_price,
                    "basis",
                    basis_pct=basis_pct,
                )
            return result
        else:
            result = self._live_executor.execute_basis({
                "exchange": opp_data.get("exchange_long", "hyperliquid"),
                "exchange_spot": opp_data.get("exchange_long", "binance"),
                "exchange_perp": opp_data.get("exchange_short", "hyperliquid"),
                "symbol": symbol,
                "size_usd": notional,
                "basis_pct": basis_pct,
                "perp_price": perp_price,
                "spot_price": spot_price,
            })
            if result.get("ok"):
                self._register_position(
                    result.get("position_id", f"live_basis_{symbol}_{int(time.time())}"),
                    symbol,
                    StrategyType.SPOT_PERP_BASIS,
                    notional, perp_price or spot_price,
                    "basis",
                    exchange_long=opp_data.get("exchange_long", ""),
                    exchange_short=opp_data.get("exchange_short", ""),
                    basis_pct=basis_pct,
                )
            return result

    # ── Step 4: 监控 ─────────────────────────────────

    def _monitor(self, snapshot: Any) -> List[Dict[str, Any]]:
        """监控所有活跃仓位"""
        if not self._active_positions:
            return []

        positions = list(self._active_positions.values())

        # Paper 模式：资金费率结算 + 平仓条件检查
        if self._mode == ExecutionMode.PAPER:
            try:
                from .models import FundingRateSnapshot
                fr_snaps = {}
                raw_rates = self._extract_funding_rates(snapshot)
                for sym, rate in raw_rates.items():
                    fr_snaps[sym] = FundingRateSnapshot(
                        symbol=sym, current_rate=rate, predicted_rate=rate,
                        rate_8h_avg=rate, rate_24h_avg=rate,
                        annual_yield=abs(rate) * 3 * 365,
                        oi_total=0, volume_24h=0,
                    )
                self._funding_executor.settle_funding(fr_snaps)
                self._funding_executor.check_close_conditions(fr_snaps)
                for pos_id, reason in self._funding_executor.get_closed_position_ids():
                    if pos_id in self._active_positions:
                        self._close_position(pos_id, reason)
                        self._funding_executor.remove_position(pos_id)
            except Exception as e:
                logger.debug("[ArbOrchestrator] Paper funding 结算异常: %s", e)

        # 收集当前价格和费率
        current_prices = self._extract_prices(snapshot)
        current_funding = self._extract_funding_rates(snapshot)

        metrics_list, actions = metrics_tracker.monitor_all(
            positions, current_prices, current_funding
        )

        self._last_metrics = metrics_list

        # 处理动作
        for action in actions:
            pos_id = action.get("position_id", "")
            action_type = action.get("type", "")

            if action_type in ("close", "force_close"):
                self._close_position(pos_id, action.get("reason", action_type))
            elif action_type == "rebalance":
                logger.info(f"[ArbOrchestrator] 需要再平衡: {pos_id} ({action.get('reason')})")
            elif action_type == "alert":
                logger.warning(f"[ArbOrchestrator] 仓位告警: {pos_id} ({action.get('reason')})")

        # 熔断器检查
        try:
            emergency_handler.check_circuit_breaker(
                pool=self._capital_pool,
                positions=positions,
            )
        except Exception as e:
            logger.debug(f"[ArbOrchestrator] 熔断器检查异常: {e}")

        return actions

    # ── 辅助方法 ─────────────────────────────────────

    def _init_capital_pool(self, snapshot: Any):
        """从 snapshot 初始化资金池（同步全局协调器）"""
        equity = 0
        if snapshot:
            equity = getattr(snapshot, 'total_equity', 0)
            if not equity:
                equity = getattr(snapshot, 'account_equity', 0) or 0

        if equity > 0:
            global_capital_coordinator.update_equity(float(equity))
            v3_available = global_capital_coordinator.get_v3_pool_available()
            allocated = self._capital_pool.allocated_usd
            self._capital_pool.total_pool_usd = v3_available + allocated
            self._capital_pool.available_usd = max(0, v3_available)

    def _build_account_snapshot(self, snapshot: Any) -> ArbAccountSnapshot:
        """构建账户快照"""
        equity = 0
        available = 0
        if snapshot:
            equity = float(getattr(snapshot, 'total_equity', 0) or 0)
            if not equity:
                equity = float(getattr(snapshot, 'account_equity', 0) or 0)
            available = float(getattr(snapshot, 'available_balance', 0) or 0)

        return ArbAccountSnapshot(
            total_equity=equity,
            available_balance=available,
            frozen_margin=float(self._capital_pool.allocated_usd),
            arbitrage_pool_balance=float(self._capital_pool.total_pool_usd),
        )

    def _build_v3_opportunity(self, opp_data: Dict) -> Optional[ArbitrageOpportunity]:
        """从扫描结果构建 V3 ArbitrageOpportunity"""
        try:
            return ArbitrageOpportunity(
                opportunity_id=f"v3_{opp_data.get('symbol', '')}_{int(time.time())}",
                symbol=opp_data.get("symbol", ""),
                strategy=opp_data.get("source", "funding_rate"),
                expected_annual_yield=opp_data.get("expected_annual_yield", 0),
                risk_score=opp_data.get("risk_score", 0.5),
                confidence=max(0, 1.0 - opp_data.get("risk_score", 0.5)),
                timestamp=time.time(),
                exchange_a=opp_data.get("exchange_a", ""),
                exchange_b=opp_data.get("exchange_b", ""),
                spread_pct=opp_data.get("spread_pct", 0) or getattr(opp_data.get("spread"), "spread_pct", 0),
                z_score=opp_data.get("z_score", 0) or getattr(opp_data.get("spread"), "z_score", 0),
            )
        except Exception as e:
            logger.debug(f"[ArbOrchestrator] 构建 V3 机会失败: {e}")
            return None

    def _register_position(
        self,
        position_id: str,
        symbol: str,
        strategy: StrategyType,
        size_usd: float,
        entry_price: float,
        direction: str,
        exchange_long: str = "",
        exchange_short: str = "",
        z_score: float = 0.0,
        spread_pct: float = 0.0,
        basis_pct: float = 0.0,
    ):
        """注册新仓位到活跃列表并写入 DB"""
        size = size_usd / entry_price if entry_price > 0 else 0

        is_short = direction in ("short", "funding_short")
        is_long = direction in ("long", "funding_long")
        is_cross = direction == "cross"
        is_basis = direction == "basis"

        if is_cross:
            long_sz, short_sz = size, size
        elif is_basis:
            long_sz = size if basis_pct < 0 else 0
            short_sz = size if basis_pct > 0 else size
        elif is_short:
            long_sz, short_sz = 0, size
        elif is_long:
            long_sz, short_sz = size, 0
        else:
            long_sz, short_sz = size, 0

        pos = ArbHedgePosition(
            position_id=position_id,
            symbol=symbol,
            strategy=strategy,
            long_size=long_sz,
            long_entry_price=entry_price if long_sz > 0 else 0,
            short_size=short_sz,
            short_entry_price=entry_price if short_sz > 0 else 0,
            delta=0.0,
            entry_time=time.time(),
            status=ArbitrageStatus.ACTIVE,
            exchange_long=exchange_long,
            exchange_short=exchange_short,
            entry_z_score=z_score,
            entry_spread_pct=spread_pct,
            entry_basis_pct=basis_pct,
        )

        self._active_positions[position_id] = pos
        self._position_notional[position_id] = size_usd
        save_position_open(pos, size_usd, self._mode)
        logger.info(f"[ArbOrchestrator] 注册仓位: {position_id} {symbol} ${size_usd:.0f}")

    def _close_position(self, position_id: str, reason: str):
        """关闭仓位"""
        pos = self._active_positions.pop(position_id, None)
        if pos is None:
            return

        notional = self._position_notional.pop(position_id, pos.notional)

        pool = global_capital_coordinator.pool_for_strategy(pos.strategy.value)
        global_capital_coordinator.release(pool, notional, strategy_id=position_id)

        self._capital_pool.allocated_usd = max(0, self._capital_pool.allocated_usd - notional)
        self._capital_pool.available_usd += notional

        if self._mode == ExecutionMode.LIVE:
            self._live_executor.close_position(
                position_id, reason,
                position_data={
                    "symbol": pos.symbol,
                    "long_size": pos.long_size,
                    "short_size": pos.short_size,
                    "exchange_long": pos.exchange_long,
                    "exchange_short": pos.exchange_short,
                }
            )

        save_position_close(position_id, reason)
        logger.info(f"[ArbOrchestrator] 平仓: {position_id} reason={reason}")

    def _extract_prices(self, snapshot: Any) -> Dict[str, float]:
        """从 snapshot 提取当前价格"""
        prices = {}
        if snapshot is None:
            return prices
        markets = getattr(snapshot, 'markets', {})
        if isinstance(markets, dict):
            for sym, m in markets.items():
                p = float(getattr(m, 'price', 0) or 0)
                if p > 0:
                    prices[sym] = p
        return prices

    def _extract_funding_rates(self, snapshot: Any) -> Dict[str, float]:
        """从 snapshot 提取资金费率"""
        rates = {}
        if snapshot is None:
            return rates
        deriv = getattr(snapshot, 'derivatives_snapshot', {})
        if isinstance(deriv, dict):
            for sym, d in deriv.items():
                if isinstance(d, dict):
                    r = float(d.get('funding_rate', 0) or 0)
                    if r != 0:
                        rates[sym] = r
        return rates


# ── 模块级单例 ──
arbitrage_orchestrator = ArbitrageOrchestrator()
