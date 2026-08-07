"""LiveExecutor —— 实盘执行通道（封装 HL native + CCXT）。

设计目标（阶段 3 执行层标准化）:
- 对外提供与 PaperExecutor 同构的 ExecutionChannel 接口
- 内部委托 trading_commands.place_ai_driven_order（自动路由 HL native / CCXT）
- 不删原代码，仅包一层 + 返回值标准化

核心差异（vs PaperExecutor）:
- place_order 委托 place_ai_driven_order（通过 trigger_context.pre_made_decisions 传决策）
- place_ai_driven_order 返回 None（结果内部消耗），故 LiveExecutor 合成 OrderResult
- get_positions / get_balance 委托交易所 adapter（非 paper_engine）

注意: 实盘下单是"触发式"的 —— place_ai_driven_order 接收 pre_made_decisions 后，
内部完成风控、sizing、TP/SL 计算、下单、持久化。LiveExecutor 只负责构造决策并触发。

阶段 3 子仓位跟踪（LIVE_SUB_POSITION_TRACKING）:
- 默认 false（保持旧行为，避免影响线上实盘）
- true 时 place_order 路由到 live_position_manager.execute_order，
  由 LPM 计算净差额、维护 LiveSubPosition 账本，再通过 exchange_callback
  回调本执行器的 _send_raw_order 实际下单。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from backend.services.exchange.executors import (
    ExecutionChannel,
    OrderContext,
    OrderResult,
)
from backend.utils.trace_context import bind_trace, generate_trace_id, get_trace_id

logger = logging.getLogger(__name__)


def _live_sub_position_tracking_enabled() -> bool:
    """读取 LIVE_SUB_POSITION_TRACKING 开关（默认 false）。

    开启后 LiveExecutor.place_order 会路由到 LivePositionManager.execute_order，
    本地按 trade_nature 维护子仓位账本，对交易所只发净差额单。
    """
    return os.getenv("LIVE_SUB_POSITION_TRACKING", "false").lower().strip() in (
        "true", "1", "yes", "on",
    )


def _default_exchange() -> str:
    """读取全局默认交易所（热路径用，避免反复 import）。"""
    try:
        from config import settings
        return getattr(settings, "DEFAULT_EXCHANGE", "asterdex") or "asterdex"
    except Exception:
        return "asterdex"


class LiveExecutor(ExecutionChannel):
    """实盘执行通道。

    封装 trading_commands.place_ai_driven_order（自动路由 HL/CCXT）。
    所有方法 sync（与 trading_commands 一致）。

    exchange 参数用于审计/日志（实际路由由 account.selected_exchange 决定）。
    """

    def __init__(self, exchange: Optional[str] = None):
        self._exchange = exchange

    @property
    def channel_name(self) -> str:
        return "live"

    # ── 下单 ────────────────────────────────────────────────────

    def place_order(self, db, ctx: OrderContext) -> OrderResult:
        """下单 —— 委托 trading_commands.place_ai_driven_order。

        实盘下单通过 trigger_context.pre_made_decisions 传递决策（跳过内部 AI 调用）。
        place_ai_driven_order 返回 None，故本方法合成 OrderResult。

        决策 dict 结构（与 full_auto._execute_live_trade 一致）:
        {
            "operation": "buy"/"sell"/"close",
            "symbol": ctx.symbol,
            "side": ctx.side,
            "leverage": ctx.leverage,
            "take_profit_price": ctx.tp_price,
            "stop_loss_price": ctx.sl_price,
            ... (sizing/price 由 place_ai_driven_order 内部 position_manager 计算)
        }

        阶段 3 子仓位跟踪: 当 LIVE_SUB_POSITION_TRACKING=true 时，路由到
        live_position_manager.execute_order —— 由 LPM 计算净差额、维护子仓账本，
        再通过 exchange_callback 回调 _send_raw_order 实际下单。
        """
        # 绑定 trace_id（若当前无）
        _bound_locally = False
        if not get_trace_id():
            _trace_cm = bind_trace(generate_trace_id("live-order"))
            _trace_cm.__enter__()
            _bound_locally = True

        try:
            # ── 阶段 3: 子仓位跟踪路径（默认关闭，灰度启用）──
            if _live_sub_position_tracking_enabled():
                return self._place_order_via_lpm(db, ctx)

            # ── 旧路径: 直连 place_ai_driven_order ──
            trigger_ctx = self._send_raw_order(db, ctx)

            return OrderResult(
                status="filled",  # 乐观标记（实际成交由交易所确认）
                symbol=ctx.symbol,
                side=ctx.side,
                filled_quantity=ctx.quantity,
                leverage=ctx.leverage,
                tp_price=ctx.tp_price,
                sl_price=ctx.sl_price,
                channel="live",
                exchange=self._exchange,
                raw={"trigger_context": trigger_ctx},
            )

        except Exception as e:
            logger.error(
                f"[LiveExecutor] place_order 异常: account={ctx.account_id} "
                f"{ctx.symbol} {ctx.side}: {e}",
                exc_info=True,
            )
            return OrderResult(
                status="error",
                symbol=ctx.symbol,
                side=ctx.side,
                channel="live",
                exchange=self._exchange,
                error=str(e),
            )
        finally:
            if _bound_locally:
                try:
                    _trace_cm.__exit__(None, None, None)
                except Exception:
                    pass

    def _send_raw_order(self, db, ctx: OrderContext) -> Dict[str, Any]:
        """实际向交易所下单（直连 place_ai_driven_order）。

        返回 trigger_context dict（place_ai_driven_order 自身返回 None，
        结果由交易所回填 + 持久化到 ai_decision_logs）。

        该方法同时作为 LivePositionManager.execute_order 的 exchange_callback
        调用目标（通过 _build_exchange_callback 包装适配签名）。
        """
        from backend.services.trading_commands import place_ai_driven_order

        # 构造决策（place_ai_driven_order 内部会用 pre_made_decisions 跳过 AI）
        decision = self._build_decision(ctx)

        # 构造 trigger_context（与 full_auto._execute_live_trade 一致）
        trigger_ctx: Dict[str, Any] = {
            "source": "unified_executor",
            "strategy_id": ctx.strategy_id,
            "pre_made_decisions": [decision],
        }
        # 合并调用方传入的额外 trigger_context
        if ctx.trigger_context:
            trigger_ctx.update(ctx.trigger_context)

        logger.info(
            f"[LiveExecutor] 触发实盘下单: account={ctx.account_id} "
            f"{ctx.symbol} {ctx.side} qty={ctx.quantity} lev={ctx.leverage}x "
            f"strategy={ctx.strategy_id}"
        )

        # place_ai_driven_order 返回 None（结果内部消耗/持久化）
        place_ai_driven_order(
            account_id=ctx.account_id,
            trigger_context=trigger_ctx,
        )
        return trigger_ctx

    def _place_order_via_lpm(self, db, ctx: OrderContext) -> OrderResult:
        """通过 LivePositionManager 下单（子仓位跟踪路径）。

        LPM 计算净差额后调用 exchange_callback(db, symbol, order_side, qty, leverage)，
        callback 内部用本执行器的 _send_raw_order 实际下单。
        LPM 维护 LiveSubPosition 账本并返回 {sub_position_id, order_id, ...}。
        """
        from backend.services.live_position_manager import live_position_manager

        # LPM 的 side 语义是 position side（long/short），OrderContext.side 是 order side（buy/sell）
        position_side = "long" if ctx.side == "buy" else "short"
        trade_nature = ctx.trade_nature or "swing"
        tier = ctx.timeframe_tier or "mid"

        executor_self = self

        def _exchange_cb(_db, symbol, order_side, qty, leverage):
            """LPM exchange_callback 适配器: 构造 OrderContext 调用 _send_raw_order。

            签名: (db, symbol, order_side[buy/sell], net_qty, leverage) -> {order_id, fill_price}
            """
            # 复用原 ctx 的 TP/SL/strategy，覆盖 side/qty/leverage（差额单）
            sub_ctx = OrderContext(
                account_id=ctx.account_id,
                symbol=symbol,
                side=order_side,
                quantity=float(qty) if qty is not None else 0.0,
                order_type=ctx.order_type,
                price=ctx.price,
                leverage=float(leverage) if leverage is not None else ctx.leverage,
                tp_price=ctx.tp_price,
                sl_price=ctx.sl_price,
                strategy_id=ctx.strategy_id,
                timeframe_tier=tier,
                trade_nature=trade_nature,
                expected_hold_hours=ctx.expected_hold_hours,
                reduce_only=ctx.reduce_only,
                algo=ctx.algo,
                algo_config=ctx.algo_config,
                trigger_context=ctx.trigger_context,
                position_metadata=ctx.position_metadata,
            )
            try:
                executor_self._send_raw_order(_db, sub_ctx)
            except Exception as cb_err:
                logger.error(
                    f"[LiveExecutor] LPM exchange_callback 下单异常: "
                    f"{symbol} {order_side} qty={qty}: {cb_err}",
                    exc_info=True,
                )
                raise
            # place_ai_driven_order 不返回 order_id/fill_price（异步撮合，由交易所回填）
            return {"order_id": None, "fill_price": 0.0}

        lpm_result = live_position_manager.execute_order(
            db=db,
            account_id=ctx.account_id,
            symbol=ctx.symbol,
            side=position_side,
            size=float(ctx.quantity or 0.0),
            leverage=float(ctx.leverage or 1.0),
            trade_nature=trade_nature,
            tier=tier,
            exchange_callback=_exchange_cb,
        )

        return OrderResult(
            status="filled",
            symbol=ctx.symbol,
            side=ctx.side,
            filled_quantity=ctx.quantity,
            leverage=ctx.leverage,
            tp_price=ctx.tp_price,
            sl_price=ctx.sl_price,
            channel="live",
            exchange=self._exchange,
            position_id=lpm_result.get("sub_position_id"),
            raw={"lpm_result": lpm_result},
        )

    def _build_decision(self, ctx: OrderContext) -> Dict[str, Any]:
        """从 OrderContext 构造 place_ai_driven_order 所需的决策 dict。

        注意: place_ai_driven_order 内部会用 position_manager.evaluate_trade 重新计算
        sizing/notional，所以这里只传方向/杠杆/TP/SL 等核心字段。
        """
        return {
            "operation": "buy" if ctx.side == "buy" else "sell",
            "symbol": ctx.symbol,
            "side": ctx.side,
            "action": ctx.side,  # 兼容字段
            "leverage": int(ctx.leverage) if ctx.leverage else 10,
            "take_profit_price": ctx.tp_price,
            "stop_loss_price": ctx.sl_price,
            "price": ctx.price,  # 限价单价格（market 单为 None/0）
            "confidence": 0.8,  # 默认置信度（实盘由 pre_made_decisions 跳过 AI）
            "reason": f"unified_executor: {ctx.trade_nature or 'swing'}",
            "trade_nature": ctx.trade_nature or "swing",
            "timeframe_tier": ctx.timeframe_tier or "mid",
            # 阶段 3.2: 执行算法透传（下游 place_ai_driven_order 消费切片下单）
            "algo": (ctx.algo or "MARKET").upper(),
            "algo_config": ctx.algo_config,
        }

    # ── 平仓 ────────────────────────────────────────────────────

    def close_position(
        self, db, account_id: int, symbol: str, side: str,
        reason: str = "manual", quantity: Optional[float] = None,
        strategy_id: Optional[str] = None,
    ) -> OrderResult:
        """平仓 —— 通过 place_ai_driven_order 发反向 reduce_only 单。

        实盘平仓: 对 long 仓位发 sell（reduce_only），对 short 发 buy（reduce_only）。
        place_ai_driven_order 内部处理 reduce_only 标记。
        """
        close_side = "sell" if side == "long" else "buy"
        ctx = OrderContext(
            account_id=account_id,
            symbol=symbol,
            side=close_side,
            quantity=quantity or 0.0,  # 0 表示全平（place_ai_driven_order 内部处理）
            order_type="market",
            leverage=1.0,
            reduce_only=True,
            strategy_id=strategy_id,
            trigger_context={"close_reason": reason, "close_position_side": side},
        )
        result = self.place_order(db, ctx)
        # 平仓的 pnl 由交易所回填，此处无法立即获取
        return result

    # ── 查询 ────────────────────────────────────────────────────

    def get_positions(self, db, account_id: int, status: str = "open") -> List[Dict[str, Any]]:
        """查询实盘持仓 —— 通过交易所 adapter（非 paper_engine）。

        委托 ExchangeManager.get_or_create_global_client 或 HyperliquidTradingClient.get_positions。
        注意: 实盘持仓查询是异步的（HTTP），此处用 asyncio.run 桥接。
        """
        try:
            from backend.database.models import Account
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                logger.warning(f"[LiveExecutor] get_positions: 账户 {account_id} 不存在")
                return []

            selected_exchange = (getattr(account, "selected_exchange", None) or self._exchange or _default_exchange()).lower()

            if selected_exchange == "hyperliquid":
                return self._get_hl_positions(db, account_id)
            return self._get_ccxt_positions(db, account_id, selected_exchange)
        except Exception as e:
            logger.error(f"[LiveExecutor] get_positions 异常: {e}", exc_info=True)
            return []

    def _get_hl_positions(self, db, account_id: int) -> List[Dict[str, Any]]:
        """通过 HyperliquidTradingClient 查询持仓。"""
        from backend.services.hyperliquid_environment import get_hyperliquid_client
        client = get_hyperliquid_client(db, account_id)
        if not client:
            return []
        # HyperliquidTradingClient.get_positions 返回 list[dict]
        positions = client.get_positions()
        # 标准化字段名（与 paper_engine.get_positions 对齐）
        result = []
        for p in (positions or []):
            szi = float(p.get("szi", 0) or p.get("size", 0) or 0)
            if abs(szi) < 1e-9:
                continue  # 跳过空仓位
            result.append({
                "symbol": p.get("coin", p.get("symbol", "")),
                "side": "long" if szi > 0 else "short",
                "size": abs(szi),
                "entry_price": float(p.get("entryPx", 0) or 0),
                "mark_price": float(p.get("markPx", 0) or 0),
                "leverage": float(p.get("leverage", {}).get("value", 1) or 1) if isinstance(p.get("leverage"), dict) else float(p.get("leverage", 1) or 1),
                "unrealized_pnl": float(p.get("unrealizedPnl", 0) or 0),
                "margin": float(p.get("marginUsed", 0) or 0),
                "status": "open",
                "channel": "live",
                "exchange": "hyperliquid",
                "raw": p,
            })
        return result

    def _get_ccxt_positions(self, db, account_id: int, exchange: str) -> List[Dict[str, Any]]:
        """通过 CCXT adapter 查询持仓。"""
        import asyncio
        from backend.database.models import Account
        from backend.services.exchange.exchange_manager import get_exchange_manager

        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return []
        mgr = get_exchange_manager()
        user_id = account.user_id or 1
        client = mgr.get_or_create_global_client(exchange, user_id=user_id)
        if not client:
            logger.warning(f"[LiveExecutor] CCXT 客户端未配置: exchange={exchange} user={user_id}")
            return []
        try:
            positions = asyncio.run(client.get_positions())
        except Exception as e:
            logger.warning(f"[LiveExecutor] CCXT get_positions 异常: {e}")
            return []
        result = []
        for p in (positions or []):
            size = float(p.get("size", 0) or getattr(p, "size", 0) or 0)
            if abs(size) < 1e-9:
                continue
            side = str(p.get("side", getattr(p, "side", "")) or "").lower()
            result.append({
                "symbol": p.get("symbol", getattr(p, "symbol", "")),
                "side": side,
                "size": abs(size),
                "entry_price": float(p.get("entry_price", getattr(p, "entry_price", 0)) or 0),
                "mark_price": float(p.get("mark_price", getattr(p, "mark_price", 0)) or 0),
                "leverage": float(p.get("leverage", getattr(p, "leverage", 1)) or 1),
                "unrealized_pnl": float(p.get("unrealized_pnl", getattr(p, "unrealized_pnl", 0)) or 0),
                "margin": float(p.get("margin", getattr(p, "margin", 0)) or 0),
                "status": "open",
                "channel": "live",
                "exchange": exchange,
            })
        return result

    def get_balance(self, db, account_id: int) -> Optional[Dict[str, Any]]:
        """查询实盘余额 —— 通过交易所 adapter。

        返回归一化 dict（与 paper_engine.get_balance 字段对齐）。
        """
        try:
            from backend.database.models import Account
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                return None

            selected_exchange = (getattr(account, "selected_exchange", None) or self._exchange or _default_exchange()).lower()

            if selected_exchange == "hyperliquid":
                return self._get_hl_balance(db, account_id)
            return self._get_ccxt_balance(db, account_id, selected_exchange)
        except Exception as e:
            logger.error(f"[LiveExecutor] get_balance 异常: {e}", exc_info=True)
            return None

    def _get_hl_balance(self, db, account_id: int) -> Optional[Dict[str, Any]]:
        """通过 HyperliquidTradingClient 查询余额。"""
        from backend.services.hyperliquid_environment import get_hyperliquid_client
        client = get_hyperliquid_client(db, account_id)
        if not client:
            return None
        state = client.get_account_state()
        # 归一化字段（与 paper_engine.get_balance 对齐）
        return {
            "account_id": account_id,
            "total_equity": float(state.get("total_equity", state.get("margin", 0)) or 0),
            "available_balance": float(state.get("available", state.get("withdrawable", 0)) or 0),
            "frozen_margin": float(state.get("used_margin", state.get("margin_used", 0)) or 0),
            "unrealized_pnl": float(state.get("unrealized_pnl", 0) or 0),
            "channel": "live",
            "exchange": "hyperliquid",
            "raw": state,
        }

    def _get_ccxt_balance(self, db, account_id: int, exchange: str) -> Optional[Dict[str, Any]]:
        """通过 CCXT adapter 查询余额。"""
        import asyncio
        from backend.database.models import Account
        from backend.services.exchange.exchange_manager import get_exchange_manager

        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return None
        mgr = get_exchange_manager()
        user_id = account.user_id or 1
        client = mgr.get_or_create_global_client(exchange, user_id=user_id)
        if not client:
            return None
        try:
            bal = asyncio.run(client.get_balance())
        except Exception as e:
            logger.warning(f"[LiveExecutor] CCXT get_balance 异常: {e}")
            return None
        return {
            "account_id": account_id,
            "total_equity": float(bal.get("total_equity", bal.get("total", 0)) or 0),
            "available_balance": float(bal.get("available", bal.get("free", 0)) or 0),
            "frozen_margin": float(bal.get("frozen_margin", bal.get("used", 0)) or 0),
            "unrealized_pnl": float(bal.get("unrealized_pnl", 0) or 0),
            "channel": "live",
            "exchange": exchange,
            "raw": bal,
        }
