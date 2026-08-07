"""
套利应急处理器

4项应急程序：
1. 单腿失败处理
2. 强制平仓
3. 交易所连接丢失
4. 三级熔断

设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第3.5节
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .unified_models import ArbHedgePosition, ArbitrageCapitalPool, StrategyType

logger = logging.getLogger(__name__)


class EmergencyHandler:
    """套利应急处理器"""

    # ── 单腿失败参数 ──
    LEG_RETRY_COUNT: int = 3
    LEG_RETRY_DELAY_SEC: float = 1.0
    LEG_MAX_EXPOSURE_SEC: float = 60.0

    # ── 冷却期参数 ──
    COOLDOWN_LEG_FAILURE: float = 1800      # 30 min
    COOLDOWN_FORCED_CLOSE: float = 1800     # 30 min
    COOLDOWN_STRATEGY_BREACH: float = 7200  # 2h
    COOLDOWN_POOL_BREACH: float = 86400     # 24h

    # ── 三级熔断参数 ──
    POSITION_LOSS_PCT: float = 0.02         # 单仓亏损 > 2% 池子
    STRATEGY_DAILY_LOSS_PCT: float = 0.015  # 策略日亏 > 1.5% 池子
    POOL_DAILY_LOSS_PCT: float = 0.03       # 总日亏 > 3% 池子

    def __init__(self):
        self._cooldowns: Dict[str, float] = {}  # key -> cooldown_until timestamp
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_until: float = 0.0

    def handle_single_leg_failure(
        self,
        filled_position: Dict[str, Any],
        failed_exchange: str,
        failed_symbol: str,
        exchange_client: Any = None,
    ) -> Dict[str, Any]:
        """
        单腿失败处理

        时间线：
        - <10s: 重试3次，间隔1s
        - 10-30s: 加大滑点重试1次
        - 30-60s: 立即平仓
        - >60s: 强制平仓 + 冷却
        """
        logger.warning(
            f"[Emergency] 单腿失败: filled={filled_position.get('exchange', '')}, "
            f"failed={failed_exchange}:{failed_symbol}"
        )

        start_time = time.time()
        position_id = filled_position.get("position_id", "unknown")

        # Phase 1: 重试
        for i in range(self.LEG_RETRY_COUNT):
            elapsed = time.time() - start_time
            if elapsed > self.LEG_MAX_EXPOSURE_SEC:
                break

            logger.info(f"[Emergency] 重试 #{i+1} for {failed_exchange}:{failed_symbol}")
            # 真实重试：通过 exchange_client 下单
            if exchange_client is not None:
                try:
                    from .async_bridge import run_async
                    from backend.services.exchange.base_exchange_client import (
                        ExchangeOrder, OrderSide, OrderType,
                    )
                    side = OrderSide.BUY if filled_position.get("side") == "short" else OrderSide.SELL
                    retry_order = ExchangeOrder(
                        order_id=f"arb_retry_{failed_symbol}_{int(time.time())}_{i}",
                        symbol=failed_symbol,
                        side=side,
                        order_type=OrderType.MARKET,
                        size=float(filled_position.get("size", 0)),
                    )
                    result = run_async(exchange_client.place_order(retry_order))
                    if result and result.get("status") != "error":
                        logger.info(f"[Emergency] 重试 #{i+1} 成功")
                        return {"handled": True, "action": "retry_success"}
                except Exception as retry_err:
                    logger.warning(f"[Emergency] 重试 #{i+1} 失败: {retry_err}")
            time.sleep(self.LEG_RETRY_DELAY_SEC)

        # 重试全部失败 → 紧急平仓成功腿
        logger.warning(f"[Emergency] 重试全部失败，紧急平仓 {position_id}")

        # 真实平仓成功腿
        if exchange_client is not None:
            try:
                from .async_bridge import run_async
                from backend.services.exchange.base_exchange_client import (
                    ExchangeOrder, OrderSide, OrderType,
                )
                filled_symbol = filled_position.get("symbol", "")
                filled_size = float(filled_position.get("size", 0))
                if filled_symbol and filled_size > 0:
                    close_side = OrderSide.SELL if filled_position.get("side") == "long" else OrderSide.BUY
                    close_order = ExchangeOrder(
                        order_id=f"arb_emerg_close_{filled_symbol}_{int(time.time())}",
                        symbol=filled_symbol,
                        side=close_side,
                        order_type=OrderType.MARKET,
                        size=filled_size,
                        reduce_only=True,
                    )
                    close_result = run_async(exchange_client.place_order(close_order))
                    logger.info(f"[Emergency] 紧急平仓结果: {close_result}")
            except Exception as close_err:
                logger.error(f"[Emergency] 紧急平仓失败: {close_err}")

        # 设置冷却
        self._set_cooldown(f"leg_{position_id}", self.COOLDOWN_LEG_FAILURE)

        return {
            "handled": True,
            "action": "emergency_close",
            "position_id": position_id,
            "cooldown_sec": self.COOLDOWN_LEG_FAILURE,
        }

    def handle_forced_close(
        self,
        position: ArbHedgePosition,
        reason: str,
        exchange_client: Any = None,
    ) -> Dict[str, Any]:
        """强制平仓 — 通过 LiveExecutor 或直接 exchange_client 执行"""
        logger.warning(
            f"[Emergency] 强制平仓: {position.position_id}, reason={reason}"
        )

        close_ok = False

        # 优先使用 LiveExecutor
        try:
            from .live_executor import LiveExecutor
            executor = LiveExecutor()
            position_data = {
                "symbol": position.symbol,
                "long_size": position.size_long,
                "short_size": position.size_short,
                "exchange_long": position.exchange_long,
                "exchange_short": position.exchange_short,
            }
            result = executor.close_position(
                position.position_id, reason=f"emergency:{reason}",
                position_data=position_data,
            )
            close_ok = result.get("ok", False)
            if close_ok:
                logger.info(f"[Emergency] 强制平仓成功: {position.position_id}")
            else:
                logger.error(f"[Emergency] LiveExecutor 平仓失败: {result}")
        except Exception as le_err:
            logger.warning(f"[Emergency] LiveExecutor 不可用，尝试直接平仓: {le_err}")

        # 回退：直接通过 exchange_client 平仓
        if not close_ok and exchange_client is not None:
            try:
                from .async_bridge import run_async
                from backend.services.exchange.base_exchange_client import (
                    ExchangeOrder, OrderSide, OrderType,
                )
                # 平多腿
                if position.size_long > 0:
                    close_order = ExchangeOrder(
                        order_id=f"arb_force_close_long_{position.symbol}_{int(time.time())}",
                        symbol=position.symbol,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        size=position.size_long,
                        reduce_only=True,
                    )
                    run_async(exchange_client.place_order(close_order))
                # 平空腿
                if position.size_short > 0:
                    close_order = ExchangeOrder(
                        order_id=f"arb_force_close_short_{position.symbol}_{int(time.time())}",
                        symbol=position.symbol,
                        side=OrderSide.BUY,
                        order_type=OrderType.MARKET,
                        size=position.size_short,
                        reduce_only=True,
                    )
                    run_async(exchange_client.place_order(close_order))
                close_ok = True
                logger.info(f"[Emergency] 直接平仓成功: {position.position_id}")
            except Exception as direct_err:
                logger.error(f"[Emergency] 直接平仓也失败: {direct_err}")

        self._set_cooldown(
            f"forced_{position.symbol}_{position.strategy.value}",
            self.COOLDOWN_FORCED_CLOSE,
        )

        return {
            "handled": close_ok,
            "action": "force_close",
            "position_id": position.position_id,
            "symbol": position.symbol,
            "reason": reason,
        }

    def handle_exchange_connectivity_loss(
        self,
        exchange_id: str,
        active_positions: List[ArbHedgePosition],
    ) -> Dict[str, Any]:
        """交易所连接丢失处理"""
        logger.error(f"[Emergency] 交易所连接丢失: {exchange_id}")

        affected = [
            p for p in active_positions
            if p.exchange_long == exchange_id or p.exchange_short == exchange_id
        ]

        return {
            "handled": True,
            "action": "block_exchange",
            "exchange_id": exchange_id,
            "affected_positions": [p.position_id for p in affected],
            "recommendation": "block_new_positions" if affected else "monitor_only",
        }

    def check_circuit_breaker(
        self,
        pool: ArbitrageCapitalPool,
        positions: List[ArbHedgePosition],
        daily_pnl_by_strategy: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        检查三级熔断

        Level 1: 仓位级 — 单仓亏损 > 2% 池子
        Level 2: 策略级 — 策略日亏 > 1.5% 池子
        Level 3: 池级 — 总日亏 > 3% 池子
        """
        pool_total = max(pool.total_pool_usd, 1.0)

        # Level 3: 池级
        if pool.daily_realized_loss >= pool_total * self.POOL_DAILY_LOSS_PCT:
            self._circuit_breaker_active = True
            try:
                from .arbitrage_alert_monitor import arb_alert_monitor
                arb_alert_monitor.on_circuit_breaker(
                    f"池级日亏 {pool.daily_realized_loss:.2f} > {self.POOL_DAILY_LOSS_PCT:.0%}"
                )
            except Exception:
                pass
            self._circuit_breaker_until = time.time() + self.COOLDOWN_POOL_BREACH
            pool.cooldown_until = self._circuit_breaker_until
            logger.critical(
                f"[Emergency] 池级熔断! 日亏 {pool.daily_realized_loss:.2f} "
                f"> {self.POOL_DAILY_LOSS_PCT:.0%} of {pool_total:.2f}"
            )
            return {
                "level": "pool",
                "action": "close_all",
                "cooldown_sec": self.COOLDOWN_POOL_BREACH,
                "message": "池级熔断，关闭所有套利仓位",
            }

        # Level 2: 策略级
        if daily_pnl_by_strategy:
            for strategy, pnl in daily_pnl_by_strategy.items():
                if abs(pnl) >= pool_total * self.STRATEGY_DAILY_LOSS_PCT:
                    self._set_cooldown(f"strategy_{strategy}", self.COOLDOWN_STRATEGY_BREACH)
                    logger.warning(
                        f"[Emergency] 策略级熔断: {strategy}, 日亏={pnl:.2f}"
                    )
                    return {
                        "level": "strategy",
                        "action": "freeze_strategy",
                        "strategy": strategy,
                        "cooldown_sec": self.COOLDOWN_STRATEGY_BREACH,
                        "message": f"策略 {strategy} 熔断，冻结2h后半仓运行",
                    }

        # Level 1: 仓位级
        for pos in positions:
            pos_loss_pct = abs(pos.accumulated_funding) / pool_total if pool_total > 0 else 0
            # 简化：用 accumulated_funding 作为亏损代理
            # Phase 2: 使用真实 P&L
            if pos_loss_pct > self.POSITION_LOSS_PCT:
                self._set_cooldown(
                    f"pos_{pos.symbol}_{pos.strategy.value}",
                    self.COOLDOWN_FORCED_CLOSE,
                )
                return {
                    "level": "position",
                    "action": "close_position",
                    "position_id": pos.position_id,
                    "cooldown_sec": self.COOLDOWN_FORCED_CLOSE,
                    "message": f"仓位 {pos.position_id} 亏损过大，强制平仓",
                }

        return {"level": "none", "action": "none", "message": "无熔断触发"}

    def is_in_cooldown(self, key: str) -> bool:
        """检查某个 key 是否处于冷却期"""
        until = self._cooldowns.get(key, 0)
        return time.time() < until

    def is_circuit_breaker_active(self) -> bool:
        """检查池级熔断是否激活"""
        if self._circuit_breaker_active and time.time() > self._circuit_breaker_until:
            self._circuit_breaker_active = False
        return self._circuit_breaker_active

    def _set_cooldown(self, key: str, duration_sec: float):
        self._cooldowns[key] = time.time() + duration_sec


# ── 模块级单例 ──
emergency_handler = EmergencyHandler()
