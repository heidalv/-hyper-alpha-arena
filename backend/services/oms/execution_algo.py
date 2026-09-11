# -*- coding: utf-8 -*-
"""ExecutionAlgo：maker 追价执行器（v3 方向 2，p2-oms-exec）。

把「怎么成交」从硬编码市价单，变成可调策略：

  1. 先落 `intent` 拿到 client_order_id（幂等键）
  2. post-only 限价挂最优买一/卖一
  3. 超时未成交 → 撤单 → 按追价步长重挂（最多 N 次）
  4. 仍不成交 → 按 `fallback` 策略：market / cancel / leave

**安全默认**：
  - `OMS_SHADOW=true`：整条流程走完，但**不真发单**（状态机写到 submitted/acked/filled 用影子标记）
  - `EXEC_ALGO_ENABLED=false`：现有 live 路径完全不碰本模块
  - 真正接管必须两个开关都显式打开

复用 AsterdexAdapter.place_order_maker_first 的经验（盘口挂单、部分成交补量、
TP/SL 后挂），但做成交易所无关、带状态机、带幂等键的通用层。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from backend.services.oms.client_id import new_client_order_id
from backend.services.oms.order_store import (
    OrderStatus,
    get_order,
    record_intent,
    shadow_mode,
    transition,
)

logger = logging.getLogger(__name__)


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return default


def algo_enabled() -> bool:
    """总开关。关着时现有 live 路径完全不走本模块。"""
    return _env_true("EXEC_ALGO_ENABLED", False)


@dataclass
class AlgoConfig:
    """一次执行的策略参数。未指定项读环境变量。"""

    chase_timeout_s: float = 8.0      # 单次挂单等待秒数
    max_chases: int = 3               # 最多追价次数（含首次）
    chase_step_bp: float = 1.0        # 每次追价向对手价靠拢的步长（bp）
    poll_interval_s: float = 1.5
    fallback: str = "market"          # market | cancel | leave
    post_only: bool = True

    @classmethod
    def from_env(cls, overrides: Optional[Dict[str, Any]] = None) -> "AlgoConfig":
        cfg = cls(
            chase_timeout_s=_env_float("EXEC_ALGO_CHASE_TIMEOUT_S", 8.0),
            max_chases=_env_int("EXEC_ALGO_MAX_CHASES", 3),
            chase_step_bp=_env_float("EXEC_ALGO_CHASE_STEP_BP", 1.0),
            poll_interval_s=_env_float("EXEC_ALGO_POLL_INTERVAL_S", 1.5),
            fallback=str(os.getenv("EXEC_ALGO_FALLBACK", "market") or "market").lower(),
            post_only=_env_true("EXEC_ALGO_POST_ONLY", True),
        )
        if overrides:
            for k, v in overrides.items():
                if hasattr(cfg, k) and v is not None:
                    setattr(cfg, k, v)
        if cfg.fallback not in ("market", "cancel", "leave"):
            cfg.fallback = "market"
        cfg.max_chases = max(1, min(int(cfg.max_chases), 10))
        cfg.chase_timeout_s = max(2.0, float(cfg.chase_timeout_s))
        return cfg


@dataclass
class AlgoRequest:
    account_id: int
    exchange: str
    symbol: str
    side: str                     # buy / sell
    qty: float
    order_type: str = "market"    # 意图类型；algo 会把它变成 limit+post_only
    price: Optional[float] = None
    reduce_only: bool = False
    leverage: int = 1
    position_side: Optional[str] = None
    tp: Optional[float] = None
    sl: Optional[float] = None
    strategy_id: Optional[str] = None
    client_order_id: Optional[str] = None   # 调用方也可自带；缺省本层生成
    config: Optional[AlgoConfig] = None
    intent_extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AlgoResult:
    ok: bool
    status: str
    client_order_id: str
    exchange_order_id: Optional[str] = None
    filled_qty: float = 0.0
    avg_price: Optional[float] = None
    maker: bool = False
    partial_maker: bool = False
    chase_count: int = 0
    shadow: bool = False
    message: str = ""
    fee: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────── 盘口 / 精度 ───────────────────────────
async def _best_prices(client: Any, symbol: str) -> Optional[tuple]:
    """返回 (best_bid, best_ask)；盘口不可用 → None。"""
    try:
        book = await client.get_orderbook(symbol, depth=5)
        bids, asks = book.get("bids") or [], book.get("asks") or []
        if not bids or not asks:
            return None
        return float(bids[0][0]), float(asks[0][0])
    except Exception as exc:
        logger.warning("[oms.algo] 盘口读取失败 %s: %s", symbol, exc)
        return None


def _unify_symbol(client: Any, symbol: str) -> str:
    if hasattr(client, "_swap_symbol"):
        try:
            return client._swap_symbol(symbol)
        except Exception:
            pass
    return symbol


def _quantize(client: Any, symbol: str, amount: float, price: Optional[float]) -> tuple:
    ex = getattr(client, "_exchange", None)
    amt, px = float(amount), price
    if ex is None:
        return amt, px
    try:
        amt = float(ex.amount_to_precision(symbol, amount))
    except Exception:
        pass
    if px is not None:
        try:
            px = float(ex.price_to_precision(symbol, px))
        except Exception:
            pass
    return amt, px


def _build_exchange_order(
    req: AlgoRequest, *, qty: float, price: Optional[float],
    order_type: str, post_only: bool, client_order_id: str, symbol: str,
) -> Any:
    from backend.services.exchange.base_exchange_client import ExchangeOrder, OrderSide, OrderType

    side = OrderSide.BUY if str(req.side).lower() in ("buy", "long") else OrderSide.SELL
    otype = OrderType.LIMIT if order_type == "limit" else OrderType.MARKET
    o = ExchangeOrder(
        order_id=client_order_id,
        symbol=symbol,
        side=side,
        order_type=otype,
        size=float(qty),
        price=price,
        sl=req.sl,
        tp=req.tp,
        post_only=bool(post_only),
        reduce_only=bool(req.reduce_only),
        position_side=req.position_side,
        leverage=int(req.leverage or 1),
        client_order_id=client_order_id,
    )
    return o


def _extract_fill(resp: Dict[str, Any]) -> Dict[str, Any]:
    """把各家 create_order / fetch_order 返回值归一成 filled/avg/oid/status。"""
    if not isinstance(resp, dict):
        return {"status": "error", "filled": 0.0, "avg": None, "oid": None, "message": "empty_response"}
    status = str(resp.get("status") or "").lower()
    oid = str(resp.get("order_id") or resp.get("id") or resp.get("orderId") or "") or None
    try:
        filled = float(resp.get("filled") or resp.get("qty") or resp.get("amount") or 0)
    except (TypeError, ValueError):
        filled = 0.0
    try:
        avg = resp.get("average") or resp.get("avg_price") or resp.get("price")
        avg = float(avg) if avg is not None else None
    except (TypeError, ValueError):
        avg = None
    msg = str(resp.get("message") or resp.get("info") or "")[:400]
    return {"status": status, "filled": filled, "avg": avg, "oid": oid,
            "maker": bool(resp.get("maker")), "partial_maker": bool(resp.get("partial_maker")),
            "message": msg, "raw": resp}


# ─────────────────────────── 核心执行 ───────────────────────────
async def execute(client: Any, req: AlgoRequest) -> AlgoResult:
    """执行一笔意图。client 必须是已初始化的 CcxtBaseAdapter 子类。

    流程：intent 落库 → （影子模式直接模拟终态）→ maker 追价 → 兜底 → 状态机落终态。
    """
    cfg = req.config or AlgoConfig.from_env()
    cid = req.client_order_id or new_client_order_id(account_id=req.account_id)
    is_shadow = shadow_mode()
    is_buy = str(req.side).lower() in ("buy", "long")
    sym = _unify_symbol(client, req.symbol)

    intent_payload = {
        "algo": "maker_chase",
        "config": asdict(cfg),
        "tp": req.tp, "sl": req.sl,
        "position_side": req.position_side,
        **(req.intent_extra or {}),
    }
    if not record_intent(
        client_order_id=cid, account_id=req.account_id, exchange=req.exchange,
        symbol=req.symbol, side="buy" if is_buy else "sell",
        order_type=req.order_type, qty=float(req.qty), price=req.price,
        reduce_only=req.reduce_only, post_only=cfg.post_only,
        leverage=req.leverage, strategy_id=req.strategy_id,
        shadow=is_shadow, intent=intent_payload,
    ):
        return AlgoResult(ok=False, status="rejected", client_order_id=cid,
                          message="intent 落库失败，拒发单（防止无账本裸单）")

    if is_shadow:
        return await _shadow_fill(client, req, cid, cfg, sym, is_buy)

    transition(cid, OrderStatus.SUBMITTED)
    return await _live_chase(client, req, cid, cfg, sym, is_buy)


async def _shadow_fill(client: Any, req: AlgoRequest, cid: str,
                       cfg: AlgoConfig, sym: str, is_buy: bool) -> AlgoResult:
    """影子模式：读真实盘口估一个成交价，状态机走完整生命周期，**不发单**。"""
    transition(cid, OrderStatus.SUBMITTED)
    book = await _best_prices(client, sym)
    px = None
    if book:
        px = book[0] if is_buy else book[1]
    elif req.price:
        px = float(req.price)
    transition(cid, OrderStatus.ACKED, exchange_order_id=f"shadow-{cid}")
    transition(cid, OrderStatus.FILLED, filled_qty=float(req.qty), avg_price=px)
    return AlgoResult(
        ok=True, status="filled", client_order_id=cid,
        exchange_order_id=f"shadow-{cid}", filled_qty=float(req.qty),
        avg_price=px, maker=True, chase_count=0, shadow=True,
        message="OMS_SHADOW=true：未真实发单，状态机已走完整路径",
    )


async def _live_chase(client: Any, req: AlgoRequest, cid: str,
                      cfg: AlgoConfig, sym: str, is_buy: bool) -> AlgoResult:
    """真实追价。每一步都写状态机；失联标 unknown 而不是猜。"""
    remaining = float(req.qty)
    filled_total = 0.0
    notional = 0.0
    last_oid: Optional[str] = None
    chase_count = 0
    maker_qty = 0.0

    for chase in range(int(cfg.max_chases)):
        chase_count = chase + 1
        book = await _best_prices(client, sym)
        if not book:
            logger.warning("[oms.algo] %s 盘口不可用，跳过追价直接兜底", sym)
            break
        best_bid, best_ask = book
        # 首次挂最优；之后每次向对手价靠拢 chase_step_bp
        mid = (best_bid + best_ask) / 2.0
        step = mid * (cfg.chase_step_bp / 1e4) * chase
        if is_buy:
            limit_px = best_bid + step
            # 不能穿过 ask（否则 post-only 被拒）
            limit_px = min(limit_px, best_ask - mid * 1e-6)
        else:
            limit_px = best_ask - step
            limit_px = max(limit_px, best_bid + mid * 1e-6)

        amount, limit_px = _quantize(client, sym, remaining, limit_px)
        if amount <= 0 or limit_px is None or limit_px <= 0:
            break

        # 追价单用独立子 id（父单 cid 保留意图；子单 = cid + 序号，仍 ≤28）
        child_cid = cid if chase == 0 else f"{cid}{chase}"[:28]
        if chase > 0:
            record_intent(
                client_order_id=child_cid, account_id=req.account_id, exchange=req.exchange,
                symbol=req.symbol, side="buy" if is_buy else "sell",
                order_type="limit", qty=amount, price=limit_px,
                reduce_only=req.reduce_only, post_only=True,
                leverage=req.leverage, strategy_id=req.strategy_id,
                parent_id=cid, chase_seq=chase, shadow=False,
                intent={"parent": cid, "chase": chase, "algo": "maker_chase"},
            )
            transition(child_cid, OrderStatus.SUBMITTED)

        order = _build_exchange_order(
            req, qty=amount, price=limit_px, order_type="limit",
            post_only=cfg.post_only, client_order_id=child_cid, symbol=sym,
        )
        try:
            resp = await client.place_order(order)
        except Exception as exc:
            logger.warning("[oms.algo] place_order 异常 cid=%s: %s", child_cid, exc)
            transition(child_cid, OrderStatus.UNKNOWN, error=str(exc)[:400])
            if chase == 0:
                transition(cid, OrderStatus.UNKNOWN, error=str(exc)[:400])
            # 未知态：不继续追价（可能已成交），交给对账
            return AlgoResult(ok=False, status="unknown", client_order_id=cid,
                              chase_count=chase_count, message=f"发单后失联: {exc}"[:200])

        fill = _extract_fill(resp)
        if fill["status"] == "error":
            # post-only 被拒（价格已穿透）→ 下一轮追价或兜底
            transition(child_cid if chase > 0 else cid, OrderStatus.REJECTED,
                       error=fill["message"][:400])
            logger.info("[oms.algo] post-only 被拒 chase=%d: %s", chase, fill["message"][:120])
            continue

        oid = fill["oid"]
        last_oid = oid or last_oid
        transition(child_cid if chase > 0 else cid, OrderStatus.ACKED,
                   exchange_order_id=oid)

        # 轮询等待
        deadline = time.time() + cfg.chase_timeout_s
        polled_filled = float(fill["filled"] or 0)
        polled_avg = fill["avg"]
        terminal = fill["status"] in ("closed", "filled", "canceled", "cancelled",
                                      "expired", "rejected")
        while not terminal and time.time() < deadline:
            await asyncio.sleep(max(0.4, cfg.poll_interval_s))
            if not oid:
                break
            try:
                st = await client._exchange.fetch_order(oid, sym)
                info = _extract_fill(st if isinstance(st, dict) else {})
                polled_filled = info["filled"]
                polled_avg = info["avg"] or polled_avg
                if info["status"] in ("closed", "filled", "canceled", "cancelled",
                                      "expired", "rejected"):
                    terminal = True
                    break
                if polled_filled > 0:
                    transition(child_cid if chase > 0 else cid, OrderStatus.PARTIAL,
                               filled_qty=polled_filled + filled_total,
                               avg_price=polled_avg, exchange_order_id=oid)
            except Exception:
                continue

        # 超时撤余量
        if not terminal and oid:
            try:
                await client.cancel_order(oid, sym)
            except Exception:
                pass
            try:
                st = await client._exchange.fetch_order(oid, sym)
                info = _extract_fill(st if isinstance(st, dict) else {})
                polled_filled = info["filled"]
                polled_avg = info["avg"] or polled_avg
            except Exception:
                pass

        got = max(float(polled_filled or 0), 0.0)
        if got > 0:
            filled_total += got
            notional += got * float(polled_avg or limit_px or 0)
            maker_qty += got
            remaining = max(float(req.qty) - filled_total, 0.0)
            if remaining <= 1e-9:
                avg = notional / filled_total if filled_total > 0 else polled_avg
                transition(cid, OrderStatus.FILLED, exchange_order_id=last_oid,
                           filled_qty=filled_total, avg_price=avg)
                if chase > 0:
                    transition(child_cid, OrderStatus.FILLED, exchange_order_id=oid,
                               filled_qty=got, avg_price=polled_avg)
                return AlgoResult(
                    ok=True, status="filled", client_order_id=cid,
                    exchange_order_id=last_oid, filled_qty=filled_total,
                    avg_price=avg, maker=True, chase_count=chase_count,
                )
            # 部分成交：子单标 cancelled（余量已撤），父单继续追
            transition(child_cid if chase > 0 else cid, OrderStatus.PARTIAL,
                       exchange_order_id=oid, filled_qty=filled_total,
                       avg_price=notional / filled_total if filled_total else polled_avg)
        else:
            transition(child_cid if chase > 0 else cid, OrderStatus.CANCELLED,
                       exchange_order_id=oid, error="chase_timeout_unfilled")

    # ── 兜底 ──
    return await _fallback(client, req, cid, cfg, sym, is_buy,
                           remaining, filled_total, notional, maker_qty, last_oid, chase_count)


async def _fallback(client: Any, req: AlgoRequest, cid: str, cfg: AlgoConfig,
                    sym: str, is_buy: bool, remaining: float, filled_total: float,
                    notional: float, maker_qty: float, last_oid: Optional[str],
                    chase_count: int) -> AlgoResult:
    if remaining <= 1e-9 and filled_total > 0:
        avg = notional / filled_total
        transition(cid, OrderStatus.FILLED, exchange_order_id=last_oid,
                   filled_qty=filled_total, avg_price=avg)
        return AlgoResult(ok=True, status="filled", client_order_id=cid,
                          exchange_order_id=last_oid, filled_qty=filled_total,
                          avg_price=avg, maker=True, chase_count=chase_count)

    if cfg.fallback == "leave":
        st = OrderStatus.PARTIAL if filled_total > 0 else OrderStatus.CANCELLED
        avg = (notional / filled_total) if filled_total > 0 else None
        transition(cid, st, exchange_order_id=last_oid, filled_qty=filled_total,
                   avg_price=avg, error="fallback=leave")
        return AlgoResult(ok=filled_total > 0, status=st, client_order_id=cid,
                          exchange_order_id=last_oid, filled_qty=filled_total,
                          avg_price=avg, maker=filled_total > 0, partial_maker=filled_total > 0,
                          chase_count=chase_count, message="fallback=leave：保留已成交，余量不补")

    if cfg.fallback == "cancel":
        transition(cid, OrderStatus.CANCELLED if filled_total <= 0 else OrderStatus.PARTIAL,
                   exchange_order_id=last_oid, filled_qty=filled_total,
                   avg_price=(notional / filled_total) if filled_total > 0 else None,
                   error="fallback=cancel")
        return AlgoResult(ok=filled_total > 0, status="cancelled" if filled_total <= 0 else "partial",
                          client_order_id=cid, exchange_order_id=last_oid,
                          filled_qty=filled_total,
                          avg_price=(notional / filled_total) if filled_total > 0 else None,
                          maker=filled_total > 0, partial_maker=filled_total > 0,
                          chase_count=chase_count, message="fallback=cancel：不补市价")

    # fallback=market：市价补足
    amount, _ = _quantize(client, sym, remaining, None)
    if amount <= 0:
        avg = (notional / filled_total) if filled_total > 0 else None
        transition(cid, OrderStatus.FILLED if filled_total > 0 else OrderStatus.CANCELLED,
                   filled_qty=filled_total, avg_price=avg, exchange_order_id=last_oid)
        return AlgoResult(ok=filled_total > 0, status="filled" if filled_total > 0 else "cancelled",
                          client_order_id=cid, filled_qty=filled_total, avg_price=avg,
                          maker=filled_total > 0, chase_count=chase_count)

    taker_cid = f"{cid}t"[:28]
    record_intent(
        client_order_id=taker_cid, account_id=req.account_id, exchange=req.exchange,
        symbol=req.symbol, side="buy" if is_buy else "sell",
        order_type="market", qty=amount, reduce_only=req.reduce_only,
        leverage=req.leverage, strategy_id=req.strategy_id,
        parent_id=cid, chase_seq=chase_count, shadow=False,
        intent={"parent": cid, "fallback": "market"},
    )
    transition(taker_cid, OrderStatus.SUBMITTED)
    order = _build_exchange_order(
        req, qty=amount, price=None, order_type="market",
        post_only=False, client_order_id=taker_cid, symbol=sym,
    )
    try:
        resp = await client.place_order(order)
    except Exception as exc:
        transition(taker_cid, OrderStatus.UNKNOWN, error=str(exc)[:400])
        if filled_total > 0:
            avg = notional / filled_total
            transition(cid, OrderStatus.PARTIAL, filled_qty=filled_total, avg_price=avg,
                       exchange_order_id=last_oid, error=f"taker_failed: {exc}"[:300])
            return AlgoResult(ok=True, status="partial", client_order_id=cid,
                              filled_qty=filled_total, avg_price=avg, maker=True,
                              partial_maker=True, chase_count=chase_count,
                              message=f"市价补量失败，保留 maker 成交: {exc}"[:200])
        transition(cid, OrderStatus.UNKNOWN, error=str(exc)[:400])
        return AlgoResult(ok=False, status="unknown", client_order_id=cid,
                          chase_count=chase_count, message=f"市价兜底失败: {exc}"[:200])

    fill = _extract_fill(resp)
    if fill["status"] == "error":
        transition(taker_cid, OrderStatus.REJECTED, error=fill["message"][:400])
        if filled_total > 0:
            avg = notional / filled_total
            transition(cid, OrderStatus.PARTIAL, filled_qty=filled_total, avg_price=avg,
                       exchange_order_id=last_oid, error=fill["message"][:300])
            return AlgoResult(ok=True, status="partial", client_order_id=cid,
                              filled_qty=filled_total, avg_price=avg, maker=True,
                              partial_maker=True, chase_count=chase_count,
                              message=f"市价补量被拒: {fill['message']}"[:200])
        transition(cid, OrderStatus.REJECTED, error=fill["message"][:400])
        return AlgoResult(ok=False, status="rejected", client_order_id=cid,
                          chase_count=chase_count, message=fill["message"][:200])

    t_qty = float(fill["filled"] or amount)
    t_px = float(fill["avg"] or 0) or None
    transition(taker_cid, OrderStatus.FILLED, exchange_order_id=fill["oid"],
               filled_qty=t_qty, avg_price=t_px)
    filled_total += t_qty
    if t_px:
        notional += t_qty * t_px
    avg = notional / filled_total if filled_total > 0 else t_px
    transition(cid, OrderStatus.FILLED, exchange_order_id=fill["oid"] or last_oid,
               filled_qty=filled_total, avg_price=avg)
    return AlgoResult(
        ok=True, status="filled", client_order_id=cid,
        exchange_order_id=fill["oid"] or last_oid, filled_qty=filled_total,
        avg_price=avg, maker=False, partial_maker=maker_qty > 0,
        chase_count=chase_count,
        message="maker 未完全成交，市价补足",
        raw={"maker_qty": maker_qty, "taker_qty": t_qty},
    )


def execute_sync(client: Any, req: AlgoRequest) -> AlgoResult:
    """同步包装：给非 async 调用方（trading_commands / LiveExecutor）用。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 已在事件循环里：开新线程跑，避免嵌套
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(execute(client, req))).result(timeout=180)
        return loop.run_until_complete(execute(client, req))
    except RuntimeError:
        return asyncio.run(execute(client, req))


def recover_stuck(*, older_than_sec: float = 300.0) -> Dict[str, Any]:
    """捞回卡在 intent/submitted/unknown 太久的单。

    本函数**只标记与告警**，不擅自撤单/补单——那是对账模块的事。
    目的是让悬挂单从"没人看"变成"看板可见"。
    """
    from backend.services.oms.order_store import stuck_orders

    rows = stuck_orders(older_than_sec=older_than_sec)
    out = {"n": len(rows), "ids": [r["client_order_id"] for r in rows[:50]],
           "by_status": {}}
    for r in rows:
        st = str(r.get("status"))
        out["by_status"][st] = out["by_status"].get(st, 0) + 1
    if rows:
        logger.warning("[oms.algo] 发现 %d 笔悬挂单（>%ss）: %s",
                       len(rows), older_than_sec, out["by_status"])
    return out
