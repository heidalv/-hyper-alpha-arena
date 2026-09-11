"""
AsterdexAdapter — Asterdex 交易所适配器

Asterdex 的 V1 API 与 Binance USDT-M 期货完全兼容（HMAC 签名、端点结构），
使用 CCXT 的 binance 驱动并覆盖 base URL 为 Asterdex 服务器。

[2026-09-03 核实（docs.asterdex.com + github.com/asterdex/api-docs）]
真实费率：USDT 永续 Maker 0% / Taker 0.04%；USD1 永续 Taker 0.005%；
          用 $ASTER 付手续费再省 5%。
真实激励：Trade & Earn（USDF / asBNB 作保证金 → 每周 USDF 奖励，进行中）。
已结束：  Stage 6 积分/空投（2026-05 发放完毕，无 Stage 7）。
真实端点：/fapi/v1/multiAssetsMargin（多资产模式）、/fapi/v1/commissionRate、
          /fapi/v1/income、/fapi/v1/userTrades、/fapi/v2/account|balance。
不存在的端点（旧代码虚构，已改为不发请求）：/fapi/v1/rh/points、
          /fapi/v1/usdf/mint、/fapi/v1/campaigns、marginType.collateralAsset。
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from backend.services.exchange.base_exchange_client import (
    ExchangeBalance,
    ExchangeFeeTier,
    ExchangeIncentiveSummary,
    ExchangePointsSnapshot,
    ExchangeRebateInfo,
    ExchangeType,
)
from backend.services.exchange.ccxt_base_adapter import CcxtBaseAdapter

logger = logging.getLogger(__name__)

ASTERDEX_FUTURES_URL = "https://fapi.asterdex.com"

# 多资产模式下可作保证金的 Aster 生息资产及官方抵押率（Trade & Earn 文档）
ASTER_COLLATERAL_RATIO: Dict[str, float] = {"USDF": 0.9999, "asBNB": 0.95}
# 稳定币类资产：无行情时按 1:1 折算（USDF 官方 1:1 锚定 USDT 可赎回）
_STABLE_ASSETS = {"USDT", "USDF", "USDC", "USD1"}


class AsterdexAdapter(CcxtBaseAdapter):
    """
    Asterdex 合约适配器

    基于 Binance-兼容 API，覆盖 CCXT binance 的 URL 配置。
    费率单一来源：rule_registry.TRADE_AND_EARN_PROGRAM.fee_schedule
    （USDT 永续 Maker 0% / Taker 0.04%）。
    """

    _ccxt_id = "binance"
    _exchange_type = ExchangeType.ASTERDEX
    _supports_spot_flag = False
    _supports_futures_flag = True

    # 费率配置（官方现值；get_fee_tier 会用 /fapi/v1/commissionRate 实测覆盖）
    _fee_tier_config: Dict[str, Any] = {
        "tier_name": "standard",
        "maker_rate": 0.0,        # 0%
        "taker_rate": 0.0004,     # 0.04%
        "rebate_rate": 0.0,       # 推荐返佣属账号运营层面（推荐人拆分），代码不假设
    }
    _base_rebate_rate: float = 0.0

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        testnet: bool = False,
    ):
        # [2026-09 修复] URL 覆盖必须在 _build_exchange 里做（见下方 override），
        # 否则 _ensure_loop 因事件循环切换重建客户端时 URL 回退到 api.binance.com，
        # 实盘请求全部打到币安。原 __init__ 末尾的运行时覆盖只对首次构建生效。
        super().__init__(
            api_key=api_key,
            secret=secret,
            password=password,
            testnet=testnet,
        )

    def _build_exchange(self) -> None:
        """构建 ccxt binance 驱动并把所有端点到 Asterdex（每次重建都生效）。"""
        super()._build_exchange()
        if self._exchange is not None:
            # [2026-09 修复] 合并而非整体替换：保留 binance urls['api'] 里的
            # sapi 等键，只覆盖 fapi/public/private 指向 Asterdex。
            _urls = dict(self._exchange.urls.get("api") or {})
            _urls.update({
                "fapiPublic": ASTERDEX_FUTURES_URL + "/fapi/v1",
                "fapiPrivate": ASTERDEX_FUTURES_URL + "/fapi/v1",
                "fapiPublicV2": ASTERDEX_FUTURES_URL + "/fapi/v2",
                "fapiPrivateV2": ASTERDEX_FUTURES_URL + "/fapi/v2",
                "public": ASTERDEX_FUTURES_URL + "/api/v3",
                "private": ASTERDEX_FUTURES_URL + "/api/v3",
            })
            self._exchange.urls["api"] = _urls
            self._exchange.urls["www"] = "https://www.asterdex.com"
            self._exchange.options["defaultType"] = "future"
            logger.debug("AsterdexAdapter URLs overridden (rebuild-safe)")

    # [2026-09 修复] ccxt binance 的 fetch_balance 会额外请求 spot sapi
    # （sapi/v1/capital/config/getall），该端点不存在于 Asterdex，导致整个
    # fetch_balance 抛异常、实盘余额恒为 0。改用币安兼容的 fapiPrivateV2
    # 裸接口直接读合约余额（与 get_positions 的 positionRisk V2 同模式）。
    #
    # [2026-09-03 多资产修复] 旧实现只累加 asset == "USDT"：一旦按 Trade & Earn
    # 把保证金换成 USDF / asBNB，系统读到余额 = 0 → 仓位计算/开仓许可全部
    # 失效（正常合约交易直接瘫痪）。改为优先读 /fapi/v2/account 的
    # totalMarginBalance / availableBalance —— 交易所已按多资产模式用
    # bid/ask 折算成 USD（API 文档注明），单资产模式下与旧口径一致。
    async def get_balance(self) -> ExchangeBalance:
        await self._ensure_loop()
        if self._exchange is None:
            return ExchangeBalance(0, 0, 0, 0)
        # ── 路径 1：账户总览（多资产折算后的权威口径） ──
        try:
            acc = await self._exchange.fapiPrivateV2GetAccount()
            if isinstance(acc, dict) and acc.get("totalMarginBalance") is not None:
                total = float(acc.get("totalMarginBalance") or 0)
                avail = float(acc.get("availableBalance") or 0)
                upnl = float(acc.get("totalUnrealizedProfit") or 0)
                init_margin = float(acc.get("totalInitialMargin") or 0)
                frozen = init_margin if init_margin > 0 else max(total - avail, 0.0)
                return ExchangeBalance(
                    total_equity=total,
                    available_balance=avail,
                    frozen_margin=frozen,
                    unrealized_pnl=upnl,
                )
        except Exception as e:
            logger.debug("AsterdexAdapter.get_balance v2/account fallback: %s", e)
        # ── 路径 2：逐资产余额兜底（稳定币 1:1；其余按抵押率×可得价折算） ──
        try:
            rows = await self._exchange.fapiPrivateV2GetBalance()
        except Exception as e:
            logger.warning("AsterdexAdapter.get_balance failed: %s", e)
            return ExchangeBalance(0, 0, 0, 0)
        total = 0.0
        avail = 0.0
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            asset = str(r.get("asset") or "").upper()
            try:
                bal = float(r.get("balance") or 0)
                av = float(r.get("availableBalance") or 0)
            except (TypeError, ValueError):
                continue
            if bal == 0 and av == 0:
                continue
            factor = self._collateral_usd_factor(asset)
            if factor <= 0:
                continue
            total += bal * factor
            avail += av * factor
        return ExchangeBalance(
            total_equity=total,
            available_balance=avail,
            frozen_margin=max(total - avail, 0.0),
            unrealized_pnl=0,
        )

    def _collateral_usd_factor(self, asset: str) -> float:
        """资产 → USD 折算系数（兜底路径用）。稳定币 1:1；USDF/asBNB 乘官方抵押率；
        其余非稳定资产无可靠即时价 → 0（不计入，宁少勿多，避免高估可用保证金）。"""
        a = str(asset or "").upper()
        if a in ("USDT", "USDC", "USD1"):
            return 1.0
        if a == "USDF":
            return float(ASTER_COLLATERAL_RATIO.get("USDF", 0.9999))
        return 0.0

    # ── [2026-09-03] Trade & Earn 相关真实接口封装（只读 + 多资产模式开关） ──

    async def get_margin_assets(self) -> List[Dict[str, Any]]:
        """逐资产保证金余额（/fapi/v2/account.assets），含是否可作保证金。

        返回: [{"asset","wallet_balance","available","unrealized_pnl",
                "margin_available": bool, "collateral_ratio": float}]
        仅返回余额非零或属于 Aster 生息资产（USDF/asBNB/ASTER/USDT）的行。
        """
        await self._ensure_loop()
        if self._exchange is None:
            return []
        try:
            acc = await self._exchange.fapiPrivateV2GetAccount()
        except Exception as e:
            logger.warning("AsterdexAdapter.get_margin_assets failed: %s", e)
            return []
        out: List[Dict[str, Any]] = []
        for a in (acc.get("assets") or []) if isinstance(acc, dict) else []:
            if not isinstance(a, dict):
                continue
            asset = str(a.get("asset") or "").upper()
            try:
                wb = float(a.get("walletBalance") or 0)
                av = float(a.get("availableBalance") or 0)
                up = float(a.get("unrealizedProfit") or 0)
            except (TypeError, ValueError):
                continue
            interesting = asset in ("USDT", "USDF", "ASBNB", "ASTER", "USD1")
            if wb == 0 and av == 0 and not interesting:
                continue
            ratio = ASTER_COLLATERAL_RATIO.get("asBNB" if asset == "ASBNB" else asset)
            out.append({
                "asset": "asBNB" if asset == "ASBNB" else asset,
                "wallet_balance": wb,
                "available": av,
                "unrealized_pnl": up,
                "margin_available": str(a.get("marginAvailable", "true")).lower() != "false",
                "collateral_ratio": float(ratio) if ratio is not None else (1.0 if asset in _STABLE_ASSETS else None),
            })
        return out

    async def get_multi_assets_mode(self) -> Optional[bool]:
        """GET /fapi/v1/multiAssetsMargin → True(多资产) / False(单资产) / None(失败)。"""
        await self._ensure_loop()
        if self._exchange is None:
            return None
        try:
            r = await self._exchange.fapiPrivateGetMultiAssetsMargin()
            if isinstance(r, dict):
                return str(r.get("multiAssetsMargin")).lower() == "true"
        except Exception as e:
            logger.debug("AsterdexAdapter.get_multi_assets_mode failed: %s", e)
        return None

    async def set_multi_assets_mode(self, enabled: bool) -> Dict[str, Any]:
        """POST /fapi/v1/multiAssetsMargin —— Trade & Earn 用 USDF/asBNB 作保证金的前提。

        交易所在有持仓/挂单时可能拒绝切换（返回 -4xxx 错误），原样透传给调用方。
        幂等：目标状态与当前一致时直接返回 success。
        """
        await self._ensure_loop()
        if self._exchange is None:
            return {"success": False, "error": "exchange_not_initialized"}
        cur = await self.get_multi_assets_mode()
        if cur is not None and cur == bool(enabled):
            return {"success": True, "multi_assets_mode": cur, "changed": False}
        try:
            r = await self._exchange.fapiPrivatePostMultiAssetsMargin(
                {"multiAssetsMargin": "true" if enabled else "false"}
            )
            logger.info("[Asterdex] multiAssetsMargin → %s: %s", enabled, r)
            return {"success": True, "multi_assets_mode": bool(enabled), "changed": True, "raw": r}
        except Exception as e:
            logger.warning("AsterdexAdapter.set_multi_assets_mode(%s) failed: %s", enabled, e)
            return {"success": False, "error": str(e)[:200], "multi_assets_mode": cur}

    async def get_commission_rate(self, symbol: str = "BTCUSDT") -> Optional[Dict[str, float]]:
        """GET /fapi/v1/commissionRate → {"maker": 0.0, "taker": 0.0004}（真实账户费率）。"""
        await self._ensure_loop()
        if self._exchange is None:
            return None
        sym = str(symbol or "BTCUSDT").upper().replace("/", "").replace(":USDT", "")
        try:
            r = await self._exchange.fapiPrivateGetCommissionRate({"symbol": sym})
            if isinstance(r, dict):
                return {
                    "maker": float(r.get("makerCommissionRate") or 0),
                    "taker": float(r.get("takerCommissionRate") or 0),
                }
        except Exception as e:
            logger.debug("AsterdexAdapter.get_commission_rate failed: %s", e)
        return None

    async def get_income_history(
        self,
        since_ms: int,
        income_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """GET /fapi/v1/income —— 真实资金流水（COMMISSION / FUNDING_FEE /
        REALIZED_PNL / TRANSFER / 各类奖励入账）。Trade & Earn 的 USDF 周奖励
        会以非交易类 incomeType + asset=USDF 出现，用于真实到账对账。"""
        await self._ensure_loop()
        if self._exchange is None:
            return []
        params: Dict[str, Any] = {"startTime": int(since_ms), "limit": int(max(1, min(limit, 1000)))}
        if income_type:
            params["incomeType"] = str(income_type)
        try:
            r = await self._exchange.fapiPrivateGetIncome(params)
            return [x for x in (r or []) if isinstance(x, dict)]
        except Exception as e:
            logger.debug("AsterdexAdapter.get_income_history failed: %s", e)
            return []

    async def get_user_trades(self, symbol: str, since_ms: int, limit: int = 1000) -> List[Dict[str, Any]]:
        """GET /fapi/v1/userTrades —— 单币种成交明细（maker 标记、quoteQty、commission）。"""
        await self._ensure_loop()
        if self._exchange is None:
            return []
        sym = str(symbol or "").upper().replace("/", "").replace(":USDT", "")
        if sym and not sym.endswith("USDT") and not sym.endswith("USD1"):
            sym = f"{sym}USDT"
        try:
            r = await self._exchange.fapiPrivateGetUserTrades(
                {"symbol": sym, "startTime": int(since_ms), "limit": int(max(1, min(limit, 1000)))}
            )
            return [x for x in (r or []) if isinstance(x, dict)]
        except Exception as e:
            logger.debug("AsterdexAdapter.get_user_trades(%s) failed: %s", sym, e)
            return []

    # ── [2026-09-03] Maker 优先下单（Aster 费率优化：Maker 0% vs Taker 0.04%） ──

    async def place_order_maker_first(
        self,
        order: Any,
        timeout_s: float = 30.0,
        poll_interval_s: float = 2.0,
    ) -> Dict[str, Any]:
        """Maker 优先下单：post-only 限价挂最优买/卖一，超时撤单回退市价。

        目标：0% maker 费率成交（Aster USDT 永续 taker 0.04%），**不改变交易意图**：
        - 只对 MARKET 意图的单做 maker 优先；其它类型原样交给 place_order
        - 裸币名（"XPL"）→ 统一符号（"XPL/USDT:USDT"）；数量按 stepSize 量化
        - 杠杆对齐（set_leverage）、双向持仓模式带 positionSide、reduceOnly 语义保留
        - 挂单不成交 / post-only 被拒 / 异常 → 撤单 → 市价兜底（最坏只是多等 timeout_s）
        - 部分成交 → 撤余量 → 市价补足（避免按全量登记持仓却只成交一半 → 平仓超平）
        - **成交后补挂 TP/SL 条件单**（reduce-only STOP_MARKET / TAKE_PROFIT_MARKET，
          复用 replace_tpsl_orders）。注意：不能把 tp/sl 放进入场单参数——ccxt binance
          会把带 stopLossPrice 的 MARKET 入场单改写成 STOP_MARKET 触发单。

        返回与 place_order 同构且可被套利/实盘两侧消费的 dict：
          {"status": "filled"|"error", "maker": bool, "price"/"average", "qty"/"amount"/
           "filled", "order_id"/"id", "exchange", "symbol", "partial_maker": bool}
        """
        await self._ensure_loop()
        if self._exchange is None:
            return {"status": "error", "message": "exchange_unavailable", "maker": False}
        otype = str(getattr(getattr(order, "order_type", None), "value", None)
                    or getattr(order, "order_type", "") or "").lower()
        if otype not in ("market", ""):
            return await self.place_order(order)

        from backend.services.exchange.base_exchange_client import ExchangeOrder, OrderType

        side_val = str(getattr(getattr(order, "side", None), "value", None) or order.side).lower()
        is_buy = side_val in ("buy", "long")
        sym = self._swap_symbol(order.symbol)

        # ── 市场元数据（精度/步长）+ 杠杆对齐 + 持仓模式 ──
        try:
            if not getattr(self._exchange, "markets", None):
                await self._exchange.load_markets()
        except Exception as e:
            logger.debug("[Asterdex] maker-first load_markets: %s → taker", e)
            return await self.place_order(order)
        if sym not in (getattr(self._exchange, "markets", None) or {}):
            logger.debug("[Asterdex] maker-first %s 无市场 → taker", sym)
            return await self.place_order(order)
        lev = int(float(getattr(order, "leverage", 0) or 0))
        if lev > 1:
            try:
                await self.set_leverage(sym, lev)   # 基类签名 (symbol, leverage)
            except Exception:
                pass
        hedge = await self._is_hedge_mode()

        # ── 盘口：买挂 best bid / 卖挂 best ask（加入队列，post-only 不会穿透） ──
        try:
            book = await self.get_orderbook(sym, depth=5)
            if not (book.get("bids") and book.get("asks")):
                raise RuntimeError("empty orderbook")
            best_bid = float(book["bids"][0][0])
            best_ask = float(book["asks"][0][0])
        except Exception as e:
            logger.debug("[Asterdex] maker-first book failed: %s → taker", e)
            return await self.place_order(order)
        limit_price = best_bid if is_buy else best_ask
        try:
            limit_price = float(self._exchange.price_to_precision(sym, limit_price))
        except Exception:
            pass
        try:
            amount = float(self._exchange.amount_to_precision(sym, float(order.size)))
        except Exception:
            amount = float(order.size)
        if amount <= 0:
            return {"status": "error", "message": "amount_after_precision<=0", "maker": False}

        params: Dict[str, Any] = {"postOnly": True}
        reduce_only = bool(getattr(order, "reduce_only", False))
        if hedge:
            _ps = getattr(order, "position_side", None)
            if reduce_only and not _ps:
                _ps = "SHORT" if is_buy else "LONG"   # 平空=买 / 平多=卖
            params["positionSide"] = _ps or ("LONG" if is_buy else "SHORT")
        elif reduce_only:
            params["reduceOnly"] = True
        # [2026-09-04 p2-oms-exec] 幂等键：maker-first 路径此前也不下发 clientOrderId
        _cid = getattr(order, "client_order_id", None)
        if _cid:
            from backend.services.oms.client_id import sanitize_for_exchange
            params["newClientOrderId"] = sanitize_for_exchange(_cid, "asterdex")

        deadline = time.time() + max(float(timeout_s or 0), 5.0)
        try:
            order_resp = await self._exchange.create_order(
                sym, "limit", "buy" if is_buy else "sell", amount, limit_price, params,
            )
        except Exception as e:
            # post-only 会因「立即成交」被拒（价格已穿透）→ 直接市价兜底
            logger.debug("[Asterdex] maker postOnly rejected: %s → taker", e)
            return await self._taker_with_tpsl(order, sym)

        oid = str((order_resp or {}).get("id") or (order_resp or {}).get("orderId") or "")
        if not oid:
            return await self._taker_with_tpsl(order, sym)

        filled = 0.0
        avg = limit_price
        terminal = False
        while time.time() < deadline:
            await asyncio.sleep(max(0.5, float(poll_interval_s or 2.0)))
            try:
                st = await self._exchange.fetch_order(oid, sym)
            except Exception:
                continue
            status = str(st.get("status") or "").lower()
            try:
                filled = float(st.get("filled") or 0)
                avg = float(st.get("average") or st.get("price") or limit_price)
            except (TypeError, ValueError):
                pass
            if status in ("closed", "filled", "canceled", "cancelled", "expired", "rejected"):
                terminal = True
                break

        # ── 超时 / 未完全成交 → 撤单（幂等），再读一次真实成交量 ──
        if not terminal or filled + 1e-12 < amount:
            try:
                await self._exchange.cancel_order(oid, sym)
            except Exception:
                pass
            try:
                st = await self._exchange.fetch_order(oid, sym)
                filled = float(st.get("filled") or filled or 0)
                avg = float(st.get("average") or st.get("price") or avg)
            except Exception:
                pass

        remaining = max(float(amount) - float(filled), 0.0)
        maker_only = filled > 0 and remaining <= 1e-9
        result: Dict[str, Any]
        if maker_only:
            result = {
                "status": "filled", "maker": True, "partial_maker": False,
                "price": avg, "average": avg, "qty": filled, "amount": filled, "filled": filled,
                "order_id": oid, "id": oid, "exchange": "asterdex", "symbol": sym,
            }
        else:
            # 余量市价补足（含"一点没成交"的情形：filled=0 → 全量市价）
            taker_order = ExchangeOrder(
                order_id=f"{getattr(order, 'order_id', '') or 'mf'}_fill",
                symbol=sym, side=order.side, order_type=OrderType.MARKET,
                size=remaining if remaining > 1e-9 else float(order.size),
                reduce_only=reduce_only, leverage=getattr(order, "leverage", None),
            )
            if getattr(order, "position_side", None):
                try:
                    taker_order.position_side = order.position_side
                except Exception:
                    pass
            tr = await self.place_order(taker_order)
            if str(tr.get("status") or "").lower() == "error":
                if filled > 0:
                    # 补量失败：如实返回已成交的 maker 部分（调用方按此量登记）
                    result = {
                        "status": "filled", "maker": True, "partial_maker": True,
                        "price": avg, "average": avg, "qty": filled, "amount": filled, "filled": filled,
                        "order_id": oid, "id": oid, "exchange": "asterdex", "symbol": sym,
                        "message": f"taker_topup_failed: {tr.get('message')}",
                    }
                else:
                    return {"status": "error", "maker": False,
                            "message": f"maker_unfilled_and_taker_failed: {tr.get('message')}"}
            else:
                try:
                    t_qty = float(tr.get("filled") or tr.get("amount") or taker_order.size)
                    t_px = float(tr.get("average") or tr.get("price") or avg)
                except (TypeError, ValueError):
                    t_qty, t_px = float(taker_order.size), avg
                total_qty = filled + t_qty
                wavg = (avg * filled + t_px * t_qty) / total_qty if total_qty > 0 else t_px
                result = {
                    "status": "filled", "maker": False, "partial_maker": filled > 0,
                    "price": wavg, "average": wavg, "qty": total_qty, "amount": total_qty,
                    "filled": total_qty, "order_id": oid or str(tr.get("id") or ""),
                    "id": oid or str(tr.get("id") or ""), "exchange": "asterdex", "symbol": sym,
                    "maker_qty": filled, "taker_qty": t_qty,
                }

        # ── 成交后补挂 TP/SL 条件单（开仓才需要；平仓单本身就是 reduce-only） ──
        if not reduce_only:
            await self._attach_tpsl_after_fill(order, sym, float(result.get("qty") or 0))
        logger.info(
            "[Asterdex] maker-first %s %s qty=%.6f maker=%s partial=%s px=%.6f",
            sym, "buy" if is_buy else "sell", float(result.get("qty") or 0),
            result.get("maker"), result.get("partial_maker"), float(result.get("price") or 0),
        )
        return result

    async def _taker_with_tpsl(self, order: Any, sym: str) -> Dict[str, Any]:
        """maker 路径直接不可用时的市价兜底：走 place_order（其自身负责 TP/SL 挂单）。"""
        r = await self.place_order(order)
        if isinstance(r, dict) and str(r.get("status") or "").lower() != "error":
            r.setdefault("maker", False)
            r.setdefault("status", "filled")
        return r

    async def _attach_tpsl_after_fill(self, order: Any, sym: str, qty: float) -> None:
        """开仓成交后把决策自带的 TP/SL 写成交易所 reduce-only 条件单。失败只告警：
        live_tpsl_sync 会在纸盘账本同步时再次尝试，不阻塞主流程。"""
        tp = getattr(order, "tp", None)
        sl = getattr(order, "sl", None)
        if not tp and not sl:
            return
        if qty <= 0:
            return
        try:
            side_val = str(getattr(getattr(order, "side", None), "value", None) or order.side).lower()
            r = await self.replace_tpsl_orders(
                sym,
                side="long" if side_val in ("buy", "long") else "short",
                quantity=qty,
                tp_price=float(tp) if tp else None,
                sl_price=float(sl) if sl else None,
            )
            if not r.get("ok"):
                logger.warning("[Asterdex] attach TP/SL after fill failed: %s", r.get("error"))
        except Exception as e:
            logger.warning("[Asterdex] attach TP/SL after fill error: %s", e)

    # ── 激励方法（[2026-09-03] 全部改为诚实实现：不再请求虚构端点） ──

    async def get_points_snapshot(self) -> ExchangePointsSnapshot:
        """积分快照 —— Stage 6 已结束、官方 API 无积分端点。

        旧实现请求 /fapi/v1/rh/points（该端点不存在于 asterdex/api-docs），永远失败后
        静默返回 0 分 / 1.0 倍并刷 warning。现在直接返回带明确 season 说明的占位快照，
        不发网络请求；接口签名与返回类型不变（rebate 域/聚合器继续可用）。
        """
        return ExchangePointsSnapshot(
            exchange="asterdex",
            points_balance=0.0,
            points_multiplier=1.0,
            season="Stage 6 已结束（无进行中积分赛季）",
            qualifying_days=0,
            required_days=0,
            airdrop_eligible=False,
            estimated_airdrop_value=0.0,
        )

    async def get_fee_tier(self) -> ExchangeFeeTier:
        """费率 —— 官方现值 Maker 0% / Taker 0.04%；/fapi/v1/commissionRate 实测覆盖（带缓存）。"""
        cache = self._get_cache()
        cache_key = "asterdex_fee_tier"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        tier = ExchangeFeeTier(
            exchange="asterdex",
            tier_name=self._fee_tier_config["tier_name"],
            maker_rate=self._fee_tier_config["maker_rate"],
            taker_rate=self._fee_tier_config["taker_rate"],
            rebate_rate=self._fee_tier_config["rebate_rate"],
        )
        real = await self.get_commission_rate("BTCUSDT")
        if real is not None and real.get("taker", 0) >= 0:
            tier = ExchangeFeeTier(
                exchange="asterdex",
                tier_name="account",
                maker_rate=float(real.get("maker", 0.0)),
                taker_rate=float(real.get("taker", 0.0004)),
                rebate_rate=self._fee_tier_config["rebate_rate"],
            )

        config = self._get_config()
        ttl = config.cache_ttls.fee_tier_seconds if config else 3600
        cache.set(cache_key, tier, ttl)
        return tier

    async def get_rebate_info(self) -> ExchangeRebateInfo:
        """返利 —— Aster 没有账户级自动返佣；推荐返佣由推荐人拆分、走账号层面，
        代码不假设任何比例（旧实现 10%×积分乘数 是虚构口径）。字段保留为 0。"""
        return ExchangeRebateInfo(
            exchange="asterdex",
            base_rebate_rate=0.0,
            current_rebate_rate=0.0,
            stacked_multiplier=1.0,
            trading_volume_7d=0.0,
            projected_weekly_rebate=0.0,
        )

    # ── USDF / 抵押资产：期货 API 无铸造与抵押资产切换端点 ──

    async def mint_usdf(self, amount_usd: float, skip_if_sufficient: bool = False) -> Dict[str, Any]:
        """USDT → USDF 铸造 —— **期货 API 不提供**（官方 api-docs 无 /fapi/v1/usdf/mint）。

        USDF 兑换只能在 Aster 网页 [Swap] 或链上完成。这里不再发虚构请求，直接返回
        fallback 结果（调用方 rebate 引擎原本就按 fallback=usdt 处理）。
        """
        return {
            "success": False,
            "error": "usdf_mint_not_available_via_futures_api",
            "hint": "请在 Aster 网页合约账户点 [Swap] 把 USDT 换成 USDF（一次性手工操作）",
            "fallback": "usdt",
        }

    async def get_usdf_balance(self) -> float:
        """当前 USDF 可用余额（/fapi/v2/balance，真实端点）。"""
        if self._exchange is None:
            return 0.0
        cache = self._get_cache()
        cache_key = "asterdex_usdf_balance"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            await self._ensure_loop()
            rows = await self._exchange.fapiPrivateV2GetBalance()
            for item in rows or []:
                if isinstance(item, dict) and str(item.get("asset") or "").upper() == "USDF":
                    balance = float(item.get("availableBalance", 0) or 0)
                    cache.set(cache_key, balance, 60)
                    return balance
            return 0.0
        except Exception as e:
            logger.debug("[Asterdex] get_usdf_balance failed: %s", e)
            return 0.0

    async def set_collateral_type(self, symbol: str, collateral: str = "USDF") -> bool:
        """按交易对指定抵押资产 —— **API 不支持**（marginType 没有 collateralAsset 参数）。

        Aster 的抵押资产由「多资产模式」统一决定：开启后账户内 USDF/asBNB/ASTER 等
        marginAvailable 资产按抵押率自动计入保证金。请改用 set_multi_assets_mode(True)。
        """
        logger.info(
            "[Asterdex] set_collateral_type(%s, %s) 不受 API 支持 → 请用 set_multi_assets_mode(True)",
            symbol, collateral,
        )
        return False

    # ── 活动信息 ──

    async def get_active_campaigns(self) -> List[Dict]:
        """进行中的激励活动（单一来源 rule_registry；不再请求虚构 /fapi/v1/campaigns）。"""
        cache = self._get_cache()
        cache_key = "asterdex_campaigns"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            from backend.services.rebate_arb.rule_registry import TRADE_AND_EARN_PROGRAM as _TE
        except Exception:
            _TE = {}
        campaigns: List[Dict] = [
            {
                "campaign_id": "aster_trade_and_earn",
                "name": _TE.get("name", "Trade & Earn"),
                "type": "collateral_rewards",
                "status": "active" if _TE.get("active", True) else "ended",
                "reward_asset": _TE.get("reward_asset", "USDF"),
                "weekly_volume_threshold_usd": _TE.get("weekly_volume_threshold_usd", 50_000.0),
                "weekly_active_days_threshold": _TE.get("weekly_active_days_threshold", 2),
                "usdf_counted_cap": _TE.get("usdf_counted_cap", 100_000.0),
                "source_url": _TE.get("source_url"),
                "end_time": None,
            },
            {
                "campaign_id": "aster_stage6_airdrop",
                "name": "ASTER Stage 6 Airdrop（已结束）",
                "type": "airdrop",
                "total_allocation": 64_000_000,
                "token": "ASTER",
                "status": "ended",
                "note": "2026-05 发放完毕；官方未公布 Stage 7",
                "end_time": "2026-05-04T12:00:00Z",
            },
        ]
        config = self._get_config()
        ttl = config.cache_ttls.campaigns_seconds if config else 1800
        cache.set(cache_key, campaigns, ttl)
        return campaigns
