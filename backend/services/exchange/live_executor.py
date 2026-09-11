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

import inspect
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
        from backend.config import settings
        return getattr(settings, "DEFAULT_EXCHANGE", "asterdex") or "asterdex"
    except Exception:
        return "asterdex"


def _leverage_fail_close() -> bool:
    """杠杆对齐失败时是否拒绝开仓（默认 true）。

    设为 false 可紧急回到"只告警不阻塞"的旧行为，但会重新暴露"交易所残留高倍数
    导致实际敞口远超本地记账"的风险，仅作应急开关。
    """
    raw = os.getenv("LIVE_LEVERAGE_FAIL_CLOSE")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _close_raw_exchange(client) -> None:
    """[2026-08-28] 关闭 fresh client 的底层 ccxt exchange（释放 aiohttp 会话）。

    每次查询新建客户端（避免 "Event loop is closed"），查询后必须显式 close，
    否则每个客户端泄漏一个 aiohttp session + 若干连接（对账 120s/次会累积）。

    [2026-09-11 修复] 旧实现 `asyncio.run(_raw.close())` 在新 event loop 里执行
    close：ccxt async 的 aiohttp 会话绑定在**上一个**（已关闭的）loop 上，新 loop
    清理不到它 → 每次查询仍泄漏一个 session（backend.error.log 每 30-45s 一轮
    "Unclosed client session/connector" 实证）。故改为「同一 loop 内执行操作并
    finally close」的 `_run_and_close`，本函数保留为兜底（不再单独新建 loop）。
    """
    try:
        _raw = getattr(client, "_exchange", None)
        if _raw is not None and hasattr(_raw, "close"):
            result = _raw.close()
            if inspect.isawaitable(result):
                # 无运行中 loop 时最后兜底（正常路径不应走到这里）
                import asyncio
                asyncio.run(result)
    except Exception:
        pass


