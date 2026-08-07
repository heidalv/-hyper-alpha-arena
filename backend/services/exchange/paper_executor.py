"""PaperExecutor —— 模拟执行通道（封装 paper_engine + 净额逻辑）。

设计目标（阶段 3 执行层标准化）:
- 对外提供与 LiveExecutor 同构的 ExecutionChannel 接口
- 内部复用现有 paper_engine（已净额化）+ paper_exchange_simulator
- 不重写模拟逻辑，仅做接口封装 + 返回值标准化 + trace_id 注入
- 完整模拟交易所行为（延迟、撮合、费率、滑点）—— 已由 paper_exchange_simulator 实现

核心: place_order / close_position / get_positions / get_balance 委托给 paper_engine，
      返回值规范化为 OrderResult（屏蔽 paper_engine 的异构返回）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.services.exchange.executors import (
    ExecutionChannel,
    OrderContext,
    OrderResult,
)
from backend.utils.trace_context import bind_trace, generate_trace_id, get_trace_id

logger = logging.getLogger(__name__)


class PaperExecutor(ExecutionChannel):
    """模拟执行通道。

    封装 paper_engine.place_order / close_position / get_positions / get_balance。
    所有方法 sync（与 paper_engine 一致）。

    exchange 参数仅用于审计/费率解析（paper 模式下账户的 selected_exchange）。
    """

    def __init__(self, exchange: Optional[str] = None):
        self._exchange = exchange
        # 惰性导入 paper_engine 单例（避免循环依赖）
        from backend.services.paper_trading_engine import paper_engine
        self._engine = paper_engine

    @property
    def channel_name(self) -> str:
        return "paper"

    # ── 下单 ────────────────────────────────────────────────────

    def place_order(self, db, ctx: OrderContext) -> OrderResult:
        """下单（开仓/加仓）—— 委托 paper_engine.place_order。

        返回值标准化:
        - paper_engine 成功返回 {"status":"filled", "position_id":..., "price":...}
        - 风控拦截返回 {"success":False, "blocked":True, "blocked_by":...}（无 status 键）
        - 余额不足/拒单返回 {"status":"rejected", "error":...}
        - 异常抛出 ValueError（PaperBalance 缺失）
        统一映射到 OrderResult。
        """
        # 若当前无 trace_id，为本笔交易绑定一个（便于跨子系统排查）
        _bound_locally = False
        if not get_trace_id():
            self._trace = bind_trace(generate_trace_id("paper-order"))
            self._trace.__enter__()
            _bound_locally = True

        try:
            raw = self._place_with_algo(db, ctx)
            return self._normalize_place_result(raw, ctx)
        except ValueError as e:
            # PaperBalance 缺失等
            logger.warning(f"[PaperExecutor] place_order ValueError: {e}")
            return OrderResult(
                status="error",
                symbol=ctx.symbol,
                side=ctx.side,
                channel="paper",
                exchange=self._exchange,
                error=str(e),
            )
        except Exception as e:
            logger.error(f"[PaperExecutor] place_order 异常: {e}", exc_info=True)
            return OrderResult(
                status="error",
                symbol=ctx.symbol,
                side=ctx.side,
                channel="paper",
                exchange=self._exchange,
                error=str(e),
            )
        finally:
            if _bound_locally:
                try:
                    self._trace.__exit__(None, None, None)
                except Exception:
                    pass

    def _place_with_algo(self, db, ctx: OrderContext) -> Dict[str, Any]:
        """下单入口：解析 OrderAlgo，MARKET 直下，其余切片（阶段 3.2 接线）。

        切片语义: 每个子单调用 paper_engine.place_order（同币自动加仓合并），
        片间按 delay_ms 等待；返回最后一次子单结果并附 algo 审计元数据。
        """
        algo = (ctx.algo or "MARKET").upper()
        if algo == "MARKET" or ctx.quantity <= 0:
            return self._engine.place_order(
                db=db,
                account_id=ctx.account_id,
                symbol=ctx.symbol,
                side=ctx.side,
                quantity=ctx.quantity,
                **ctx.to_paper_kwargs(),
            )

        from backend.services.exchange.algo_exec import build_algo_slices, execute_slices

        children, meta = build_algo_slices(
            ctx.quantity, algo, ctx.algo_config,
            # paper 无实时成交量/多venue报价 → 由 build_algo_slices 内部降级
        )
        if not children:
            return {"status": "rejected", "error": f"algo {algo} 切片为空", "algo": algo}
        if meta.get("fallback"):
            logger.warning(
                f"[AlgoExec][paper:{algo}] {ctx.symbol} {ctx.side} 降级: {meta['fallback']}"
            )

        engine = self._engine
        base_kw = ctx.to_paper_kwargs()

        def _place_slice(child_qty: float, _is_last: bool) -> Dict[str, Any]:
            return engine.place_order(
                db=db,
                account_id=ctx.account_id,
                symbol=ctx.symbol,
                side=ctx.side,
                quantity=child_qty,
                **base_kw,
            )

        logger.info(
            f"[AlgoExec][paper:{algo}] {ctx.symbol} {ctx.side} "
            f"parent_qty={ctx.quantity:.6f} slices={meta['slices']}"
        )
        out = execute_slices(
            children, _place_slice, log_prefix=f"[AlgoExec][paper:{algo}]",
        )

        # 汇总: 取最后一次成功子单的原始结果，附 algo 审计元数据
        last_ok = next(
            (r for r in reversed(out["results"]) if isinstance(r, dict)), None
        )
        if last_ok is None:
            return {
                "status": "error",
                "error": f"algo {algo} 全部子单失败: {out['errors']}",
                "algo": algo,
                "slices": out,
            }
        merged = dict(last_ok)
        merged["algo"] = algo
        merged["algo_meta"] = meta
        merged["slices_exec"] = out
        merged["filled_quantity"] = sum(
            float(r.get("quantity", 0) or 0) for r in out["results"]
            if isinstance(r, dict) and str(r.get("status", "")).lower() == "filled"
        ) or None
        return merged

    def _normalize_place_result(self, raw: Optional[Dict[str, Any]], ctx: OrderContext) -> OrderResult:
        """将 paper_engine.place_order 的异构返回规范化为 OrderResult。"""
        if not raw:
            return OrderResult(
                status="error",
                symbol=ctx.symbol,
                side=ctx.side,
                channel="paper",
                exchange=self._exchange,
                error="paper_engine 返回 None",
            )

        # 风控拦截路径（无 status 键，有 blocked=True）
        if raw.get("blocked"):
            return OrderResult(
                status="blocked",
                symbol=raw.get("symbol", ctx.symbol),
                side=raw.get("side", ctx.side),
                channel="paper",
                exchange=self._exchange,
                blocked_by=raw.get("blocked_by"),
                blocked_layer=raw.get("blocked_layer"),
                error=raw.get("reason"),
                raw=raw,
            )

        status = str(raw.get("status", "")).lower()

        if status == "filled":
            return OrderResult(
                status="filled",
                order_id=raw.get("order_id"),
                position_id=raw.get("position_id"),
                symbol=raw.get("symbol", ctx.symbol),
                side=raw.get("side", ctx.side),
                fill_price=raw.get("price"),
                filled_quantity=raw.get("quantity"),
                fee=raw.get("fee"),
                leverage=raw.get("leverage"),
                channel="paper",
                exchange=self._exchange,
                raw=raw,
            )

        if status == "pending":
            # 限价单挂单中
            return OrderResult(
                status="pending",
                order_id=raw.get("order_id"),
                symbol=raw.get("symbol", ctx.symbol),
                side=raw.get("side", ctx.side),
                fill_price=raw.get("price"),
                filled_quantity=raw.get("quantity"),
                channel="paper",
                exchange=self._exchange,
                raw=raw,
            )

        if status == "rejected":
            return OrderResult(
                status="rejected",
                symbol=raw.get("symbol", ctx.symbol),
                side=raw.get("side", ctx.side),
                channel="paper",
                exchange=self._exchange,
                error=raw.get("error", "rejected"),
                raw=raw,
            )

        # 未知 status
        return OrderResult(
            status="error",
            symbol=raw.get("symbol", ctx.symbol),
            side=raw.get("side", ctx.side),
            channel="paper",
            exchange=self._exchange,
            error=f"未知 status: {status}",
            raw=raw,
        )

    # ── 平仓 ────────────────────────────────────────────────────

    def close_position(
        self, db, account_id: int, symbol: str, side: str,
        reason: str = "manual", quantity: Optional[float] = None,
        strategy_id: Optional[str] = None,
    ) -> OrderResult:
        """平仓 —— 委托 paper_engine.close_position。

        Args:
            side: 持仓方向 "long"/"short"（仓位方向，非订单方向）
        """
        try:
            raw = self._engine.close_position(
                db=db,
                account_id=account_id,
                symbol=symbol,
                side=side,
                reason=reason,
                quantity=quantity,
                strategy_id=strategy_id,
            )
            return self._normalize_close_result(raw, symbol, side)
        except Exception as e:
            logger.error(f"[PaperExecutor] close_position 异常: {e}", exc_info=True)
            return OrderResult(
                status="error",
                symbol=symbol,
                side=side,
                channel="paper",
                exchange=self._exchange,
                error=str(e),
            )

    def _normalize_close_result(
        self, raw: Optional[Dict[str, Any]], symbol: str, side: str,
    ) -> OrderResult:
        """规范化平仓返回。

        paper_engine.close_position 返回 None（无仓位）或 dict（含 closed_fully/pnl）。
        """
        if raw is None:
            return OrderResult(
                status="rejected",
                symbol=symbol,
                side=side,
                channel="paper",
                exchange=self._exchange,
                error="无匹配的开放仓位",
            )

        closed_fully = raw.get("closed_fully", True)
        return OrderResult(
            status="filled" if closed_fully else "partial",
            symbol=raw.get("symbol", symbol),
            side=raw.get("side", side),
            fill_price=raw.get("price"),
            filled_quantity=raw.get("quantity"),
            fee=raw.get("fee"),
            pnl=raw.get("pnl"),
            leverage=raw.get("leverage"),
            channel="paper",
            exchange=self._exchange,
            raw=raw,
        )

    # ── 查询 ────────────────────────────────────────────────────

    def get_positions(self, db, account_id: int, status: str = "open") -> List[Dict[str, Any]]:
        """查询持仓 —— 委托 paper_engine.get_positions（已注入 net_group_* 字段）。"""
        return self._engine.get_positions(db, account_id, status=status)

    def get_balance(self, db, account_id: int) -> Optional[Dict[str, Any]]:
        """查询余额 —— 委托 paper_engine.get_balance。"""
        return self._engine.get_balance(db, account_id)
