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
import os
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

    @staticmethod
    def _v3_live_open_guard(symbol: str, venues) -> Optional[Dict[str, Any]]:
        """[2026-09-03 v3 方向7] 套利实盘开仓也必经 RiskEngine 单入口（急停 / TradingState /
        各腿 venue 连通性熔断 / 避险窗口）。平仓 close_position 不经此闸。返回 None=放行。"""
        try:
            from backend.services.risk.risk_engine import pre_trade, PreTradeRequest
            for v in [x for x in (venues or []) if x]:
                verdict = pre_trade(None, PreTradeRequest(
                    account_id=0, symbol=str(symbol), side="open", is_open=True, venue=str(v).lower(),
                    trade_nature="arbitrage", source="arb_live_executor",
                ))
                if not verdict.allowed:
                    logger.warning("[LiveExecutor][RiskEngine v3] 套利开仓拦截 %s@%s: %s", symbol, v, verdict.reason)
                    return {"ok": False, "error": "risk_engine_blocked", "reason": verdict.reason,
                            "reason_code": verdict.reason_code, "venue": v}
        except Exception as exc:
            logger.warning("[LiveExecutor][RiskEngine v3] 检查异常（放行）: %s", exc)
        return None

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
        # [2026-09] 杠杆跟随策略配置（arb_config.yaml funding.leverage / ARB_FUNDING_LEVERAGE）
        leverage = max(1.0, float(payload.get("leverage", 3.0) or 3.0))

        if not symbol or size_usd <= 0 or entry_price <= 0:
            return {"ok": False, "error": "invalid_parameters"}

        if self._mode == "paper":
            return self._paper_execute_funding(
                primary_exchange, symbol, size_usd, direction, entry_price,
                hedge_exchange=hedge_exchange,
            )

        _blocked = self._v3_live_open_guard(symbol, venues=[primary_exchange, hedge_exchange])
        if _blocked:
            return _blocked

        mgr = self._get_exchange_manager()
        if mgr is None:
            return {"ok": False, "error": "exchange_manager_unavailable"}

        try:
            size = max(size_usd / entry_price, 0.001)
            from .async_bridge import run_async

            # ── [2026-09] 双腿编排（兼容积分一体化）──
            # 收腿 = primary（短收正 funding）；对冲腿 = hedge（反向）。
            # 开腿顺序：非 asterdex 腿先开（市价秒成），asterdex 腿最后开
            # （maker-first 挂单等待窗口内只剩一条腿，delta 敞口受控）。
            # asterdex 腿无论主/对冲角色都接 wash 守卫 + 积分计量。
            primary_side = OrderSide.SELL if direction == "short" else OrderSide.BUY
            hedge_side = OrderSide.BUY if direction == "short" else OrderSide.SELL

            legs: List[Dict[str, Any]] = [
                {"venue": primary_exchange, "side": primary_side, "role": "primary"},
            ]
            if hedge_exchange and hedge_exchange != primary_exchange:
                legs.append({"venue": hedge_exchange, "side": hedge_side, "role": "hedge"})
            # asterdex 腿移到末尾
            _adx_idx = next((i for i, lg in enumerate(legs) if lg["venue"] == "asterdex"), -1)
            if _adx_idx >= 0 and _adx_idx != len(legs) - 1:
                legs.append(legs.pop(_adx_idx))

            # 积分策略（asterdex 凭证级开关）
            _points_on = False
            _maker_first = False
            _timeout = 30.0
            _has_asterdex = any(lg["venue"] == "asterdex" for lg in legs)
            if _has_asterdex:
                try:
                    from backend.services.rebate_arb.live_points_engine import live_points_engine
                    _pol = live_points_engine.get_policy()
                    _points_on = bool(_pol.get("enabled"))
                    _maker_first = _points_on and bool(_pol.get("maker_first"))
                    _timeout = float(_pol.get("maker_timeout_s") or 30.0)
                except Exception as _pe:
                    logger.debug("[LiveExecutor] 积分策略读取跳过: %s", _pe)

            # wash 守卫：asterdex 同所同币**反向**持仓 → 跳过（官方惩罚对冲刷分；
            # 同向加仓允许——单边方向仓 + 平仓数量按自身腿核算，互不干扰）。
            _adx_opposing: Dict[str, str] = {}  # symbol -> existing side
            if _has_asterdex:
                try:
                    _adx_client = mgr.get_client("asterdex")
                    if _adx_client is not None:
                        _existing = run_async(_adx_client.get_positions()) or []
                        for _p in _existing:
                            _psym = str(getattr(_p, "symbol", "") or "").upper()
                            _psize = float(getattr(_p, "size", 0) or 0)
                            if _psym == symbol.upper() and _psize > 0:
                                _adx_opposing[_psym] = str(getattr(_p, "side", "") or "").lower()
                except Exception as _wg_err:
                    logger.debug("[LiveExecutor] wash 守卫检查跳过: %s", _wg_err)

            # asterdex 保证金预检（软校验：按策略杠杆估算所需保证金，留 20% 缓冲）
            if _has_asterdex:
                try:
                    _adx_client = mgr.get_client("asterdex")
                    if _adx_client is not None and hasattr(_adx_client, "get_balance"):
                        _bal = run_async(_adx_client.get_balance())
                        _avail = float(getattr(_bal, "available_balance", 0) or 0)
                        _need = size_usd / leverage * 1.2
                        if _avail < _need:
                            logger.warning(
                                "[LiveExecutor] asterdex 可用余额 $%.2f < 所需保证金 $%.2f（%.0fx），跳过",
                                _avail, _need, leverage,
                            )
                            return {"ok": False, "error": "insufficient_asterdex_margin"}
                except Exception as _mb_err:
                    logger.debug("[LiveExecutor] 保证金预检跳过: %s", _mb_err)

            opened: List[Dict[str, Any]] = []  # 已开腿 [{client, side, size, venue}]
            results: Dict[str, Any] = {}
            for lg in legs:
                venue, side, role = lg["venue"], lg["side"], lg["role"]
                # wash 守卫（方向感知）：asterdex 腿与已有持仓反向 → 跳过
                if venue == "asterdex" and symbol.upper() in _adx_opposing:
                    _leg_side = "long" if side == OrderSide.BUY else "short"
                    if _adx_opposing[symbol.upper()] in ("long", "short") and _adx_opposing[symbol.upper()] != _leg_side:
                        logger.warning(
                            "[LiveExecutor] wash 守卫：asterdex %s 已有反向持仓（%s vs %s），跳过",
                            symbol, _adx_opposing[symbol.upper()], _leg_side,
                        )
                        for _op in reversed(opened):
                            self._emergency_close_leg(_op["client"], symbol, _op["size"], _op["side"], run_async)
                        return {"ok": False, "error": "wash_guard_opposing_position_on_asterdex"}
                client = mgr.get_client(venue)
                if client is None:
                    for _op in reversed(opened):
                        self._emergency_close_leg(_op["client"], symbol, _op["size"], _op["side"], run_async)
                    return {"ok": False, "error": f"no_client_for_{venue}"}

                order = ExchangeOrder(
                    order_id=f"arb_{role}_{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side=side,
                    order_type=OrderType.MARKET,
                    size=size,
                    # [2026-09] 杠杆跟随策略配置（arb_config funding.leverage）：
                    # 套利是实盘合约交易的附带，双腿对冲，低杠杆、语义可预测
                    leverage=int(leverage),
                )
                if venue == "asterdex" and _maker_first and hasattr(client, "place_order_maker_first"):
                    r = run_async(client.place_order_maker_first(order, timeout_s=_timeout))
                else:
                    r = run_async(client.place_order(order))

                if r.get("status") == "error":
                    logger.error("[LiveExecutor] %s 腿失败: %s", venue, r)
                    try:
                        from backend.services.arbitrage.arbitrage_alert_monitor import arb_alert_monitor
                        arb_alert_monitor.on_leg_failure(
                            symbol, venue, str(r.get("message", "unknown")), leg=role,
                        )
                    except Exception:
                        pass
                    for _op in reversed(opened):
                        self._emergency_close_leg(_op["client"], symbol, _op["size"], _op["side"], run_async)
                    return {"ok": False, "error": f"{role}_leg_failed: {r.get('message')}"}

                opened.append({"client": client, "side": side, "size": size, "venue": venue})
                results[role] = r

                # [2026-09] 积分账本：asterdex 腿无论主/对冲都计量（开关开启时）
                if venue == "asterdex" and _points_on:
                    try:
                        from backend.services.rebate_arb.live_points_engine import live_points_engine
                        _qty = float(r.get("qty") or r.get("amount") or size)
                        _px = float(r.get("price") or r.get("average") or entry_price)
                        _maker = bool(r.get("maker", False))
                        live_points_engine.record_fill(
                            source="funding_arb",
                            symbol=symbol,
                            side="sell" if side == OrderSide.SELL else "buy",
                            qty=_qty,
                            price=_px,
                            maker=_maker,
                            order_id=str(r.get("order_id") or order.order_id),
                        )
                    except Exception as _pt_err:
                        logger.debug("[LiveExecutor] 积分记录失败: %s", _pt_err)

            position_id = f"live_fund_{symbol}_{int(time.time())}"
            logger.info("[LiveExecutor] Funding arb LIVE executed: %s", position_id)

            return {
                "ok": True,
                "position_id": position_id,
                "mode": "live",
                "exchange": primary_exchange,
                "hedge_exchange": hedge_exchange,
                "symbol": symbol,
                "size_usd": size_usd,
                "direction": direction,
                "primary_result": results.get("primary"),
                "hedge_result": results.get("hedge"),
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

        _blocked = self._v3_live_open_guard(symbol, venues=[exchange_a, exchange_b])
        if _blocked:
            return _blocked

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

            # [2026-09] asterdex 腿积分一体化（跨所套利）：
            # wash 守卫（反向持仓才拦截）+ 积分计量。跨所套利双腿需同时成交，
            # 不做 maker-first（挂单等待会破坏配对）。
            _x_points_on = False
            if "asterdex" in (exchange_a, exchange_b):
                try:
                    from backend.services.rebate_arb.live_points_engine import live_points_engine
                    _x_points_on = bool(live_points_engine.get_policy().get("enabled"))
                except Exception:
                    _x_points_on = False

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

            # [2026-09] asterdex 腿计量（开关开启时）
            if _x_points_on:
                try:
                    from backend.services.rebate_arb.live_points_engine import live_points_engine
                    for _ex, _side, _res, _oid in (
                        (exchange_a, side_a, result_a, order_a.order_id),
                        (exchange_b, side_b, result_b, order_b.order_id),
                    ):
                        if _ex != "asterdex" or _res.get("status") == "error":
                            continue
                        _qty = float(_res.get("qty") or _res.get("amount") or size)
                        _px = float(_res.get("price") or _res.get("average") or ref_price)
                        live_points_engine.record_fill(
                            source="cross_exchange_arb",
                            symbol=symbol,
                            side="sell" if _side == OrderSide.SELL else "buy",
                            qty=_qty,
                            price=_px,
                            maker=False,
                            order_id=str(_oid),
                        )
                except Exception as _xe:
                    logger.debug("[LiveExecutor] 跨所积分记录失败: %s", _xe)

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

        _blocked = self._v3_live_open_guard(symbol, venues=[exchange])
        if _blocked:
            return _blocked

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
                # [2026-09] 积分一体化：asterdex 腿平仓 → 结算持仓积分
                if ex == "asterdex" and str(result.get("status") or "").lower() not in ("error",):
                    try:
                        from backend.services.rebate_arb.live_points_engine import live_points_engine
                        live_points_engine.record_close(
                            source="funding_arb",
                            symbol=symbol,
                            order_id=close_order.order_id,
                            reason=reason,
                        )
                    except Exception as _pc_err:
                        logger.debug("[LiveExecutor] 平仓积分记录失败: %s", _pc_err)
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
