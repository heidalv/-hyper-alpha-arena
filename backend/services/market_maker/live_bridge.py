# -*- coding: utf-8 -*-
"""[h665 2026-10-01] 实盘执行桥:mm 引擎期望报价 ↔ Asterdex 真实限价单。

[h666] 升级:Asterdex V3 原生客户端优先(EIP-712 钱包签名;2026-03-25 后新 Key
只有 V3,旧 Binance 兼容 HMAC 适配器作回退)。Chase 订单(BBO 自动贴单)留作
下一阶段优化,当前用 LIMIT GTX(post-only)挂引擎算出的价。

安全铁律(写死在代码里):
1. lane mode != 'live' 或 status != 'active' ⇒ 下单类操作全部 no-op(读可);
2. 未配置 API Key ⇒ bridge 不可用(no_op 模式);
3. 每次下单前过 check_caps(总名义/单币名义/日亏/下单熔断);
4. 只挂 post-only(GTX);减仓侧 reduce-only;价量变化超过 ε 才撤改(限频保护);
5. 429/418 限频 ⇒ 抛 AsterdexRateLimited,熔断计数 +1(不硬重试,防 IP 封禁);
6. 任何异常 → 记录、返回错误态,绝不让 tick 主循环崩溃。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_SYNC_INTERVAL_S = 1.0
PX_EPS_RATIO = 5e-5
QTY_EPS_RATIO = 0.15

# [h666] V3 三件套环境变量(user/signer/private_key)
V3_ENV = ("ASTERDEX_V3_USER", "ASTERDEX_V3_SIGNER", "ASTERDEX_V3_PRIVATE_KEY")


def _v3_keys() -> tuple:
    user = os.getenv("ASTERDEX_V3_USER", "").strip()
    signer = os.getenv("ASTERDEX_V3_SIGNER", "").strip()
    pk = os.getenv("ASTERDEX_V3_PRIVATE_KEY", "").strip()
    return user, signer, pk


def _keys_for(api_key: str = "", api_secret: str = "") -> tuple:
    """旧式(Binance 兼容 HMAC)Key 解析:显式参数 > 环境变量 > 凭据表。"""
    key = api_key or os.getenv("ASTERDEX_API_KEY", "")
    sec = api_secret or os.getenv("ASTERDEX_API_SECRET", "")
    if key and sec:
        return key, sec
    try:
        from backend.database.connection import get_db
        from backend.database.models import ExchangeCredential

        db = next(get_db())
        try:
            c = (db.query(ExchangeCredential)
                 .filter(ExchangeCredential.exchange == "asterdex",
                         ExchangeCredential.enabled == True)  # noqa: E712
                 .order_by(ExchangeCredential.account_id.is_(None).desc())
                 .first())
            if c:
                return (getattr(c, "api_key", "") or ""), (getattr(c, "api_secret", "") or "")
        finally:
            db.close()
    except Exception:
        pass
    return "", ""


def _resolve_caps(lane: dict) -> Dict[str, float]:
    meta = lane.get("meta") or {}
    caps = dict(meta.get("live_caps") or {})
    out = {
        "total_notional_usd": float(caps.get("total_notional_usd", 500.0) or 500.0),
        "per_symbol_usd": float(caps.get("per_symbol_usd", 150.0) or 150.0),
        "daily_loss_usd": float(caps.get("daily_loss_usd", 30.0) or 30.0),
        "reject_break": float(caps.get("reject_break", 3.0) or 3.0),
        "cooldown_s": float(caps.get("cooldown_s", 60.0) or 60.0),
        # [h673] 实盘杠杆:2× 只为把名义上限用满(权益<$500 时 1× 保证金不够),
        # 风险上限不变(仍以名义 cap 为准)。
        # [h822 2026-10-04 审计 C3 修复] **主动流不读这个 2×**:主动流的逐仓杠杆
        # 必须由"强平距离 ≥ 3× 灾难止损"算死(flow_rules.exchange_leverage),
        # 2× 是做市时代为把名义用满设的,不是单边持仓该承担的倍数。
        "leverage": float(caps.get("leverage", 2.0) or 2.0),
        # [h674 用户指令] **账户级限制随权益浮动**(绝对值只是天花板):
        "total_notional_mult_equity": float(caps.get("total_notional_mult_equity", 2.0) or 2.0),
        "per_symbol_mult_equity": float(caps.get("per_symbol_mult_equity", 0.5) or 0.5),
        "daily_loss_pct_equity": float(caps.get("daily_loss_pct_equity", 10.0) or 10.0),
    }
    # [h822] 主动流车道:杠杆按流规则逐笔算(强平 ≥3× 止损),不沿用做市的 2×。
    try:
        _params = (meta.get("params") or {})
        if float(_params.get("active_flow_mode") or 0.0) > 0:
            from backend.services.market_maker.flow_rules import (
                exchange_leverage, load_learn_params,
            )
            from pathlib import Path as _P
            _learn = load_learn_params(_P(os.getcwd()))
            _stop = float(_learn.get("disaster_stop_cap_bp") or 40.0)
            _lev = int(exchange_leverage(_stop, 0.01))
            out["leverage"] = float(max(1, _lev))
            out["leverage_source"] = "flow_exchange_leverage"
            out["flow_stop_bp"] = _stop
    except Exception:
        pass
    return out


def keys_ready() -> bool:
    """[h666] 实盘 Key 就绪检查:V3 三件套(user/signer/private_key)或旧式
    api_key/secret 二选一即可(新 Key 只有 V3)。"""
    u, s, pk = _v3_keys()
    if u and s and pk:
        return True
    k, sec = _keys_for()
    return bool(k and sec)


class LiveBridge:
    """每车道一个实例(由 runner 持有)。V3 优先,旧适配器回退。"""

    def __init__(self, lane: dict):
        self.lane_id = str(lane.get("lane_id") or "")
        self.lane = lane
        self.caps = _resolve_caps(lane)
        user, signer, pk = _v3_keys()
        key, sec = _keys_for()
        self._v3 = None
        if user and signer and pk:
            try:
                from backend.services.exchange.asterdex_v3_client import (
                    AsterdexV3Client,
                )
                self._v3 = AsterdexV3Client(user=user, signer=signer, private_key=pk)
            except Exception as e:
                logger.warning("[h666] V3 客户端初始化失败: %s", e)
        self.enabled = bool(self._v3 or (key and sec))
        self._key, self._sec = key, sec
        self._adapter = None
        self._desired: Dict[str, Dict[str, Any]] = {}
        self._order_ids: Dict[str, List[str]] = {}
        self._last_sync: Dict[str, float] = {}
        self._last_fill_ms: Dict[str, int] = {}
        self._lev_synced: Dict[str, float] = {}
        self._reject_streak = 0
        self._cooldown_until = 0.0
        self._day_start_equity: Optional[float] = None
        self._day_start_date: Optional[str] = None
        self.last_error = ""
        self.snapshot: Dict[str, Any] = {"enabled": self.enabled, "balance": None,
                                         "positions": [], "orders": [],
                                         "caps": self.caps, "cooldown": False,
                                         "reject_streak": 0, "v3": bool(self._v3)}

    # ── 适配器(旧式回退) ──────────────────────────────────────────
    def _get_adapter(self):
        if not (self._key and self._sec):
            return None
        if self._adapter is None:
            try:
                from backend.services.exchange.asterdex_adapter import AsterdexAdapter
                self._adapter = AsterdexAdapter(api_key=self._key, secret=self._sec)
            except Exception as e:
                self.last_error = f"adapter_init: {e}"
                logger.warning("[h665] 实盘适配器初始化失败: %s", e)
                return None
        return self._adapter

    def _run(self, coro):
        try:
            return asyncio.run(coro)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning("[h665] 实盘桥异步操作失败: %s", e)
            return None

    def _lane_tradeable(self) -> bool:
        if not self.enabled:
            return False
        if (self.lane.get("mode") or "") != "live":
            return False
        if (self.lane.get("status") or "") != "active":
            return False
        if time.time() < self._cooldown_until:
            return False
        return True

    # ── 风控 ──────────────────────────────────────────────────────
    def _effective_caps(self) -> Dict[str, float]:
        """[h674] 账户级有效上限 = min(绝对值天花板, 权益×倍数)。
        权益未取到 ⇒ 回退绝对值(保守=天花板本身就是兜底)。"""
        bal = self.snapshot.get("balance") or {}
        eq = float(bal.get("total_equity") or 0.0)
        eff = {
            "total": float(self.caps["total_notional_usd"]),
            "per_symbol": float(self.caps["per_symbol_usd"]),
            "daily_loss": float(self.caps["daily_loss_usd"]),
            "equity": eq,
        }
        if eq > 0:
            eff["total"] = min(eff["total"],
                               float(self.caps["total_notional_mult_equity"]) * eq)
            eff["per_symbol"] = min(eff["per_symbol"],
                                    float(self.caps["per_symbol_mult_equity"]) * eq)
            _pct = float(self.caps.get("daily_loss_pct_equity") or 0.0)
            if _pct > 0 and self._day_start_equity:
                eff["daily_loss"] = _pct / 100.0 * float(self._day_start_equity)
        return eff

    def check_caps(self, open_positions: List[Dict[str, Any]],
                   new_notional: float = 0.0,
                   symbol: str = "") -> tuple:
        """(ok, reason)。总敞口/单币(随权益)/日亏(随权益)/下单熔断。"""
        eff = self._effective_caps()
        pos_notional = sum(abs(float(p.get("notional") or 0.0)) for p in open_positions)
        if pos_notional + new_notional > eff["total"]:
            return False, f"total_notional_cap({pos_notional:+.1f}+{new_notional:.1f}>{eff['total']:.1f})"
        if symbol:
            sym_notional = sum(abs(float(p.get("notional") or 0.0))
                               for p in open_positions
                               if str(p.get("symbol") or "").upper()
                               == str(symbol).upper())
            if sym_notional + new_notional > eff["per_symbol"]:
                return False, f"per_symbol_cap({sym_notional:+.1f}+{new_notional:.1f}>{eff['per_symbol']:.1f})"
        if self._day_start_equity is not None:
            bal = self.snapshot.get("balance") or {}
            eq = float(bal.get("total_equity") or 0.0)
            if eq > 0 and (self._day_start_equity - eq) >= eff["daily_loss"]:
                return False, f"daily_loss_cap({self._day_start_equity:.1f}→{eq:.1f}≥{eff['daily_loss']:.1f})"
        if self._reject_streak >= int(self.caps["reject_break"]):
            return False, f"reject_break({self._reject_streak})"
        return True, ""

    def _note_reject(self, why: str) -> None:
        self._reject_streak += 1
        self.last_error = f"reject: {why}"
        if self._reject_streak >= int(self.caps["reject_break"]):
            self._cooldown_until = time.time() + float(self.caps["cooldown_s"])
            logger.warning("[h665] 实盘下单熔断:%s,冷却 %.0fs", why, self.caps["cooldown_s"])
        self.snapshot["reject_streak"] = self._reject_streak
        self.snapshot["cooldown"] = time.time() < self._cooldown_until

    def _note_ok(self) -> None:
        if self._reject_streak:
            self._reject_streak = 0
            self.snapshot["reject_streak"] = 0
            self.snapshot["cooldown"] = False

    # ── 只读:余额/持仓/挂单 ────────────────────────────────────────
    def refresh_account(self) -> Dict[str, Any]:
        if not self.enabled:
            self.snapshot["balance"] = None
            self.snapshot["positions"] = []
            self.snapshot["orders"] = []
            return self.snapshot
        # [h673] 杠杆对齐(2×,每小时重试一次,失败静默:不阻塞账务)
        _now = time.time()
        for _s in (self.lane.get("meta") or {}).get("symbols") or []:
            if _now - self._lev_synced.get(str(_s), 0.0) < 3600.0:
                continue
            try:
                if self._v3 is not None:
                    self._v3.set_leverage(f"{_s}USDT",
                                          int(self.caps.get("leverage", 2.0)))
                else:
                    _adp = self._get_adapter()
                    if _adp is not None:
                        self._run(_adp.set_leverage(
                            f"{str(_s).upper()}/USDT:USDT",
                            int(self.caps.get("leverage", 2.0))))
                self._lev_synced[str(_s)] = _now
            except Exception as e:
                logger.warning("[h673] 杠杆对齐失败 %s: %s", _s, e)
        bal = poss = ords = None
        if self._v3 is not None:
            try:
                acc = self._v3.account()
                bal = {
                    "total_equity": float(acc.get("totalMarginBalance") or 0.0),
                    "available_balance": float(acc.get("availableBalance") or 0.0),
                    "unrealized_pnl": float(acc.get("totalUnrealizedProfit") or 0.0),
                }
            except Exception as e:
                logger.warning("[h666] V3 account 失败: %s", e)
            try:
                poss = self._v3.positions()
            except Exception as e:
                logger.warning("[h666] V3 positions 失败: %s", e)
            try:
                ords = self._v3.open_orders()
            except Exception as e:
                logger.warning("[h666] V3 openOrders 失败: %s", e)
        else:
            adapter = self._get_adapter()
            if adapter is not None:
                b = self._run(adapter.get_balance())
                if b is not None:
                    bal = {"total_equity": float(getattr(b, "total_equity", 0.0) or 0.0),
                           "available_balance": float(getattr(b, "available_balance", 0.0) or 0.0),
                           "unrealized_pnl": float(getattr(b, "unrealized_pnl", 0.0) or 0.0)}
                poss = self._run(adapter.get_positions())
                ords = self._run(adapter.fetch_open_orders())
        pos_list = []
        for p in (poss or []):
            if self._v3 is not None:
                amt = float(p.get("positionAmt") or 0.0)
                entry = float(p.get("entryPrice") or 0.0)
                pos_list.append({
                    "symbol": str(p.get("symbol") or "").split("/")[0],
                    "side": "long" if amt > 0 else ("short" if amt < 0 else ""),
                    "size": abs(amt),
                    "entry_price": entry,
                    "unrealized_pnl": float(p.get("unRealizedProfit") or 0.0),
                    "notional": abs(amt) * (entry or float(p.get("markPrice") or 0.0)),
                })
            else:
                pos_list.append({
                    "symbol": str(getattr(p, "symbol", "") or "").split("/")[0],
                    "side": str(getattr(p, "side", "") or ""),
                    "size": float(getattr(p, "size", 0.0) or 0.0),
                    "entry_price": float(getattr(p, "entry_price", 0.0) or 0.0),
                    "unrealized_pnl": float(getattr(p, "unrealized_pnl", 0.0) or 0.0),
                    "notional": abs(float(getattr(p, "size", 0.0) or 0.0)
                                    * float(getattr(p, "entry_price", 0.0) or 0.0)),
                })
        ord_list = []
        for o in (ords or []):
            try:
                if self._v3 is not None:
                    ord_list.append({
                        "id": str(o.get("orderId") or ""),
                        "symbol": str(o.get("symbol") or "").split("/")[0],
                        "side": str(o.get("side") or ""),
                        "price": float(o.get("price") or 0.0),
                        "amount": float(o.get("origQty") or 0.0),
                        "status": str(o.get("status") or ""),
                    })
                else:
                    ord_list.append({
                        "id": str(o.get("id") or o.get("order_id") or ""),
                        "symbol": str(o.get("symbol") or "").split("/")[0],
                        "side": str(o.get("side") or ""),
                        "price": float(o.get("price") or 0.0),
                        "amount": float(o.get("amount") or o.get("remaining") or 0.0),
                        "status": str(o.get("status") or ""),
                    })
            except Exception:
                continue
        self.snapshot["orders"] = ord_list
        if bal is not None:
            from datetime import datetime, timezone
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._day_start_date != today or self._day_start_equity is None:
                self._day_start_date = today
                self._day_start_equity = float(bal.get("total_equity") or 0.0)
            bal["day_start_equity"] = self._day_start_equity
            self.snapshot["balance"] = bal
        self.snapshot["positions"] = pos_list
        # [h674] 有效上限(随权益)暴露给页面
        self.snapshot["caps_effective"] = self._effective_caps()
        return self.snapshot

    def poll_fills(self, symbol: str) -> List[Dict[str, Any]]:
        since = self._last_fill_ms.get(symbol, int((time.time() - 120) * 1000))
        trades: List[Any] = []
        if self._v3 is not None and self._lane_tradeable():
            try:
                trades = self._v3.my_trades(f"{symbol}USDT", start_ms=since)
            except Exception as e:
                logger.warning("[h666] V3 userTrades 失败: %s", e)
        else:
            adapter = self._get_adapter()
            if adapter is not None and self._lane_tradeable():
                sym_ccxt = f"{str(symbol).upper()}/USDT:USDT"
                trades = self._run(adapter.fetch_my_trades(sym_ccxt, since_ms=since)) or []
        fills = []
        max_ms = since
        for t in trades:
            if self._v3 is not None:
                ms = int(float(t.get("time") or 0.0))
                side = str(t.get("side") or "").lower()
                qty = float(t.get("qty") or 0.0)
                px = float(t.get("price") or 0.0)
                fee = float(t.get("commission") or 0.0)
                oid = str(t.get("orderId") or "")
            else:
                ms = int(float(t.get("timestamp") or 0.0) or 0)
                side = str(t.get("side") or "").lower()
                qty = float(t.get("amount") or 0.0)
                px = float(t.get("price") or 0.0)
                fee = float((t.get("fee") or {}).get("cost") or 0.0)
                oid = str(t.get("order") or "")
            if ms <= since:
                continue
            fills.append({"symbol": symbol,
                          "side": "buy" if side == "buy" else "sell",
                          "qty": qty, "px": px, "fee": fee,
                          "ts_ms": ms, "order_id": oid})
            max_ms = max(max_ms, ms)
        self._last_fill_ms[symbol] = max_ms
        return fills

    # ── 下单核心:期望报价 → 撤改挂单 ────────────────────────────────
    def sync_quotes(self, symbol: str, desired: Dict[str, Any]) -> Dict[str, Any]:
        out = {"ok": False, "placed": 0, "cancelled": 0, "reason": "no_op"}
        if not self.enabled:
            out["reason"] = "no_keys_or_adapter"
            return out
        if not self._lane_tradeable():
            out["reason"] = "lane_not_tradeable"
            return out
        now = time.time()
        if now - self._last_sync.get(symbol, 0.0) < MIN_SYNC_INTERVAL_S:
            return out
        prev = self._desired.get(symbol) or {}
        if self._same_quote(prev.get("bid"), desired.get("bid")) \
                and self._same_quote(prev.get("ask"), desired.get("ask")):
            return out
        self._last_sync[symbol] = now
        new_notional = 0.0
        for side in ("bid", "ask"):
            q = desired.get(side)
            if q and q.get("px") and q.get("qty"):
                new_notional += float(q["px"]) * float(q["qty"])
        ok, why = self.check_caps(self.snapshot.get("positions") or [],
                                  new_notional=new_notional, symbol=symbol)
        if not ok:
            self._note_reject(why)
            out["reason"] = f"caps:{why}"
            return out
        cancelled = 0
        ids = list(self._order_ids.get(symbol) or [])
        for oid in ids:
            try:
                if self._v3 is not None:
                    self._v3.cancel_order(f"{symbol}USDT", order_id=oid)
                else:
                    adapter = self._get_adapter()
                    if adapter is not None:
                        self._run(adapter.cancel_order(str(oid), f"{symbol.upper()}/USDT:USDT"))
                cancelled += 1
            except Exception as e:
                self._note_reject(f"cancel:{type(e).__name__}")
        self._order_ids[symbol] = []
        placed = 0
        new_ids: List[str] = []
        for side in ("bid", "ask"):
            q = desired.get(side)
            if not q or not q.get("px") or not q.get("qty"):
                continue
            try:
                cid = f"mmlv-{self.lane_id[-8:]}-{symbol}-{side[:1]}-{int(now * 1000)}"[:36]
                if self._v3 is not None:
                    res = self._v3.place_order(
                        symbol=f"{symbol}USDT",
                        side="BUY" if side == "bid" else "SELL",
                        quantity=float(q["qty"]),
                        price=float(q["px"]),
                        time_in_force="GTX",
                        reduce_only=bool(q.get("reduce") or False),
                        client_order_id=cid,
                    )
                    status = str(res.get("status") or "")
                    if status in ("NEW", "PARTIALLY_FILLED", "FILLED"):
                        placed += 1
                        oid = res.get("orderId")
                        if oid:
                            new_ids.append(str(oid))
                    else:
                        self._note_reject(f"v3:{status}:{str(res.get('msg') or res.get('code') or '')[:40]}")
                else:
                    from backend.services.exchange.base_exchange_client import (
                        ExchangeOrder, OrderSide, OrderType,
                    )
                    adapter = self._get_adapter()
                    order = ExchangeOrder(
                        order_id="", symbol=f"{symbol.upper()}/USDT:USDT",
                        side=OrderSide.BUY if side == "bid" else OrderSide.SELL,
                        order_type=OrderType.LIMIT,
                        size=float(q["qty"]), price=float(q["px"]),
                        post_only=True,
                        reduce_only=bool(q.get("reduce") or False),
                        leverage=1, client_order_id=cid)
                    res = self._run(adapter.place_order(order)) if adapter else None
                    if res and str(res.get("status") or "").lower() not in ("error", "rejected"):
                        placed += 1
                        oid = res.get("order_id") or res.get("id")
                        if oid:
                            new_ids.append(str(oid))
                    else:
                        self._note_reject(str((res or {}).get("message") or "place_failed")[:60])
            except AsterdexRateLimited as e:
                self._note_reject(f"rate:{e}")
            except Exception as e:
                self._note_reject(f"{side}:{type(e).__name__}")
        self._order_ids[symbol] = new_ids
        self._desired[symbol] = desired
        if placed:
            self._note_ok()
        out.update({"ok": placed > 0 or cancelled > 0, "placed": placed,
                    "cancelled": cancelled, "reason": ""})
        return out

    @staticmethod
    def _same_quote(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
        if not a and not b:
            return True
        if not a or not b:
            return False
        px0, px1 = float(a.get("px") or 0.0), float(b.get("px") or 0.0)
        qty0, qty1 = float(a.get("qty") or 0.0), float(b.get("qty") or 0.0)
        if px0 > 0 and px1 > 0 and abs(px1 - px0) / px0 > PX_EPS_RATIO:
            return False
        if qty0 > 0 and qty1 > 0 and abs(qty1 - qty0) / qty0 > QTY_EPS_RATIO:
            return False
        return True

    def cancel_all(self) -> int:
        """kill switch:撤该车道全部挂单(含重启后残留)。返回撤单数。"""
        cancelled = 0
        if self._v3 is not None:
            try:
                orders = self._v3.open_orders()
                for o in orders:
                    oid = o.get("orderId")
                    sym = o.get("symbol") or ""
                    if oid and self._v3.cancel_order(str(sym), order_id=str(oid)):
                        cancelled += 1
            except Exception as e:
                logger.warning("[h666] cancel_all V3 异常: %s", e)
        else:
            adapter = self._get_adapter()
            if adapter is None:
                return 0
            try:
                orders = self._run(adapter.fetch_open_orders()) or []
                for o in orders:
                    oid = o.get("id") or o.get("order_id")
                    sym = o.get("symbol") or ""
                    if oid and self._run(adapter.cancel_order(str(oid), str(sym))):
                        cancelled += 1
            except Exception as e:
                logger.warning("[h665] cancel_all 异常: %s", e)
        self._order_ids = {}
        self._desired = {}
        return cancelled

    def market_exit(self, symbol: str) -> bool:
        """[h665] 孤儿仓真实减仓出口:reduce-only 市价单(平掉该币全部仓位)。"""
        if not self._lane_tradeable():
            return False
        try:
            pos_qty = sum(float(p.get("size") or 0.0)
                          for p in (self.snapshot.get("positions") or [])
                          if str(p.get("symbol") or "").upper() == symbol.upper()
                          and str(p.get("side") or "") == "long")
            pos_qty -= sum(float(p.get("size") or 0.0)
                           for p in (self.snapshot.get("positions") or [])
                           if str(p.get("symbol") or "").upper() == symbol.upper()
                           and str(p.get("side") or "") == "short")
            if abs(pos_qty) < 1e-12:
                return True
            side = "SELL" if pos_qty > 0 else "BUY"
            if self._v3 is not None:
                res = self._v3.place_market_order(symbol=f"{symbol}USDT",
                                                  side=side, quantity=abs(pos_qty),
                                                  reduce_only=True)
                return str(res.get("status") or "") in ("NEW", "PARTIALLY_FILLED", "FILLED")
            from backend.services.exchange.base_exchange_client import (
                ExchangeOrder, OrderSide, OrderType,
            )
            adapter = self._get_adapter()
            if adapter is None:
                return False
            order = ExchangeOrder(
                order_id="", symbol=f"{symbol.upper()}/USDT:USDT",
                side=OrderSide.SELL if pos_qty > 0 else OrderSide.BUY,
                order_type=OrderType.MARKET, size=abs(pos_qty),
                reduce_only=True, leverage=1,
                client_order_id=f"mmlive-exit-{symbol}-{int(time.time() * 1000)}"[:36])
            res = self._run(adapter.place_order(order))
            return bool(res and str(res.get("status") or "").lower() not in ("error", "rejected"))
        except Exception as e:
            self._note_reject(f"market_exit:{type(e).__name__}")
            return False


# [h666] 便于引用限频异常
from backend.services.exchange.asterdex_v3_client import AsterdexRateLimited  # noqa: E402
