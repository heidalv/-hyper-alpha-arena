"""
引擎层硬风控（整改#5）—— 业务无关，对标 NautilusTrader RiskEngine。

位于订单提交路径最后一道防线（live_executor / paper_executor 之前），做纯粹的
规格/数量/名义/精度/限流/重复/交易状态校验，返回标准化 OrderDenied 原因码。

零风险接入约定：
  - 由 RISK_ENGINE_ENABLED 环境变量门控，默认关闭 → check_submit 直接透传 None（放行）。
  - 未登记 InstrumentSpec 的品种不做规格类校验（只做状态/限流/重复），避免误杀。
  - 校验为纯函数式，不产生任何副作用（限流/重复窗口除外，且可 reset）。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class DenyCategory(Enum):
    QUANTITY_BELOW_MINIMUM = "quantity_below_minimum"
    QUANTITY_EXCEEDS_MAXIMUM = "quantity_exceeds_maximum"
    QUANTITY_PRECISION_INVALID = "quantity_precision_invalid"
    PRICE_PRECISION_INVALID = "price_precision_invalid"
    NOTIONAL_BELOW_MINIMUM = "notional_below_minimum"
    NOTIONAL_EXCEEDS_MAXIMUM = "notional_exceeds_maximum"
    NOTIONAL_EXCEEDS_MAX_PER_ORDER = "notional_exceeds_max_per_order"
    MARGIN_EXCEEDS_FREE_BALANCE = "margin_exceeds_free_balance"
    REDUCE_ONLY_WOULD_INCREASE = "reduce_only_would_increase_position"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    TRADING_HALTED = "trading_halted"
    TRADING_REDUCING_ONLY = "trading_state_reducing"
    DUPLICATE_ORDER = "duplicate_order"
    INSTRUMENT_NOT_FOUND = "instrument_not_found"
    INVALID_ORDER_SIDE = "invalid_order_side"


class TradingState(Enum):
    ACTIVE = "active"
    HALTED = "halted"          # 拒绝新单，允许撤单
    REDUCING = "reducing"      # 仅允许减仓


@dataclass
class OrderDenied:
    """标准化拒绝事件，对标 NautilusTrader OrderDenied。"""
    category: DenyCategory
    context: Dict[str, float] = field(default_factory=dict)
    reason_text: str = ""

    def log_line(self) -> str:
        ctx = " ".join(f"{k}={v}" for k, v in self.context.items())
        return f"[RiskEngine] DENY category={self.category.value} {ctx} {self.reason_text}".strip()


@dataclass
class InstrumentSpec:
    """交易品种规格（从交易所拉取/缓存）。"""
    symbol: str
    price_precision: int = 8
    quantity_precision: int = 8
    min_quantity: float = 0.0
    max_quantity: float = float("inf")
    min_notional: float = 0.0
    max_notional: float = float("inf")
    tick_size: float = 0.0


@dataclass
class OrderRequest:
    """引擎无关的下单请求视图。"""
    symbol: str
    side: str                      # 'buy'|'sell'|'long'|'short'
    quantity: float
    price: Optional[float] = None
    notional: Optional[float] = None
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    trigger_price: Optional[float] = None
    max_notional_per_order: Optional[float] = None


def _precision_ok(value: float, precision: int) -> bool:
    """value 是否可用 `precision` 位小数精确表示（容忍浮点误差）。"""
    if value is None:
        return True
    scaled = value * (10 ** precision)
    return abs(scaled - round(scaled)) < 1e-6


class RiskEngine:
    """引擎层风控 —— 在订单提交前校验。"""

    _VALID_SIDES = {"buy", "sell", "long", "short"}

    def __init__(self, enabled: Optional[bool] = None, max_submits_per_sec: Optional[int] = None):
        self.enabled = _env_bool("RISK_ENGINE_ENABLED", False) if enabled is None else enabled
        self.trading_state = TradingState.ACTIVE
        self.specs: Dict[str, InstrumentSpec] = {}
        self._submit_timestamps: List[float] = []
        self._max_submits_per_sec = (
            _env_int("RISK_MAX_SUBMITS_PER_SEC", 10) if max_submits_per_sec is None else max_submits_per_sec
        )
        self._seen_client_order_ids: set = set()
        self._seen_fill_ids: set = set()
        self._allow_overfills = _env_bool("RISK_ALLOW_OVERFILLS", False)
        # 统计（供前端风控面板）
        self.deny_counts: Dict[str, int] = {}

    # ---------- 规格管理 ----------
    def register_spec(self, spec: InstrumentSpec) -> None:
        self.specs[spec.symbol] = spec

    def set_trading_state(self, state: TradingState) -> None:
        self.trading_state = state

    # ---------- 提交校验 ----------
    def check_submit(self, order_request: OrderRequest, account_state: Optional[dict] = None,
                     position_state: Optional[dict] = None) -> Optional[OrderDenied]:
        """返回 None=通过，OrderDenied=拒绝。禁用时透传 None。"""
        if not self.enabled:
            return None
        account_state = account_state or {}
        position_state = position_state or {}
        req = order_request

        # 1. 交易状态
        if self.trading_state == TradingState.HALTED:
            return self._deny(DenyCategory.TRADING_HALTED, {}, "trading halted")
        if self.trading_state == TradingState.REDUCING and not req.reduce_only:
            return self._deny(DenyCategory.TRADING_REDUCING_ONLY, {}, "only reduce-only allowed")

        # 2. 订单方向
        if str(req.side).lower() not in self._VALID_SIDES:
            return self._deny(DenyCategory.INVALID_ORDER_SIDE, {}, f"side={req.side}")

        # 3. 限流
        now = time.time()
        self._submit_timestamps = [t for t in self._submit_timestamps if now - t < 1.0]
        if len(self._submit_timestamps) >= self._max_submits_per_sec:
            return self._deny(DenyCategory.RATE_LIMIT_EXCEEDED,
                              {"submits_last_sec": len(self._submit_timestamps),
                               "max": self._max_submits_per_sec}, "rate limit")

        # 4. 重复 client_order_id
        if req.client_order_id and req.client_order_id in self._seen_client_order_ids:
            return self._deny(DenyCategory.DUPLICATE_ORDER, {}, f"coid={req.client_order_id}")

        # 5. reduce_only 不增仓
        if req.reduce_only:
            deny = self._check_reduce_only(req, position_state)
            if deny:
                return deny

        # 6~8. 规格类校验（仅当登记了 spec）
        spec = self.specs.get(req.symbol)
        if spec is not None:
            deny = self._check_against_spec(req, spec)
            if deny:
                return deny

        # 9. per-order 名义上限
        notional = self._notional(req)
        if req.max_notional_per_order and notional is not None and notional > req.max_notional_per_order:
            return self._deny(DenyCategory.NOTIONAL_EXCEEDS_MAX_PER_ORDER,
                              {"notional": notional, "max_per_order": req.max_notional_per_order},
                              "notional exceeds per-order cap")

        # 10. 保证金 ≤ 自由余额
        free = account_state.get("free_balance")
        margin = account_state.get("required_margin")
        if free is not None and margin is not None and margin > float(free):
            return self._deny(DenyCategory.MARGIN_EXCEEDS_FREE_BALANCE,
                              {"required_margin": float(margin), "free_balance": float(free)},
                              "insufficient free balance")

        # 通过 → 记录窗口
        self._submit_timestamps.append(now)
        if req.client_order_id:
            self._seen_client_order_ids.add(req.client_order_id)
        return None

    def _check_reduce_only(self, req: OrderRequest, position_state: dict) -> Optional[OrderDenied]:
        pos_qty = float(position_state.get("quantity", 0.0) or 0.0)   # 正=多，负=空
        side = str(req.side).lower()
        opening_long = side in ("buy", "long")
        # reduce_only 但方向会加仓（无持仓，或同向）
        if pos_qty == 0:
            return self._deny(DenyCategory.REDUCE_ONLY_WOULD_INCREASE,
                              {"position": pos_qty}, "reduce-only with no position")
        if (opening_long and pos_qty > 0) or ((not opening_long) and pos_qty < 0):
            return self._deny(DenyCategory.REDUCE_ONLY_WOULD_INCREASE,
                              {"position": pos_qty, "side": side}, "reduce-only would increase position")
        return None

    def _check_against_spec(self, req: OrderRequest, spec: InstrumentSpec) -> Optional[OrderDenied]:
        qty = float(req.quantity)
        # 数量精度
        if not _precision_ok(qty, spec.quantity_precision):
            return self._deny(DenyCategory.QUANTITY_PRECISION_INVALID,
                              {"quantity": qty, "precision": spec.quantity_precision}, "qty precision")
        # 数量 min/max
        if qty < spec.min_quantity:
            return self._deny(DenyCategory.QUANTITY_BELOW_MINIMUM,
                              {"quantity": qty, "min": spec.min_quantity}, "qty below min")
        if qty > spec.max_quantity:
            return self._deny(DenyCategory.QUANTITY_EXCEEDS_MAXIMUM,
                              {"quantity": qty, "max": spec.max_quantity}, "qty above max")
        # 价格精度
        if req.price is not None and not _precision_ok(req.price, spec.price_precision):
            return self._deny(DenyCategory.PRICE_PRECISION_INVALID,
                              {"price": req.price, "precision": spec.price_precision}, "price precision")
        if req.trigger_price is not None and not _precision_ok(req.trigger_price, spec.price_precision):
            return self._deny(DenyCategory.PRICE_PRECISION_INVALID,
                              {"trigger_price": req.trigger_price}, "trigger precision")
        # 名义 min/max
        notional = self._notional(req)
        if notional is not None:
            if notional < spec.min_notional:
                return self._deny(DenyCategory.NOTIONAL_BELOW_MINIMUM,
                                  {"notional": notional, "min": spec.min_notional}, "notional below min")
            if notional > spec.max_notional:
                return self._deny(DenyCategory.NOTIONAL_EXCEEDS_MAXIMUM,
                                  {"notional": notional, "max": spec.max_notional}, "notional above max")
        return None

    @staticmethod
    def _notional(req: OrderRequest) -> Optional[float]:
        if req.notional is not None:
            return float(req.notional)
        if req.price is not None:
            return abs(float(req.quantity) * float(req.price))
        return None

    # ---------- 成交校验 ----------
    def check_fill(self, fill_event: dict, order_state: Optional[dict] = None) -> Optional[str]:
        """成交校验：超额成交保护 + 重复成交去重。返回 None=通过，否则原因字符串。"""
        if not self.enabled:
            return None
        order_state = order_state or {}
        # 4 字段去重（symbol, trade_id, price, qty）
        key = (fill_event.get("symbol"), fill_event.get("trade_id"),
               fill_event.get("price"), fill_event.get("quantity"))
        if fill_event.get("trade_id") is not None and key in self._seen_fill_ids:
            return "duplicate_fill"
        # 超额成交
        if not self._allow_overfills:
            filled = float(order_state.get("filled_quantity", 0.0) or 0.0)
            order_qty = float(order_state.get("order_quantity", 0.0) or 0.0)
            this_fill = float(fill_event.get("quantity", 0.0) or 0.0)
            if order_qty > 0 and (filled + this_fill) > order_qty * (1 + 1e-9):
                return "overfill_rejected"
        if fill_event.get("trade_id") is not None:
            self._seen_fill_ids.add(key)
        return None

    # ---------- 内部 ----------
    def _deny(self, category: DenyCategory, context: dict, reason: str) -> OrderDenied:
        self.deny_counts[category.value] = self.deny_counts.get(category.value, 0) + 1
        denied = OrderDenied(category=category, context=context, reason_text=reason)
        logger.info(denied.log_line())
        return denied

    def reset_windows(self) -> None:
        """清空限流/重复窗口（测试/长驻进程周期性清理用）。"""
        self._submit_timestamps.clear()
        self._seen_client_order_ids.clear()
        self._seen_fill_ids.clear()


# 模块级单例（对标 doc：exchange_factory 初始化时创建）
_risk_engine_singleton: Optional[RiskEngine] = None


def get_risk_engine() -> RiskEngine:
    global _risk_engine_singleton
    if _risk_engine_singleton is None:
        _risk_engine_singleton = RiskEngine()
    return _risk_engine_singleton
