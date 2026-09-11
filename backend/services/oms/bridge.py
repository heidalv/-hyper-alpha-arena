# -*- coding: utf-8 -*-
"""OMS 与现有 live 下单路径的桥接（v3 方向 2，p2-oms-exec）。

设计目标：**默认零行为改变**，但一旦开关打开就立刻有完整账本。

  OMS_RECORD_ORDERS=true（默认）
      现有 `_place_order_fresh_client` 路径：下单前生成 client_order_id、落 intent、
      成交后写终态。**不改变**下单方式（仍是市价 / 既有 maker_first）。
  EXEC_ALGO_ENABLED=true（默认 false）
      改走 ExecutionAlgo maker 追价；OMS_SHADOW=true 时只走状态机不发真单。

任何异常都回退到原路径，绝不因为 OMS 本身故障阻断交易。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _env_true(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def record_orders_enabled() -> bool:
    return _env_true("OMS_RECORD_ORDERS", True)


def attach_client_order_id(order: Any, *, account_id: int) -> Optional[str]:
    """给 ExchangeOrder 挂上 client_order_id（已有则保留）。返回最终 id。"""
    try:
        existing = getattr(order, "client_order_id", None)
        if existing:
            return str(existing)
        from backend.services.oms.client_id import new_client_order_id

        cid = new_client_order_id(account_id=account_id)
        try:
            order.client_order_id = cid
        except Exception:
            # dataclass frozen 等极端情况：忽略，下游可能拿不到
            pass
        if not getattr(order, "order_id", None):
            try:
                order.order_id = cid
            except Exception:
                pass
        return cid
    except Exception as exc:
        logger.debug("[oms.bridge] attach cid 失败: %s", exc)
        return None


def _decision_ts_from(order: Any) -> Optional[float]:
    """从 order 上取决策时间戳（毫秒）。决策产生时由调用方打在 order.trigger_context
    或 decision 上；取不到返回 None（该订单不记 decision→intent 段）。"""
    for attr in ("decision_ts", "decision_ts_ms"):
        v = getattr(order, attr, None)
        if v:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    tc = getattr(order, "trigger_context", None)
    if isinstance(tc, dict):
        for k in ("decision_ts", "decision_ts_ms", "ts_decision"):
            if tc.get(k):
                try:
                    return float(tc[k])
                except (TypeError, ValueError):
                    pass
    return None


def begin_order(order: Any, *, account_id: int, exchange: str,
                strategy_id: Optional[str] = None) -> Optional[str]:
    """下单前：挂幂等键 + 落 intent + 标 submitted。失败返回 None（调用方继续原路径）。"""
    if not record_orders_enabled():
        return attach_client_order_id(order, account_id=account_id)
    try:
        from backend.services.oms.order_store import OrderStatus, record_intent, transition

        cid = attach_client_order_id(order, account_id=account_id)
        if not cid:
            return None
        side = getattr(getattr(order, "side", None), "value", None) or getattr(order, "side", "")
        otype = getattr(getattr(order, "order_type", None), "value", None) or getattr(order, "order_type", "")
        ok = record_intent(
            client_order_id=cid, account_id=account_id, exchange=str(exchange),
            symbol=str(getattr(order, "symbol", "")),
            side=str(side).lower(), order_type=str(otype).lower(),
            qty=float(getattr(order, "size", 0) or 0),
            price=getattr(order, "price", None),
            reduce_only=bool(getattr(order, "reduce_only", False)),
            post_only=bool(getattr(order, "post_only", False)),
            leverage=int(getattr(order, "leverage", 1) or 1),
            strategy_id=strategy_id,
            shadow=False,
            intent={"bridge": "place_order_fresh_client",
                    "decision_ts": getattr(order, "decision_ts", None) or _decision_ts_from(order)},
        )
        if ok:
            transition(cid, OrderStatus.SUBMITTED)
        return cid
    except Exception as exc:
        logger.warning("[oms.bridge] begin_order 失败（继续原路径）: %s", exc)
        return getattr(order, "client_order_id", None)


def finish_order(cid: Optional[str], result: Any, *, error: Optional[str] = None) -> None:
    """下单后：按结果写终态。任何异常吞掉，不影响主流程。"""
    if not cid or not record_orders_enabled():
        return
    try:
        from backend.services.oms.order_store import OrderStatus, transition

        if error:
            transition(cid, OrderStatus.REJECTED, error=str(error)[:400])
            return
        if not isinstance(result, dict):
            transition(cid, OrderStatus.UNKNOWN, error="result_not_dict")
            return
        status = str(result.get("status") or "").lower()
        if status == "error":
            transition(cid, OrderStatus.REJECTED, error=str(result.get("message") or "")[:400])
            return
        oid = str(result.get("order_id") or result.get("id") or result.get("orderId") or "") or None
        try:
            filled = float(result.get("filled") or result.get("qty") or result.get("amount") or 0)
        except (TypeError, ValueError):
            filled = 0.0
        try:
            avg = result.get("average") or result.get("price")
            avg = float(avg) if avg is not None else None
        except (TypeError, ValueError):
            avg = None
        if oid:
            transition(cid, OrderStatus.ACKED, exchange_order_id=oid)
        if filled > 0 or status in ("closed", "filled"):
            transition(cid, OrderStatus.FILLED, exchange_order_id=oid,
                       filled_qty=filled or None, avg_price=avg)
        elif status in ("canceled", "cancelled"):
            transition(cid, OrderStatus.CANCELLED, exchange_order_id=oid)
        else:
            # 已发出但结果含糊 → unknown，交给对账
            transition(cid, OrderStatus.UNKNOWN, exchange_order_id=oid,
                       error=f"ambiguous_status={status}")
    except Exception as exc:
        logger.warning("[oms.bridge] finish_order 失败 cid=%s: %s", cid, exc)


def try_algo_place(client: Any, order: Any, *, account_id: int, exchange: str,
                   strategy_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """若 EXEC_ALGO_ENABLED：走 maker 追价，返回与 place_order 同构的 dict；否则返回 None。"""
    try:
        from backend.services.oms.execution_algo import (
            AlgoConfig,
            AlgoRequest,
            algo_enabled,
            execute_sync,
        )

        if not algo_enabled():
            return None
        side = getattr(getattr(order, "side", None), "value", None) or getattr(order, "side", "")
        req = AlgoRequest(
            account_id=account_id, exchange=exchange,
            symbol=str(getattr(order, "symbol", "")),
            side=str(side).lower(),
            qty=float(getattr(order, "size", 0) or 0),
            order_type=str(getattr(getattr(order, "order_type", None), "value",
                                   getattr(order, "order_type", "market")) or "market").lower(),
            price=getattr(order, "price", None),
            reduce_only=bool(getattr(order, "reduce_only", False)),
            leverage=int(getattr(order, "leverage", 1) or 1),
            position_side=getattr(order, "position_side", None),
            tp=getattr(order, "tp", None),
            sl=getattr(order, "sl", None),
            strategy_id=strategy_id,
            client_order_id=getattr(order, "client_order_id", None),
            config=AlgoConfig.from_env(),
        )
        res = execute_sync(client, req)
        if not res.ok and res.status in ("rejected", "unknown"):
            return {"status": "error", "message": res.message or res.status,
                    "client_order_id": res.client_order_id}
        return {
            "status": "filled" if res.status == "filled" else res.status,
            "maker": res.maker, "partial_maker": res.partial_maker,
            "price": res.avg_price, "average": res.avg_price,
            "qty": res.filled_qty, "amount": res.filled_qty, "filled": res.filled_qty,
            "order_id": res.exchange_order_id, "id": res.exchange_order_id,
            "client_order_id": res.client_order_id,
            "exchange": exchange, "shadow": res.shadow, "message": res.message,
        }
    except Exception as exc:
        logger.warning("[oms.bridge] algo 路径失败，回退原路径: %s", exc)
        return None