def _run_and_close(client, op):
    """在同一 event loop 内执行异步操作并在 finally 中 close 客户端。

    唯一能真正释放 aiohttp 会话的方式（见 _close_raw_exchange 的 09-11 注释）。
    """
    import asyncio

    async def _do():
        try:
            return await op()
        finally:
            _close_raw_exchange(client)

    return asyncio.run(_do())


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
            # ── [2026-09-05] 短线新开硬闸（实盘与纸盘同一语义）──
            try:
                from backend.services.full_auto.scalp_open_gate import scalp_new_open_blocked
                _sc_block, _sc_reason = scalp_new_open_blocked(
                    add_type="open",
                    trade_nature=getattr(ctx, "trade_nature", None),
                    timeframe_tier=getattr(ctx, "timeframe_tier", None),
                    reduce_only=bool(getattr(ctx, "reduce_only", False)),
                )
                if _sc_block:
                    return OrderResult(
                        status="blocked",
                        symbol=ctx.symbol,
                        side=ctx.side,
                        channel="live",
                        exchange=self._exchange,
                        error=_sc_reason,
                        blocked_by="scalp_open_disabled",
                        blocked_layer="scalp_open_gate",
                    )
            except Exception as _sc_err:
                logger.warning("[LiveExecutor] 短线新开闸检查异常（拒开）: %s", _sc_err)
                try:
                    from backend.config.settings import SCALP_OPEN_DISABLED as _sod
                except Exception:
                    _sod = True
                if _sod and not bool(getattr(ctx, "reduce_only", False)):
                    return OrderResult(
                        status="blocked",
                        symbol=ctx.symbol,
                        side=ctx.side,
                        channel="live",
                        exchange=self._exchange,
                        error="scalp_open_gate_error",
                        blocked_by="scalp_open_disabled",
                        blocked_layer="scalp_open_gate",
                    )

            # ── [2026-08-31 诚实拒单] 白名单前置校验 ──
            # place_ai_driven_order 拒单时只写 ai_decision_logs(executed=False)、
            # 返回 None，本方法却乐观标 "filled" → 调用方（平仓/减仓链路）以为
            # 已成交，实盘单黑洞。这里提前校验，不符时如实返回 error。
            # 注意：HL 账户白名单来自 hyperliquid_symbol_service，不走全局
            # user_trading_pairs，跳过本前置（HL 拒单路径不变）。
            try:
                from backend.database.models import Account as _Acct
                _acct = db.query(_Acct).filter(_Acct.id == ctx.account_id).first()
                _ex_name = (
                    getattr(_acct, "selected_exchange", None)
                    or self._exchange or _default_exchange()
                ).lower()
                if _ex_name != "hyperliquid":
                    from backend.services.trading_pairs_config import (
                        get_user_trading_pairs_set,
                    )
                    _base = str(ctx.symbol or "").upper().split("/")[0].strip()
                    if _base and _base not in get_user_trading_pairs_set():
                        return OrderResult(
                            status="error",
                            symbol=ctx.symbol,
                            side=ctx.side,
                            channel="live",
                            exchange=self._exchange,
                            error=f"symbol_not_in_whitelist:{_base}",
                        )
            except Exception:
                pass

            # ── 阶段 3: 子仓位跟踪路径（默认关闭，灰度启用）──
            if _live_sub_position_tracking_enabled():
                return self._place_order_via_lpm(db, ctx)

            # ── [2026-09-04 杠杆统一] 旧路径补齐交易所杠杆对齐 ──
            # 此前 _apply_leverage + fail-close 只装在 LPM 路径上，而
            # LIVE_SUB_POSITION_TRACKING 默认 false —— 线上实际跑的恰恰是这条
            # 旧路径，等于杠杆对齐在生效路径上从未执行过。
            # 交易所的杠杆是按 symbol 记忆的：上一次设成多少就一直留着，且同向
            # 同币仓位会被合并。本地按 5x 记账而交易所残留 75x 时，同样的保证金
            # 会开出 15 倍名义敞口，爆仓价也完全不是本地算的那个（XPL 事故）。
            # 减仓/平仓不阻断，否则止损会被卡住。
            if not bool(getattr(ctx, "reduce_only", False)):
                _lev_ok = False
                try:
                    _lev_ok = self._apply_leverage(
                        db, ctx.account_id, ctx.symbol, float(ctx.leverage or 0),
                    )
                except Exception as _lev_err:
                    logger.warning(
                        "[LiveExecutor] 杠杆对齐异常 %s: %s", ctx.symbol, _lev_err,
                    )
                if not _lev_ok and _leverage_fail_close():
                    logger.error(
                        "[LiveExecutor] %s 杠杆未确认对齐到 %sx，拒绝开仓"
                        "（交易所残留倍数会让实际敞口偏离本地记账）",
                        ctx.symbol, ctx.leverage,
                    )
                    return OrderResult(
                        status="error",
                        symbol=ctx.symbol,
                        side=ctx.side,
                        channel="live",
                        exchange=self._exchange,
                        error=f"leverage_align_failed:{ctx.symbol}@{ctx.leverage}x",
                    )

            # ── 旧路径: 直连 place_ai_driven_order ──
            trigger_ctx = self._send_raw_order(db, ctx)

            # [2026-09-07] 不再乐观标 filled：回读 OMS live_orders 最新状态。
            # 订单已走 _place_order_fresh_client 的 begin/finish（OMS 状态机），
            # 这里把真实状态映射回 OrderResult，避免「以为成交实则悬挂/拒单」。
            _real_status, _oms_meta = self._readback_oms_status(db, ctx)
            return OrderResult(
                status=_real_status,
                symbol=ctx.symbol,
                side=ctx.side,
                filled_quantity=ctx.quantity if _real_status == "filled" else 0.0,
                leverage=ctx.leverage,
                tp_price=ctx.tp_price,
                sl_price=ctx.sl_price,
                channel="live",
                exchange=self._exchange,
                raw={"trigger_context": trigger_ctx, "oms": _oms_meta},
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

    def _readback_oms_status(self, db, ctx: OrderContext) -> tuple:
        """[2026-09-07] 下单后回读 OMS live_orders 最新状态，映射为 OrderResult.status。

        place_ai_driven_order 返回 None（结果内部消耗），但订单已走
        _place_order_fresh_client 的 OMS begin/finish —— live_orders 有真实状态。
        取该账户+币种最近 30s 内最新一条，映射：
          filled→filled；partial→partial；acked/submitted/intent→submitted（已受理未成交）；
          rejected/expired/cancelled→error；unknown/无记录→submitted（保守：已发出待确认）。
        任何异常回退 "filled"（不影响主流程，与旧行为一致）。
        """
        try:
            from backend.services.oms.order_store import list_orders, now_ms
            rows = list_orders(
                account_id=int(ctx.account_id),
                symbol=str(ctx.symbol or "").upper(),
                since_ms=now_ms() - 30_000,
                limit=1,
            )
            if not rows:
                return "submitted", {"note": "no_oms_record"}
            o = rows[0]
            st = str(o.get("status") or "").lower()
            meta = {
                "client_order_id": o.get("client_order_id"),
                "status": st,
                "filled_qty": o.get("filled_qty"),
                "avg_price": o.get("avg_price"),
                "exchange_order_id": o.get("exchange_order_id"),
            }
            if st == "filled":
                return "filled", meta
            if st == "partial":
                return "partial", meta
            if st in ("acked", "submitted", "intent"):
                return "submitted", meta
            if st in ("rejected", "expired", "cancelled"):
                return "error", {**meta, "error": f"oms_{st}"}
            return "submitted", meta  # unknown → 保守：已发出待确认
        except Exception as exc:
            logger.debug("[LiveExecutor] OMS 回读失败（回退乐观 filled）: %s", exc)
            return "filled", {"note": "oms_readback_error", "error": str(exc)[:120]}

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

    def _apply_leverage(self, db, account_id: int, symbol: str, leverage: float) -> bool:
        """下单前把交易所杠杆对齐到该币种的统一档位，返回是否 **已确认** 对齐。

        [2026-09-04 fail-close] 原实现失败只告警不阻塞，交给对账兜底——但对账是事后的，
        期间交易所仍按旧杠杆成交：若交易所残留 75x 而本地按 5x 记账，同样的保证金会开出
        15 倍的名义敞口（XPL 事故即此）。故开仓路径改为失败即拒单，由调用方按
        reduce_only 区分（减仓/平仓失败仍放行，避免止损被卡住）。
        """
        try:
            from backend.database.models import Account
            account = db.query(Account).filter(Account.id == account_id).first()
            ex = (
                getattr(account, "selected_exchange", None)
                or self._exchange or _default_exchange()
            ).lower()
            if ex == "hyperliquid":
                from backend.services.hyperliquid_environment import get_hyperliquid_client
                client = get_hyperliquid_client(db, account_id)
                if client is None:
                    logger.warning("[LiveExecutor] HL 无客户端, 杠杆无法对齐")
                    return False
                try:
                    client.set_leverage(db, symbol, int(round(leverage)))
                    return True
                except Exception as e:
                    logger.warning("[LiveExecutor] HL set_leverage 失败: %s", e)
                    return False
            from backend.services.exchange.exchange_manager import ExchangeManager
            _mt_lev = (getattr(account, "binance_market_type", None) or "usdt_m")
            # [2026-08-28] 新建客户端，避免跨 asyncio.run 复用缓存客户端的
            # "Event loop is closed"（与余额/持仓查询同因）。
            client = ExchangeManager().create_fresh_client(
                ex, user_id=getattr(account, "user_id", None) or 1,
                account_id=account_id,
                market_type=_mt_lev if ex == "binance" else None,
            )
            if client is None:
                logger.warning("[LiveExecutor] %s 无客户端, 杠杆无法对齐", ex)
                return False
            try:
                ok = _run_and_close(
                    client, lambda: client.set_leverage(symbol, int(round(leverage)))
                )
            except Exception:
                ok = False
            if not ok:
                logger.warning(
                    "[LiveExecutor] ccxt set_leverage 未确认 %s %sx", symbol, leverage,
                )
            return bool(ok)
        except Exception as e:
            logger.warning(
                "[LiveExecutor] set_leverage 应用失败(%s %sx): %s", symbol, leverage, e,
            )
            return False

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
                # G6: 差额单前对齐交易所杠杆到该币种统一档位
                _lev_ok = False
                try:
                    _lev_ok = executor_self._apply_leverage(
                        _db, ctx.account_id, symbol, leverage,
                    )
                except Exception as _lev_err:
                    logger.warning(
                        "[LiveExecutor] 杠杆对齐异常 %s: %s", symbol, _lev_err,
                    )
                # [2026-09-04 fail-close] 杠杆没对上就开仓 = 按交易所残留倍数成交，
                # 名义敞口会偏离本地记账（XPL 事故）。减仓/平仓不阻断，否则止损会被卡住。
                if not _lev_ok and not bool(sub_ctx.reduce_only) and _leverage_fail_close():
                    raise RuntimeError(
                        f"杠杆对齐失败，拒绝开仓：{symbol} 目标 {leverage}x "
                        f"（交易所未确认，实际成交倍数不可控）"
                    )
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

        # [2026-09-02 因子闭环修复 D10] 实盘开仓写因子快照。
        # 此前 paper 侧在 paper_engine 内部记快照、live 侧全程零调用，
        # signal_trade_feedback 里没有任何实盘样本 —— 而它正是 factor_ic_evaluator
        # 算因子 IC 的唯一数据源，等于实盘成交对因子权重的贡献恒为 0。
        # 位置在下单完成之后，因子计算耗时不会影响成交价；钩子内部独立会话 +
        # 全异常自吞，失败只丢一条学习样本，绝不影响下单结果。
        try:
            from backend.services.live_learning_hooks import (
                record_live_entry_snapshot,
            )
            record_live_entry_snapshot(
                account_id=ctx.account_id,
                sub_position_id=lpm_result.get("sub_position_id"),
                symbol=ctx.symbol,
                position_side=position_side,
                trade_nature=trade_nature,
            )
        except Exception as _llh_err:
            logger.debug("[LiveExecutor] 实盘开仓学习钩子跳过: %s", _llh_err)

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
            # [2026-08-31 平仓修复] 数量与 reduce_only 必须透传：此前被丢弃导致
            # 平仓决策落入 sell 开仓分支（按余额重算名义、reduce_only=False、
            # 无 quantity 时目标份数=0）——实盘"平仓"从未真正发出平仓单。
            "quantity": float(ctx.quantity or 0),
            "reduce_only": bool(ctx.reduce_only),
            "close_position_side": (ctx.trigger_context or {}).get("close_position_side"),
            # 阶段 3.2: 执行算法透传（下游 place_ai_driven_order 消费切片下单）
            "algo": (ctx.algo or "MARKET").upper(),
            "algo_config": ctx.algo_config,
        }

    # ── 平仓 ────────────────────────────────────────────────────

    def close_position(
        self, db, account_id: int, symbol: str, side: str,
        reason: str = "manual", quantity: Optional[float] = None,
        strategy_id: Optional[str] = None,
        trade_nature: Optional[str] = None,
    ) -> OrderResult:
        """平仓 —— 通过 place_ai_driven_order 发反向 reduce_only 单。

        [2026-08-28 实盘接线GAP-4] LIVE_SUB_POSITION_TRACKING=true 时路由到
        LPM：trade_nature 给定时按层平该层子仓(qty=部分平仓数量)；未给定
        （紧急/全平场景）平掉该 symbol 全周期子仓。账本与交易所实仓同步，
        杜绝"发原始 reduce_only 单但 LPM 账本不动"的漂移。
        """
        if _live_sub_position_tracking_enabled():
            try:
                from backend.services.live_position_manager import live_position_manager

                def _cb(_db, _symbol, _order_side, _qty, _lev):
                    close_ctx = OrderContext(
                        account_id=account_id,
                        symbol=_symbol,
                        side=_order_side,
                        quantity=float(_qty),
                        order_type="market",
                        leverage=float(_lev),
                        reduce_only=True,
                        strategy_id=strategy_id,
                        trigger_context={"close_reason": reason, "close_position_side": side},
                    )
                    self._send_raw_order(_db, close_ctx)
                    return {"order_id": None, "fill_price": 0.0}

                _close_qty = quantity if quantity and quantity > 0 else None
                if trade_nature:
                    res = live_position_manager.close_sub_position(
                        db, account_id, symbol, trade_nature, _cb,
                        qty=_close_qty,
                    )
                else:
                    res = live_position_manager.close_all_symbol(
                        db, account_id, symbol, _cb, qty=_close_qty,
                    )
                return OrderResult(
                    status="filled" if res.get("closed") else "no_position",
                    symbol=symbol,
                    side="sell" if side == "long" else "buy",
                    filled_quantity=float(res.get("closed_size") or 0.0),
                    leverage=1.0,
                    channel="live",
                    exchange=self._exchange,
                    raw={"lpm_close": res},
                )
            except Exception as lpm_err:
                logger.error(
                    "[LiveExecutor] LPM 平仓路由异常(降级直连reduce_only): %s", lpm_err,
                    exc_info=True,
                )

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
        _mt = (getattr(account, "binance_market_type", None) or "usdt_m")
        # [2026-08-28 实盘零成交修复] 每次查询新建客户端：缓存客户端绑定的
        # aiohttp 会话/event loop 跨 asyncio.run 复用会抛 "Event loop is closed"
        # 并可能阻塞（对账/宪法风控热路径）。单次调用内 create→run→丢弃。
        client = mgr.create_fresh_client(
            exchange, user_id=user_id, account_id=account_id,
            market_type=_mt if exchange == "binance" else None,
        )
        if not client:
            logger.warning(f"[LiveExecutor] CCXT 客户端未配置: exchange={exchange} user={user_id} account={account_id}")
            return []
        try:
            positions = _run_and_close(client, client.get_positions)
        except Exception as e:
            logger.warning(f"[LiveExecutor] CCXT get_positions 异常: {e}")
            return []

        # [2026-08-28 实盘零成交修复] client.get_positions() 返回 ExchangePosition
        # dataclass 列表（非 dict），原 p.get(...) 会 AttributeError。统一 _val 兼容。
        def _val(obj, key, default):
            if obj is None:
                return default
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        result = []
        for p in (positions or []):
            size = float(_val(p, "size", 0) or 0)
            if abs(size) < 1e-9:
                continue
            side = str(_val(p, "side", "") or "").lower()
            result.append({
                "symbol": _val(p, "symbol", ""),
                "side": side,
                "size": abs(size),
                "entry_price": float(_val(p, "entry_price", 0) or 0),
                "mark_price": float(_val(p, "mark_price", 0) or 0),
                "leverage": float(_val(p, "leverage", 1) or 1),
                "unrealized_pnl": float(_val(p, "unrealized_pnl", 0) or 0),
                "margin": float(_val(p, "margin", 0) or 0),
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
        _mt = (getattr(account, "binance_market_type", None) or "usdt_m")
        # [2026-08-28 实盘零成交修复] 每次查询新建客户端（见 _get_ccxt_positions
        # 同因注释），杜绝跨 asyncio.run 复用导致的 "Event loop is closed"。
        client = mgr.create_fresh_client(
            exchange, user_id=user_id, account_id=account_id,
            market_type=_mt if exchange == "binance" else None,
        )
        if not client:
            return None
        try:
            bal = _run_and_close(client, client.get_balance)
        except Exception as e:
            logger.warning(f"[LiveExecutor] CCXT get_balance 异常: {e}")
            return None
        # [2026-08-28 实盘零成交修复] client.get_balance() 返回 ExchangeBalance
        # dataclass（不是 dict），此前直接调 .get() → AttributeError → 恒返回 None
        # → 宪法风控 snapshot 权益恒 0 → 所有实盘开仓被「无法获取实盘权益」拒绝。
        # 统一用「dict .get 优先，getattr 兜底」的 _val 取值，兼容两种返回形态。
        def _val(obj, key, default):
            if obj is None:
                return default
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        total_equity = _val(bal, "total_equity", None)
        if total_equity in (None, 0):
            total_equity = _val(bal, "total", 0)
        return {
            "account_id": account_id,
            "total_equity": float(total_equity or 0),
            "available_balance": float(_val(bal, "available_balance", None)
                                       or _val(bal, "available", None)
                                       or _val(bal, "free", 0) or 0),
            "frozen_margin": float(_val(bal, "frozen_margin", None)
                                   or _val(bal, "used", 0) or 0),
            "unrealized_pnl": float(_val(bal, "unrealized_pnl", 0) or 0),
            "channel": "live",
            "exchange": exchange,
            "raw": bal,
        }
