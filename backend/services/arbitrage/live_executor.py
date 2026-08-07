"""
Live Executor — 真实下单桥接层

包装 BaseExchangeClient.place_order() 实现真实交易执行。
通过 ExchangeManager 获取交易所客户端，执行配对下单。
处理单腿失败场景（LegRiskManager）。

支持 Paper 和 Live 双模式：
- Paper 模式：模拟下单，不调用真实交易所 API
- Live 模式：通过 async_bridge 调用异步交易所 API
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.services.exchange.base_exchange_client import (
    ExchangeOrder,
    OrderSide,
    OrderType,
)

logger = logging.getLogger(__name__)


class LiveExecutor:
    """套利执行器 — 支持 Paper/Live 双模式"""

    def __init__(self, mode: str = "paper"):
        self._exchange_manager = None
        self._mode = mode  # "paper" or "live"
        self._paper_positions: Dict[str, Dict[str, Any]] = {}

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str):
        if value not in ("paper", "live"):
            raise ValueError(f"Invalid mode: {value}, must be 'paper' or 'live'")
        self._mode = value

    def _get_exchange_manager(self):
        """延迟加载 ExchangeManager"""
        if self._exchange_manager is None:
            try:
                from backend.services.exchange.exchange_manager import get_exchange_manager
                self._exchange_manager = get_exchange_manager()
            except Exception as e:
                logger.error(f"[LiveExecutor] 无法加载 ExchangeManager: {e}")
                return None
        return self._exchange_manager

    def execute_funding(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行资金费率套利（delta-neutral）

        - 主腿：primary_exchange 上按 direction 开收 funding 方向
        - 对冲腿：hedge_exchange 上开反向仓位
        - 禁止在同一所同一 symbol 同时开多空（会抵消 funding）
        """
        primary_exchange = payload.get("exchange", payload.get("primary_exchange", "hyperliquid"))
        hedge_exchange = payload.get("hedge_exchange", "binance")
        symbol = payload.get("symbol", "")
        size_usd = payload.get("size_usd", 0)
        direction = payload.get("direction", "short")  # short=收正 funding, long=收负 funding
        entry_price = payload.get("entry_price", 0)

        if not symbol or size_usd <= 0 or entry_price <= 0:
            return {"ok": False, "error": "invalid_parameters"}

        if self._mode == "paper":
            return self._paper_execute_funding(
                primary_exchange, symbol, size_usd, direction, entry_price,
                hedge_exchange=hedge_exchange,
            )

        mgr = self._get_exchange_manager()
        if mgr is None:
            return {"ok": False, "error": "exchange_manager_unavailable"}

        try:
            primary_client = mgr.get_client(primary_exchange)
            if primary_client is None:
                return {"ok": False, "error": f"no_client_for_{primary_exchange}"}

            size = max(size_usd / entry_price, 0.001)
            from .async_bridge import run_async

            # 主腿：收 funding 方向
            primary_side = OrderSide.SELL if direction == "short" else OrderSide.BUY
            primary_order = ExchangeOrder(
                order_id=f"arb_primary_{symbol}_{int(time.time())}",
                symbol=symbol,
                side=primary_side,
                order_type=OrderType.MARKET,
                size=size,
            )
            primary_result = run_async(primary_client.place_order(primary_order))
            if primary_result.get("status") == "error":
                try:
                    from backend.services.arbitrage.arbitrage_alert_monitor import arb_alert_monitor
                    arb_alert_monitor.on_leg_failure(
                        symbol, primary_exchange,
                        str(primary_result.get("message", "unknown")),
                        leg="primary",
                    )
                except Exception:
                    pass
                return {
                    "ok": False,
                    "error": f"primary_leg_failed: {primary_result.get('message')}",
                }

            # 对冲腿：跨所反向（同所同 symbol 会抵消 funding）
            hedge_side = OrderSide.BUY if direction == "short" else OrderSide.SELL
            hedge_result = {"status": "skipped", "message": "no_hedge_exchange"}
            hedge_ex = hedge_exchange

            if hedge_exchange and hedge_exchange != primary_exchange:
                hedge_client = mgr.get_client(hedge_exchange)
                if hedge_client is None:
                    self._emergency_close_leg(
                        primary_client, symbol, size, primary_side, run_async
                    )
                    return {"ok": False, "error": f"no_client_for_{hedge_exchange}"}

                hedge_order = ExchangeOrder(
                    order_id=f"arb_hedge_{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side=hedge_side,
                    order_type=OrderType.MARKET,
                    size=size,
                )
                hedge_result = run_async(hedge_client.place_order(hedge_order))
                if hedge_result.get("status") == "error":
                    logger.error(
                        "[LiveExecutor] 对冲腿失败，紧急平仓主腿: %s", hedge_result
                    )
                    try:
                        from backend.services.arbitrage.arbitrage_alert_monitor import arb_alert_monitor
                        arb_alert_monitor.on_leg_failure(
                            symbol, hedge_exchange,
                            str(hedge_result.get("message", "unknown")),
                            leg="hedge",
                        )
                    except Exception:
                        pass
                    self._emergency_close_leg(
                        primary_client, symbol, size, primary_side, run_async
                    )
                    return {"ok": False, "error": "hedge_leg_failed_emergency_closed_primary"}
            else:
                hedge_ex = ""
                logger.warning(
                    "[LiveExecutor] 无跨所对冲，%s 单腿方向性持仓 direction=%s",
                    primary_exchange, direction,
                )

            position_id = f"live_fund_{symbol}_{int(time.time())}"
            logger.info("[LiveExecutor] Funding arb LIVE executed: %s", position_id)

            return {
                "ok": True,
                "position_id": position_id,
                "mode": "live",
                "exchange": primary_exchange,
                "hedge_exchange": hedge_ex,
                "symbol": symbol,
                "size_usd": size_usd,
                "direction": direction,
                "primary_result": primary_result,
                "hedge_result": hedge_result,
            }

        except Exception as e:
            logger.error("[LiveExecutor] Funding execution failed: %s", e)
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _emergency_close_leg(client, symbol: str, size: float, opened_side: OrderSide, run_async):
        close_side = OrderSide.BUY if opened_side == OrderSide.SELL else OrderSide.SELL
        close_order = ExchangeOrder(
            order_id=f"arb_emergency_close_{symbol}_{int(time.time())}",
            symbol=symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            size=size,
            reduce_only=True,
        )
        run_async(client.place_order(close_order))

    def execute_cross_exchange(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行跨交易所套利

        在两个交易所同时开配对仓位，赚取价差收敛利润。
        """
        exchange_a = payload.get("exchange_a", "")
        exchange_b = payload.get("exchange_b", "")
        symbol = payload.get("symbol", "")
        size_usd = payload.get("size_usd", 0)
        price_a = payload.get("price_a", 0)
        price_b = payload.get("price_b", 0)
        direction_a = payload.get("direction_a", "sell")  # z_score > 0: A贵卖A
        direction_b = payload.get("direction_b", "buy")

        if not all([exchange_a, exchange_b, symbol]) or size_usd <= 0:
            return {"ok": False, "error": "invalid_parameters"}

        if self._mode == "paper":
            return self._paper_execute_cross_exchange(
                exchange_a, exchange_b, symbol, size_usd,
                price_a or 1, price_b or 1,
                direction_a, direction_b,
            )

        # Live 模式
        mgr = self._get_exchange_manager()
        if mgr is None:
            return {"ok": False, "error": "exchange_manager_unavailable"}

        try:
            client_a = mgr.get_client(exchange_a)
            client_b = mgr.get_client(exchange_b)
            if client_a is None or client_b is None:
                return {"ok": False, "error": "client_unavailable"}

            ref_price = price_a or price_b or 1
            size = size_usd / ref_price
            size = max(size, 0.001)

            from .async_bridge import run_async

            side_a = OrderSide.SELL if direction_a == "sell" else OrderSide.BUY
            side_b = OrderSide.SELL if direction_b == "sell" else OrderSide.BUY

            order_a = ExchangeOrder(
                order_id=f"arb_xa_{symbol}_{int(time.time())}",
                symbol=symbol,
                side=side_a,
                order_type=OrderType.MARKET,
                size=size,
            )
            order_b = ExchangeOrder(
                order_id=f"arb_xb_{symbol}_{int(time.time())}",
                symbol=symbol,
                side=side_b,
                order_type=OrderType.MARKET,
                size=size,
            )

            # 同时下单
            result_a = run_async(client_a.place_order(order_a))
            result_b = run_async(client_b.place_order(order_b))

            # 处理单腿失败
            a_ok = result_a.get("status") != "error"
            b_ok = result_b.get("status") != "error"

            if not a_ok and not b_ok:
                return {"ok": False, "error": "both_legs_failed"}

            if not a_ok:
                # A 失败，紧急平仓 B
                logger.error(f"[LiveExecutor] A腿失败，紧急平仓B: {result_a}")
                close_b = ExchangeOrder(
                    order_id=f"arb_close_xb_{int(time.time())}",
                    symbol=symbol,
                    side=OrderSide.SELL if side_b == OrderSide.BUY else OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    size=size,
                    reduce_only=True,
                )
                run_async(client_b.place_order(close_b))
                return {"ok": False, "error": "leg_a_failed_emergency_closed_b"}

            if not b_ok:
                # B 失败，紧急平仓 A
                logger.error(f"[LiveExecutor] B腿失败，紧急平仓A: {result_b}")
                close_a = ExchangeOrder(
                    order_id=f"arb_close_xa_{int(time.time())}",
                    symbol=symbol,
                    side=OrderSide.SELL if side_a == OrderSide.BUY else OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    size=size,
                    reduce_only=True,
                )
                run_async(client_a.place_order(close_a))
                return {"ok": False, "error": "leg_b_failed_emergency_closed_a"}

            position_id = f"live_cross_{symbol}_{int(time.time())}"
            logger.info(f"[LiveExecutor] Cross-exchange arb LIVE executed: {position_id}")

            return {
                "ok": True,
                "position_id": position_id,
                "mode": "live",
                "exchange_a": exchange_a,
                "exchange_b": exchange_b,
                "symbol": symbol,
                "size_usd": size_usd,
                "result_a": result_a,
                "result_b": result_b,
            }

        except Exception as e:
            logger.error(f"[LiveExecutor] Cross-exchange execution failed: {e}")
            return {"ok": False, "error": str(e)}

    def execute_basis(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """执行期现基差套利"""
        exchange = payload.get("exchange", "hyperliquid")
        symbol = payload.get("symbol", "")
        size_usd = payload.get("size_usd", 0)
        basis_pct = payload.get("basis_pct", 0)
        perp_price = payload.get("perp_price", 0)
        spot_price = payload.get("spot_price", 0)

        if not symbol or size_usd <= 0:
            return {"ok": False, "error": "invalid_parameters"}

        if self._mode == "paper":
            return self._paper_execute_basis(exchange, symbol, size_usd, basis_pct, perp_price, spot_price)

        # Live 模式: 买入低价资产，卖出高价资产
        mgr = self._get_exchange_manager()
        if mgr is None:
            return {"ok": False, "error": "exchange_manager_unavailable"}

        try:
            client = mgr.get_client(exchange)
            if client is None:
                return {"ok": False, "error": f"no_client_for_{exchange}"}

            from .async_bridge import run_async

            ref_price = perp_price or spot_price or 1
            size = size_usd / ref_price
            size = max(size, 0.001)

            if basis_pct > 0:
                # 基差为正: perp贵，做空perp做多spot
                short_order = ExchangeOrder(
                    order_id=f"arb_basis_short_{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    size=size,
                )
                long_order = ExchangeOrder(
                    order_id=f"arb_basis_long_{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    size=size,
                )
            else:
                short_order = ExchangeOrder(
                    order_id=f"arb_basis_short_{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    size=size,
                )
                long_order = ExchangeOrder(
                    order_id=f"arb_basis_long_{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    size=size,
                )

            result_short = run_async(client.place_order(short_order))
            result_long = run_async(client.place_order(long_order))

            position_id = f"live_basis_{symbol}_{int(time.time())}"
            logger.info(f"[LiveExecutor] Basis arb LIVE executed: {position_id}")

            return {
                "ok": True,
                "position_id": position_id,
                "mode": "live",
                "exchange": exchange,
                "symbol": symbol,
                "size_usd": size_usd,
                "basis_pct": basis_pct,
            }

        except Exception as e:
            logger.error(f"[LiveExecutor] Basis execution failed: {e}")
            return {"ok": False, "error": str(e)}

    def close_position(self, position_id: str, reason: str = "manual",
                       position_data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        关闭套利仓位

        Args:
            position_id: 仓位ID
            reason: 平仓原因
            position_data: 仓位信息（包含 exchange, symbol, size 等）
        """
        if self._mode == "paper":
            return self._paper_close_position(position_id, reason)

        if not position_data:
            return {"ok": False, "error": "no_position_data"}

        mgr = self._get_exchange_manager()
        if mgr is None:
            return {"ok": False, "error": "exchange_manager_unavailable"}

        try:
            from .async_bridge import run_async

            symbol = position_data.get("symbol", "")
            long_size = float(position_data.get("long_size", 0) or 0)
            short_size = float(position_data.get("short_size", 0) or 0)
            exchange_long = position_data.get("exchange_long") or ""
            exchange_short = position_data.get("exchange_short") or ""

            results = []
            legs = []
            if exchange_long and long_size > 0:
                legs.append((exchange_long, long_size, OrderSide.SELL))
            if exchange_short and short_size > 0:
                legs.append((exchange_short, short_size, OrderSide.BUY))
            if not legs:
                size = long_size or short_size
                ex = position_data.get("exchange", "hyperliquid")
                if size > 0:
                    legs.append((ex, size, OrderSide.BUY))

            for ex, size, close_side in legs:
                client = mgr.get_client(ex)
                if client is None:
                    results.append({"exchange": ex, "status": "error", "message": "no_client"})
                    continue

                close_order = ExchangeOrder(
                    order_id=f"arb_close_{symbol}_{ex}_{int(time.time())}",
                    symbol=symbol,
                    side=close_side,
                    order_type=OrderType.MARKET,
                    size=size,
                    reduce_only=True,
                )
                result = run_async(client.place_order(close_order))
                results.append({"exchange": ex, "result": result})

            logger.info(f"[LiveExecutor] LIVE closed position: {position_id}, reason: {reason}")

            return {
                "ok": True,
                "position_id": position_id,
                "closed": True,
                "reason": reason,
                "results": results,
            }

        except Exception as e:
            logger.error(f"[LiveExecutor] Close position failed: {e}")
            return {"ok": False, "error": str(e)}

    # ── Paper 模式模拟实现 ─────────────────────────────────

    def _paper_execute_funding(
        self, exchange, symbol, size_usd, direction, entry_price,
        hedge_exchange: str = "",
    ) -> Dict:
        position_id = f"paper_fund_{symbol}_{int(time.time())}"
        self._paper_positions[position_id] = {
            "position_id": position_id,
            "mode": "paper",
            "exchange": exchange,
            "hedge_exchange": hedge_exchange,
            "symbol": symbol,
            "size_usd": size_usd,
            "direction": direction,
            "entry_price": entry_price,
            "entry_time": time.time(),
            "strategy": "funding_rate",
        }
        logger.info(f"[LiveExecutor] Funding arb PAPER executed: {position_id}")
        return {"ok": True, "position_id": position_id, "mode": "paper",
                "exchange": exchange, "symbol": symbol, "size_usd": size_usd}

    def _paper_execute_cross_exchange(self, exchange_a, exchange_b, symbol, size_usd,
                                       price_a, price_b, direction_a, direction_b) -> Dict:
        position_id = f"paper_cross_{symbol}_{int(time.time())}"
        self._paper_positions[position_id] = {
            "position_id": position_id,
            "mode": "paper",
            "exchange_a": exchange_a,
            "exchange_b": exchange_b,
            "symbol": symbol,
            "size_usd": size_usd,
            "price_a": price_a,
            "price_b": price_b,
            "entry_time": time.time(),
            "strategy": "cross_exchange",
        }
        logger.info(f"[LiveExecutor] Cross-exchange arb PAPER executed: {position_id}")
        return {"ok": True, "position_id": position_id, "mode": "paper",
                "exchange_a": exchange_a, "exchange_b": exchange_b, "symbol": symbol, "size_usd": size_usd}

    def _paper_execute_basis(self, exchange, symbol, size_usd, basis_pct, perp_price, spot_price) -> Dict:
        position_id = f"paper_basis_{symbol}_{int(time.time())}"
        self._paper_positions[position_id] = {
            "position_id": position_id,
            "mode": "paper",
            "exchange": exchange,
            "symbol": symbol,
            "size_usd": size_usd,
            "basis_pct": basis_pct,
            "perp_price": perp_price,
            "spot_price": spot_price,
            "entry_time": time.time(),
            "strategy": "basis",
        }
        logger.info(f"[LiveExecutor] Basis arb PAPER executed: {position_id}")
        return {"ok": True, "position_id": position_id, "mode": "paper",
                "exchange": exchange, "symbol": symbol, "size_usd": size_usd}

    def _paper_close_position(self, position_id, reason) -> Dict:
        pos = self._paper_positions.pop(position_id, None)
        if pos:
            logger.info(f"[LiveExecutor] PAPER closed position: {position_id}, reason: {reason}")
            return {"ok": True, "position_id": position_id, "closed": True, "reason": reason}
        return {"ok": False, "error": "position_not_found"}

    def get_paper_positions(self) -> List[Dict]:
        """获取所有 paper 模拟仓位"""
        return list(self._paper_positions.values())
