"""
CcxtBaseAdapter — CCXT 交易所共享基类

所有基于 CCXT 的交易所适配器（Binance/Bybit/OKX/Gate.io/Asterdex）
继承此类即可获得完整的 BaseExchangeClient 实现。
子类只需指定 _ccxt_class / _exchange_type / _supports_spot 等属性。
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from backend.services.exchange.base_exchange_client import (
    BaseExchangeClient,
    ExchangeBalance,
    ExchangeFeeTier,
    ExchangeIncentiveSummary,
    ExchangeOrder,
    ExchangePointsSnapshot,
    ExchangePosition,
    ExchangeRebateInfo,
    ExchangeType,
)

logger = logging.getLogger(__name__)


class CcxtBaseAdapter(BaseExchangeClient):
    """
    CCXT 通用适配器基类。

    子类只需覆盖以下类变量:
        _ccxt_id:        str   — ccxt exchange id (e.g. "bybit")
        _exchange_type:  ExchangeType
        _supports_spot:  bool
        _supports_futures: bool
    以及可选的 _extra_ccxt_config: dict 用于覆盖 ccxt 初始化参数。
    """

    _ccxt_id: str = ""
    _exchange_type: ExchangeType = ExchangeType.BINANCE
    _supports_spot_flag: bool = True
    _supports_futures_flag: bool = True
    _extra_ccxt_config: Dict[str, Any] = {}

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        testnet: bool = False,
        proxy_url: str = "",
        market_type: str = "usdt_m",
    ):
        self._exchange = None
        self.market_type = (market_type or "").strip().lower()
        self._dual_side_cache = (0.0, False)
        # [2026-09-01 实盘账户读取失败根治] ccxt async 客户端与其 aiohttp 会话绑定
        # 创建/首次使用时的事件循环；单例缓存跨线程复用（live_executor/
        # live_equity 用 asyncio.run 每次新建 loop）会触发 "Future attached to
        # a different loop" → 实盘余额/持仓读取全 0。保存构造参数 + 惰性按
        # 当前 running loop 重建客户端，保证每个 loop 有自己的 exchange 实例。
        self._ctor_args = (api_key, secret, password, testnet, proxy_url, market_type)
        self._loop_id = None
        self._build_exchange()

    def _build_exchange(self) -> None:
        """按保存的构造参数构建 ccxt async 客户端（无网络，惰性 load_markets）。"""
        api_key, secret, password, testnet, proxy_url, market_type = self._ctor_args
        try:
            import ccxt.async_support as ccxt

            # [2026-08-28 交易所环境] 币安账户环境:
            #   usdt_m / futures -> binance (USD-M 永续, 现状路径)
            #   coin_m          -> binancecoinm (COIN-M 币本位, dapi)
            #   margin          -> binance + defaultType=margin (全仓/逐仓杠杆)
            _mt = self.market_type
            _cid = self._ccxt_id
            _default_type = "future"
            if self._ccxt_id == "binance":
                if _mt == "coin_m":
                    _cid = "binancecoinm"
                elif _mt == "margin":
                    _cid = "binance"
                    _default_type = "margin"
                else:  # usdt_m / futures / 其它 -> 现状(USD-M 永续)
                    _cid = "binance"
                    _default_type = "future"

            cls = getattr(ccxt, _cid, None)
            if cls is None:
                logger.warning(
                    "ccxt has no exchange '%s', adapter in stub mode", _cid
                )
                return

            config: Dict[str, Any] = {
                "apiKey": api_key,
                "secret": secret,
                "sandbox": testnet,
                "options": {
                    "defaultType": _default_type,
                    # [2026-09-01 -1021 根治] 本机时钟实测超前 Binance 1.7s+，
                    # 签名请求间歇性 InvalidNonce；w32tm 被权限拒绝，只能应用层
                    # 校准：开启 adjustForTimeDifference，_ensure_loop 里
                    # load_time_difference() 会把偏移写入 timeDifference。
                    "adjustForTimeDifference": True,
                },
                "enableRateLimit": True,
            }
            # [2026-07-10 Phase0] 代理透传：国内环境访问 Binance/Bybit/OKX 必须走代理。
            # 不配代理 → ccxt 直连全部超时 → 多所聚合数据全空。
            # [2026-08-28] 凭证级代理优先(API凭证表单可配,币安IP白名单出口),否则环境变量。
            _proxy = proxy_url or os.environ.get("BINANCE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY")
            if _proxy:
                _pl = _proxy.strip().lower()
                if _pl.startswith("socks5") or _pl.startswith("socks4"):
                    # [2026-08-28 修复] ccxt async 的 SOCKS 必须走 socksProxy（ProxyConnector）；
                    # aiohttp_proxy 会被当作 HTTP 代理直传 -> ExchangeNotAvailable。
                    config["socksProxy"] = _proxy
                else:
                    config["proxies"] = {"http": _proxy, "https": _proxy}
                    config["aiohttp_proxy"] = _proxy  # async WS (watch_order_book)
            if password:
                config["password"] = password
            config.update(self._extra_ccxt_config)

            self._exchange = cls(config)
        except ImportError:
            logger.warning(
                "ccxt not installed, %s adapter in stub mode",
                self._ccxt_id,
            )

    async def _ensure_loop(self) -> None:
        """[2026-09-01] 当前 running loop 与客户端绑定 loop 不一致时重建客户端，
        并同步交易所时间差（本机时钟实测超前 Binance 1.7s+，签名请求会间歇性
        -1021 InvalidNonce；w32tm 被权限拒绝，只能在应用层校准）。

        ccxt async_support 的 aiohttp 会话在首次网络调用时绑定所在 loop；
        单例客户端被跨线程/跨 asyncio.run() 复用时旧实例必然报
        "Future attached to a different loop"。重建成本=仅对象构造（markets
        惰性加载），正确性优先。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无 running loop 的同步上下文：保持现状（调用方负责）
        if self._loop_id is None or self._loop_id != id(loop):
            logger.debug(
                "[CcxtBase] %s 客户端重建绑定 loop=%s（原 %s）",
                self._ccxt_id, id(loop), self._loop_id,
            )
            self._build_exchange()
            self._loop_id = id(loop)
            if self._exchange is not None:
                try:
                    # 拉取交易所时间校准 timeDifference（adjustForTimeDifference
                    # 生效），消除本机时钟超前导致的 -1021。
                    await self._exchange.load_time_difference()
                except Exception:
                    pass

    # ── Properties ────────────────────────────────

    @property
    def exchange_type(self) -> ExchangeType:
        return self._exchange_type

    @property
    def supports_spot(self) -> bool:
        return self._supports_spot_flag

    @property
    def supports_futures(self) -> bool:
        return self._supports_futures_flag

    # ── Balance ───────────────────────────────────

    async def get_balance(self) -> ExchangeBalance:
        await self._ensure_loop()
        if self._exchange is None:
            return ExchangeBalance(0, 0, 0, 0)
        try:
            bal = await self._exchange.fetch_balance()
            total = bal.get("total", {})
            free = bal.get("free", {})
            used = bal.get("used", {})
            usdt_total = float(total.get("USDT", 0) or 0)
            return ExchangeBalance(
                total_equity=usdt_total,
                available_balance=float(free.get("USDT", 0) or 0),
                frozen_margin=float(used.get("USDT", 0) or 0),
                unrealized_pnl=0,
            )
        except Exception as e:
            logger.warning("%s.get_balance failed: %s", self.__class__.__name__, e)
            return ExchangeBalance(0, 0, 0, 0)

    # ── Positions ─────────────────────────────────

    async def get_positions(self) -> List[ExchangePosition]:
        await self._ensure_loop()
        if self._exchange is None:
            return []
        # [2026-08-28 P0/P1 官方文档设计] 币安系：positionRisk V2（杠杆/模式/强平价 权威）
        # + V3（maintMargin/initialMargin）；失败回退 ccxt unified。
        if self._ccxt_id in ("binance", "binancecoinm"):
            try:
                _pre = "dapi" if self._ccxt_id == "binancecoinm" else "fapi"
                _v2 = getattr(self._exchange, f"{_pre}PrivateV2GetPositionRisk")
                # [2026-08-28 实盘零成交修复] 双向持仓（Hedge）模式必须按
                # positionSide 分别拉取，否则 positionRisk 返回 1764 行全零，
                # 实盘持仓/杠杆对账永远为空（实测账户 188 ps=LONG 却被漏掉）。
                _rows = []
                try:
                    _dual = await getattr(
                        self._exchange, f"{_pre}PrivateGetPositionSideDual"
                    )()
                    _is_dual = str(_dual.get("dualSidePosition") or "").lower() == "true"
                except Exception:
                    _is_dual = self._dual_side_cache[1]
                if _is_dual:
                    for _ps in ("LONG", "SHORT"):
                        try:
                            _rows.extend(await _v2({"positionSide": _ps}) or [])
                        except Exception as _ps_err:
                            logger.warning(
                                "%s positionRisk positionSide=%s 失败: %s",
                                self.__class__.__name__, _ps, _ps_err,
                            )
                else:
                    _rows = await _v2()
                try:
                    _v3 = await getattr(self._exchange, f"{_pre}PrivateV3GetPositionRisk")()
                except Exception:
                    _v3 = []
                v3_map = {}
                for _r in _v3 or []:
                    _b = str(_r.get("symbol") or "").upper().replace("USDT", "").replace("USD", "")
                    if _b:
                        v3_map[_b] = _r
                positions = []
                _seen = set()
                for r in _rows or []:
                    _b = str(r.get("symbol") or "").upper().replace("USDT", "").replace("USD", "")
                    if not _b:
                        continue
                    _amt = float(r.get("positionAmt") or 0)
                    if _amt == 0:
                        continue
                    # [2026-08-30 去重] positionRisk 在特定模式下同一 symbol
                    # 返回多行（BOTH/单向并存等），不去重导致宪法敞口与
                    # 持仓数翻倍（实测 2 笔仓返回 4 行）。
                    if _b in _seen:
                        continue
                    _seen.add(_b)
                    _r3 = v3_map.get(_b) or {}
                    _notional = float(r.get("notional") or 0)
                    _lev = float(r.get("leverage") or 0)
                    if _lev <= 0:
                        _lev = 1.0
                    _im = float(_r3.get("initialMargin") or 0)
                    if _im <= 0 and _lev > 0:
                        _im = _notional / _lev
                    _maint = float(_r3.get("maintMargin") or 0)
                    _margin_type = str(r.get("marginType") or "") or None
                    _isolated = bool(r.get("isolated")) if r.get("isolated") is not None else None
                    positions.append(ExchangePosition(
                        symbol=f"{_b}/USDT:USDT",
                        side=("long" if _amt > 0 else "short"),
                        size=abs(_amt),
                        entry_price=float(r.get("entryPrice") or 0),
                        mark_price=float(r.get("markPrice") or 0),
                        unrealized_pnl=float(r.get("unRealizedProfit") or 0),
                        margin=round(_im, 8),
                        leverage=_lev,
                        liquidation_price=_safe_float(r.get("liquidationPrice")),
                        margin_type=_margin_type,
                        isolated=_isolated,
                        maint_margin=round(_maint, 8),
                        margin_ratio=round(_maint / _notional * 100.0, 4) if _notional > 0 else 0.0,
                    ))
                return positions
            except Exception as e:
                logger.warning(
                    "%s positionRisk V2/V3 failed (%s), fallback unified",
                    self.__class__.__name__, e,
                )
        # fallback: ccxt unified（其它交易所 / V2 失败）
        try:
            raw = await self._exchange.fetch_positions()
            positions = []
            for p in raw:
                size = float(p.get("contracts", 0) or 0)
                if size == 0:
                    continue
                positions.append(
                    ExchangePosition(
                        symbol=p.get("symbol", ""),
                        side=p.get("side", ""),
                        size=size,
                        entry_price=float(p.get("entryPrice", 0) or 0),
                        mark_price=float(p.get("markPrice", 0) or 0),
                        unrealized_pnl=float(p.get("unrealizedPnl", 0) or 0),
                        margin=float(p.get("initialMargin", 0) or 0),
                        leverage=float(p.get("leverage") or 1) or 1,
                        liquidation_price=_safe_float(p.get("liquidationPrice")),
                    )
                )
            return positions
        except Exception as e:
            logger.warning("%s.get_positions failed: %s", self.__class__.__name__, e)
            return []

    # ── Orders ────────────────────────────────────

    async def set_margin_type(self, symbol: str, margin_type: str) -> bool:
        """[2026-08-28 P2] 全仓/逐仓切换（币安 POST /fapi/v1/marginType，约 5s 一次限速）。
        margin_type: 'cross' 或 'isolated'（ccxt unified 映射 CROSSED/ISOLATED）。"""
        await self._ensure_loop()
        if self._exchange is None:
            return False
        try:
            _mt = "cross" if str(margin_type).lower() in ("cross", "crossed") else "isolated"
            await self._exchange.set_margin_mode(_mt, symbol)
            return True
        except Exception as e:
            logger.warning("[CcxtAdapter] set_margin_mode %s/%s 失败: %s", symbol, margin_type, e)
            return False

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """[2026-08-28 方案2·G6] 设置合约杠杆（binance /fapi/v1/leverage；
        bybit/okx 同构）。stub/失败返回 False 不抛——杠杆不一致由对账兜底。"""
        await self._ensure_loop()
        if self._exchange is None:
            logger.warning(
                "[CcxtAdapter] %s stub 模式, set_leverage 跳过", self._ccxt_id
            )
            return False
        try:
            params: Dict[str, Any] = {"defaultType": "future"}
            await self._exchange.set_leverage(int(leverage), symbol, params=params)
            return True
        except Exception as e:
            logger.warning(
                "[CcxtAdapter] set_leverage %s %dx 失败: %s", symbol, leverage, e
            )
            return False

    async def place_order(self, order: ExchangeOrder) -> Dict:
        await self._ensure_loop()
        if self._exchange is None:
            return {"status": "error", "message": "ccxt not available"}
        try:
            # [2026-08-29 裸币名修复] 上游（scalp/master/proposal 车道）传的是
            # 裸基币名（如 "XPL"），ccxt create_order 需要统一交易对
            # （"XPL/USDT:USDT"）；且 fresh 客户端未 load_markets 时任何符号
            # 查找都会失败（实测 19:21 XPL 首单 "does not have market symbol"，
            # XPL 实际在币安永续上市——不是上架问题，是符号格式问题）。
            _sym_unified = str(order.symbol or "")
            if "/" not in _sym_unified:
                try:
                    if not getattr(self._exchange, "markets", None):
                        await self._exchange.load_markets()
                except Exception as _lm_err:
                    logger.warning(
                        "%s.place_order load_markets 失败: %s",
                        self.__class__.__name__, _lm_err,
                    )
                _mk = getattr(self._exchange, "markets", None) or {}
                _base = _sym_unified.upper()
                _cand = None
                for _m in _mk:
                    _mu = str(_m).upper()
                    if _mu.startswith(_base + "/"):
                        if _mu.endswith("/USDT:USDT"):
                            _cand = _m
                            break
                        _cand = _cand or _m
                if not _cand:
                    return {
                        "status": "error",
                        "message": f"symbol {order.symbol} 在 {self._ccxt_id} 无匹配市场",
                    }
                if _cand != _sym_unified:
                    logger.info(
                        "[UnifiedSymbol] %s → %s (裸基币名解析)",
                        _sym_unified, _cand,
                    )
                _sym_unified = _cand
            params: Dict[str, Any] = {}
            # [2026-08-31 Hedge 修复] 币安双向持仓模式下 reduceOnly 参数被拒
            # （-1106 "sent when not required"）——hedge 模式的减仓语义由
            # positionSide 表达，无需（也不允许）reduceOnly。单向模式才带。
            _binance_like = self._ccxt_id in ("binance", "binancecoinm")
            _dual_side_now = False
            if _binance_like:
                import time as _t
                _now = _t.time()
                if _now - self._dual_side_cache[0] > 60.0:
                    try:
                        _pre = "dapi" if self._ccxt_id == "binancecoinm" else "fapi"
                        _dual = await getattr(self._exchange, f"{_pre}PrivateGetPositionSideDual")()
                        _is_dual = str(_dual.get("dualSidePosition") or "").lower() == "true"
                        self._dual_side_cache = (_now, _is_dual)
                    except Exception:
                        _is_dual = self._dual_side_cache[1]
                else:
                    _is_dual = self._dual_side_cache[1]
                _dual_side_now = bool(_is_dual)
            if order.reduce_only and not (_binance_like and _dual_side_now):
                params["reduceOnly"] = True
            # [2026-08-28 -4061 修复] Hedge 双向持仓模式必须带 positionSide
            if _binance_like and _dual_side_now:
                # 显式指定（平仓同侧减仓）优先；否则按买卖方向自动推导
                _ps = getattr(order, "position_side", None)
                params["positionSide"] = _ps or ("LONG" if order.side.value == "buy" else "SHORT")
            if getattr(order, "post_only", False):
                # [2026-08-28] maker 挂单：GTX 只做 maker（不会立即成交，吃 maker 手续费）
                params["timeInForce"] = "GTX"
            if order.leverage and order.leverage != 1:
                params["leverage"] = order.leverage
            # [2026-09-04 p2-oms-exec] 幂等键：带上后下单超时可安全重试（交易所按此去重）。
            # 各家参数名不同，按 ccxt id 分发；未提供时不加任何参数，行为与此前一致。
            _cid = getattr(order, "client_order_id", None)
            if _cid:
                from backend.services.oms.client_id import sanitize_for_exchange

                _safe_cid = sanitize_for_exchange(_cid, self._ccxt_id)
                if self._ccxt_id in ("okx", "okex"):
                    params["clOrdId"] = _safe_cid
                elif self._ccxt_id.startswith("bybit"):
                    params["orderLinkId"] = _safe_cid
                else:
                    # binance / asterdex / gate 等走 ccxt 统一字段
                    params["newClientOrderId"] = _safe_cid
            # [2026-09-03 入场单 TP/SL 修复] ccxt binance 会把带 stopLossPrice /
            # takeProfitPrice 的 MARKET 单**改写成 STOP_MARKET / TAKE_PROFIT_MARKET
            # 触发单**（ccxt binance.create_order_request: isStopLoss → uppercaseType
            # = 'STOP_MARKET', stopPrice = stopLossPrice）。对开仓来说：买入止损价
            # 在市价之下 → 交易所 -2021 "Order would immediately trigger" 拒单，
            # 开仓根本发不出去。正确做法：入场单只发纯市价，成交后把 TP/SL 作为
            # reduce-only 条件单单独挂（复用 replace_tpsl_orders，与 live_tpsl_sync
            # 同一口径）。限价入场 / 平仓单保持原参数语义不变。
            _otype_val = str(getattr(order.order_type, "value", order.order_type) or "").lower()
            _attach_tpsl_after = (
                _otype_val == "market"
                and not order.reduce_only
                and (bool(getattr(order, "tp", None)) or bool(getattr(order, "sl", None)))
            )
            if not _attach_tpsl_after:
                if getattr(order, "tp", None):
                    params["takeProfitPrice"] = float(order.tp)
                if getattr(order, "sl", None):
                    params["stopLossPrice"] = float(order.sl)
            # [2026-08-29 数量精度] 币安按市场 stepSize 校验（如 XPL step=1
            # 整数张），带小数的数量直接被拒（实测 141.9013 → "must be
            # greater than minimum amount precision of 1"）。统一量化。
            _amount = order.size
            try:
                _amount = self._exchange.amount_to_precision(_sym_unified, float(order.size))
            except Exception:
                pass
            # [2026-08-29 杠杆对齐] ccxt create_order 的 leverage 参数币安会
            # 忽略——杠杆是每币种的账户级设置，必须单独 set_leverage。
            # 不对齐时决策的 10x 会被交易所侧旧设置覆盖（实测 XPL 75x：
            # $5.5 意图的量会以 75x 保证金语义重算成 $935 名义）。
            _lev_req = int(float(getattr(order, "leverage", 0) or 0))
            if _lev_req > 1:
                try:
                    await self._exchange.set_leverage(_lev_req, _sym_unified)
                except Exception as _lev_err:
                    logger.warning(
                        "%s.set_leverage(%s, %sx) 失败(沿用交易所现值): %s",
                        self.__class__.__name__, _sym_unified, _lev_req, _lev_err,
                    )
            result = await self._exchange.create_order(
                symbol=_sym_unified,
                type=order.order_type.value,
                side=order.side.value,
                amount=_amount,
                price=order.price,
                params=params if params else None,
            )
            if _attach_tpsl_after and isinstance(result, dict):
                # 入场已成交/已受理 → 单独挂 reduce-only TP/SL 条件单。失败只告警：
                # live_tpsl_sync 在账本同步时会再挂一次，不阻塞入场结果返回。
                try:
                    _qty_filled = float(result.get("filled") or result.get("amount") or _amount or 0)
                    _tpsl = await self.replace_tpsl_orders(
                        _sym_unified,
                        side="long" if order.side.value == "buy" else "short",
                        quantity=_qty_filled if _qty_filled > 0 else float(order.size),
                        tp_price=float(order.tp) if getattr(order, "tp", None) else None,
                        sl_price=float(order.sl) if getattr(order, "sl", None) else None,
                    )
                    if not _tpsl.get("ok"):
                        logger.warning(
                            "%s.place_order TP/SL attach failed (%s): %s",
                            self.__class__.__name__, _sym_unified, _tpsl.get("error"),
                        )
                    else:
                        result.setdefault("tpsl_attached", _tpsl.get("updated"))
                except Exception as _tpsl_err:
                    logger.warning(
                        "%s.place_order TP/SL attach error (%s): %s",
                        self.__class__.__name__, _sym_unified, _tpsl_err,
                    )
            return result if isinstance(result, dict) else {"status": "ok"}
        except Exception as e:
            logger.warning("%s.place_order failed: %s", self.__class__.__name__, e)
            return {"status": "error", "message": str(e)}

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        await self._ensure_loop()
        if self._exchange is None:
            return False
        try:
            await self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.warning("%s.cancel_order failed: %s", self.__class__.__name__, e)
            return False

    async def get_open_tpsl_orders(self, base_symbols=None) -> Dict[str, Dict[str, Any]]:
        """拉取挂在交易所的 TP/SL 条件单（reduce-only 的止盈/止损触发单）。

        [2026-08-29 实盘持仓展示] 币安期货的 TP/SL 不在 positionRisk 上，而是
        独立的 TAKE_PROFIT_MARKET / STOP_MARKET 条件单——持仓页要展示止盈止损
        必须拉挂单匹配。返回 {BASE: {"tp": 触发价, "sl": 触发价}}；
        非减仓的入场条件单（无 reduceOnly/closePosition）不算持仓止盈止损。
        """
        await self._ensure_loop()
        if self._exchange is None:
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        # [限速注意] 币安 fapi 无符号 openOrders weight=40 且触发 10 倍严格限速
        # （见 live_trading_routes.get_live_orders 的教训），必须逐 symbol 查
        # （weight=1/次）；调用方无 base_symbols 时才退化为全量查。
        orders = []
        if base_symbols:
            for base in base_symbols:
                try:
                    orders.extend(
                        await self._exchange.fetch_open_orders(f"{base}/USDT:USDT")
                    )
                except Exception:
                    continue
        else:
            try:
                orders = await self._exchange.fetch_open_orders() or []
            except Exception as e:
                logger.debug("%s.get_open_tpsl_orders failed: %s",
                             self.__class__.__name__, e)
                return {}
        for o in orders or []:
            try:
                info = o.get("info") or {}
                otype = str(o.get("type") or info.get("type") or "").upper()
                stop_px = float(o.get("stopPrice") or info.get("stopPrice") or 0)
                if stop_px <= 0:
                    continue
                is_reduce = bool(
                    o.get("reduce_only")
                    or str(info.get("reduceOnly") or "").lower() == "true"
                    or str(info.get("closePosition") or "").lower() == "true"
                )
                if not is_reduce:
                    continue  # 入场条件单，不是持仓止盈止损
                base = str(o.get("symbol") or "").split("/")[0].split("-")[0].upper()
                if not base:
                    continue
                entry = out.setdefault(base, {})
                if "TAKE_PROFIT" in otype:
                    entry.setdefault("tp", stop_px)
                elif otype in ("STOP_MARKET", "STOP", "TRAILING_STOP_MARKET"):
                    entry.setdefault("sl", stop_px)
            except Exception:
                continue
        return {
            b: {k: v for k, v in d.items() if k in ("tp", "sl")}
            for b, d in out.items()
        }

    def _swap_symbol(self, symbol: str) -> str:
        raw = str(symbol or "").strip().upper()
        base = raw.split("/")[0].split("-")[0].replace("USDT", "") or raw
        return f"{base}/USDT:USDT"

    @staticmethod
    def _reduce_tpsl_kind(order: Dict[str, Any]) -> Optional[str]:
        info = order.get("info") or {}
        otype = str(order.get("type") or info.get("type") or "").upper()
        is_reduce = bool(
            order.get("reduce_only")
            or str(info.get("reduceOnly") or "").lower() == "true"
            or str(info.get("closePosition") or "").lower() == "true"
        )
        if not is_reduce:
            return None
        if "TAKE_PROFIT" in otype:
            return "tp"
        if otype in ("STOP_MARKET", "STOP", "TRAILING_STOP_MARKET"):
            return "sl"
        return None

    async def _is_hedge_mode(self) -> bool:
        if self._ccxt_id not in ("binance", "binancecoinm"):
            return False
        import time as _t
        now = _t.time()
        if now - self._dual_side_cache[0] <= 60.0:
            return bool(self._dual_side_cache[1])
        try:
            pre = "dapi" if self._ccxt_id == "binancecoinm" else "fapi"
            dual = await getattr(self._exchange, f"{pre}PrivateGetPositionSideDual")()
            is_dual = str(dual.get("dualSidePosition") or "").lower() == "true"
            self._dual_side_cache = (now, is_dual)
            return is_dual
        except Exception:
            return bool(self._dual_side_cache[1])

    async def replace_tpsl_orders(
        self,
        symbol: str,
        *,
        side: str,
        quantity: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """撤旧 reduce-only 止盈/止损，再挂新的 STOP_MARKET / TAKE_PROFIT_MARKET。

        只改传入的那一侧；价格相对已有挂单差 ≤0.1% 则跳过，避免刷交易所。
        """
        if self._exchange is None:
            return {"ok": False, "error": "ccxt not available"}
        ccxt_sym = self._swap_symbol(symbol)
        qty = abs(float(quantity or 0))
        if qty <= 0:
            return {"ok": False, "error": "qty<=0"}
        try:
            open_orders = await self._exchange.fetch_open_orders(ccxt_sym) or []
        except Exception as exc:
            logger.warning("%s.replace_tpsl fetch_open_orders failed: %s",
                           self.__class__.__name__, exc)
            return {"ok": False, "error": f"fetch_open_orders:{exc}"}

        existing: Dict[str, list] = {"tp": [], "sl": []}
        for o in open_orders:
            kind = self._reduce_tpsl_kind(o)
            if not kind:
                continue
            existing[kind].append(o)

        close_side = "sell" if str(side or "").lower() in ("long", "buy") else "buy"
        hedge = await self._is_hedge_mode()
        updated = {"tp": False, "sl": False}

        async def _maybe_replace(kind: str, new_px: Optional[float], otype: str) -> None:
            if new_px is None:
                return
            px = float(new_px or 0)
            if px <= 0:
                return
            olds = existing.get(kind) or []
            if olds:
                try:
                    old_px = float(
                        olds[0].get("stopPrice")
                        or (olds[0].get("info") or {}).get("stopPrice")
                        or 0
                    )
                except (TypeError, ValueError):
                    old_px = 0.0
                if old_px > 0 and abs(old_px - px) / old_px <= 0.001:
                    return
            for old in olds:
                oid = str(old.get("id") or (old.get("info") or {}).get("orderId") or "")
                if not oid:
                    continue
                try:
                    await self._exchange.cancel_order(oid, ccxt_sym)
                except Exception as cancel_err:
                    logger.debug("%s.replace_tpsl cancel %s failed: %s",
                                 self.__class__.__name__, oid, cancel_err)
            params: Dict[str, Any] = {"stopPrice": px}
            if hedge:
                # [2026-09-03] 双向持仓模式：减仓语义由 positionSide 表达，币安/Aster
                # 对 hedge 模式下的 reduceOnly 直接拒单 -1106 "sent when not required"
                # （与 place_order 同一规则）。单向模式才带 reduceOnly。
                params["positionSide"] = "LONG" if close_side == "sell" else "SHORT"
            else:
                params["reduceOnly"] = True
            try:
                await self._exchange.create_order(
                    ccxt_sym, otype, close_side, qty, None, params,
                )
                updated[kind] = True
            except Exception as place_err:
                logger.warning(
                    "%s.replace_tpsl place %s failed: %s",
                    self.__class__.__name__, kind, place_err,
                )
                raise

        try:
            await _maybe_replace("sl", sl_price, "STOP_MARKET")
            await _maybe_replace("tp", tp_price, "TAKE_PROFIT_MARKET")
        except Exception as exc:
            return {"ok": False, "error": str(exc), "updated": updated}
        return {"ok": True, "via": "ccxt", "updated": updated, "symbol": ccxt_sym}

    async def place_trailing_stop(
        self,
        symbol: str,
        *,
        side: str,
        quantity: float,
        callback_rate_pct: float,
        activation_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """[2026-09-03 v3 方向1] 交易所原生 TRAILING_STOP_MARKET（Binance USDM / Aster 同构）。

        callback_rate_pct：从最优价回撤该百分比触发（币安允许 0.1–10）。activation_price 可选（不给 = 立即激活）。
        已存在同向 reduce-only 追踪单则先撤再挂（同价差 ≤0.1% 跳过）。ExitPolicy trailing 激活时由 live_tpsl_sync 调用，
        默认关闭（LIVE_NATIVE_TRAILING_STOP=false），软件侧 tighten_sl 仍是兜底。
        """
        if self._exchange is None:
            return {"ok": False, "error": "ccxt not available"}
        ccxt_sym = self._swap_symbol(symbol)
        qty = abs(float(quantity or 0))
        cb = float(callback_rate_pct or 0)
        if qty <= 0 or cb <= 0:
            return {"ok": False, "error": "qty<=0 or callback<=0"}
        cb = max(0.1, min(10.0, round(cb, 1)))
        close_side = "sell" if str(side or "").lower() in ("long", "buy") else "buy"
        try:
            open_orders = await self._exchange.fetch_open_orders(ccxt_sym) or []
        except Exception as exc:
            return {"ok": False, "error": f"fetch_open_orders:{exc}"}
        for o in open_orders:
            otype = str(o.get("type") or (o.get("info") or {}).get("type") or "").upper()
            if "TRAILING" not in otype:
                continue
            try:
                old_cb = float((o.get("info") or {}).get("priceRate") or (o.get("info") or {}).get("callbackRate") or 0)
            except (TypeError, ValueError):
                old_cb = 0.0
            if old_cb > 0 and abs(old_cb - cb) <= 0.05:
                return {"ok": True, "via": "ccxt", "skipped": "same_callback", "symbol": ccxt_sym}
            oid = str(o.get("id") or (o.get("info") or {}).get("orderId") or "")
            if oid:
                try:
                    await self._exchange.cancel_order(oid, ccxt_sym)
                except Exception as cancel_err:
                    logger.debug("%s.place_trailing_stop cancel %s failed: %s", self.__class__.__name__, oid, cancel_err)
        params: Dict[str, Any] = {"callbackRate": cb}
        if activation_price and float(activation_price) > 0:
            params["activationPrice"] = float(activation_price)
        if await self._is_hedge_mode():
            params["positionSide"] = "LONG" if close_side == "sell" else "SHORT"
        else:
            params["reduceOnly"] = True
        try:
            order = await self._exchange.create_order(ccxt_sym, "TRAILING_STOP_MARKET", close_side, qty, None, params)
        except Exception as exc:
            logger.warning("%s.place_trailing_stop failed: %s", self.__class__.__name__, exc)
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "via": "ccxt", "symbol": ccxt_sym, "order_id": str((order or {}).get("id") or ""), "callback_rate": cb}

    # ── Funding Rates ─────────────────────────────

    async def get_funding_rate(self, symbol: str) -> float:
        if self._exchange is None:
            return 0.0
        try:
            result = await self._exchange.fetch_funding_rate(symbol)
            return float(result.get("fundingRate", 0) or 0)
        except Exception as e:
            logger.warning(
                "%s.get_funding_rate failed: %s", self.__class__.__name__, e
            )
            return 0.0

    async def get_all_funding_rates(self) -> Dict[str, float]:
        if self._exchange is None:
            return {}
        try:
            results = await self._exchange.fetch_funding_rates()
            rates: Dict[str, float] = {}
            if isinstance(results, dict):
                for sym, item in results.items():
                    if isinstance(item, dict):
                        rates[sym] = float(item.get("fundingRate", 0) or 0)
                    else:
                        rates[sym] = float(item or 0)
            elif isinstance(results, list):
                for item in results:
                    if isinstance(item, dict):
                        rates[item.get("symbol", "")] = float(
                            item.get("fundingRate", 0) or 0
                        )
            return rates
        except Exception as e:
            logger.warning(
                "%s.get_all_funding_rates failed: %s", self.__class__.__name__, e
            )
            return {}

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """获取单个交易对当前资金费率（小时/结算费率，小数）。

        2026-07-06 新增：部分交易所（如 OKX）不支持批量 fetch_funding_rates，
        或批量会踩到 linear/inverse 端点歧义（如 Asterdex 走 binance 驱动时的 dapiPublic）。
        逐 symbol 用统一 ccxt 符号（如 "BTC/USDT:USDT"）显式取 linear 永续，稳定可靠。
        无数据/异常返回 None（由上游决定跳过，绝不臆造）。
        """
        await self._ensure_loop()
        if self._exchange is None:
            return None
        try:
            res = await self._exchange.fetch_funding_rate(symbol)
            if isinstance(res, dict):
                r = res.get("fundingRate")
                if r is not None:
                    return float(r)
        except Exception as e:
            logger.debug(
                "%s.get_funding_rate(%s) failed: %s", self.__class__.__name__, symbol, e
            )
        return None

    # ── Orderbook ─────────────────────────────────

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Dict:
        await self._ensure_loop()
        if self._exchange is None:
            return {"bids": [], "asks": []}
        try:
            book = await self._exchange.fetch_order_book(symbol, limit=depth)
            return book if isinstance(book, dict) else {"bids": [], "asks": []}
        except Exception as e:
            logger.warning(
                "%s.get_orderbook failed: %s", self.__class__.__name__, e
            )
            return {"bids": [], "asks": []}

    # ── Klines ────────────────────────────────────

    async def get_klines(
        self, symbol: str, interval: str, limit: int = 100
    ) -> List[Dict]:
        await self._ensure_loop()
        if self._exchange is None:
            return []
        try:
            ohlcv = await self._exchange.fetch_ohlcv(symbol, interval, limit=limit)
            return [
                {
                    "timestamp": c[0],
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5],
                }
                for c in ohlcv
            ]
        except Exception as e:
            logger.warning("%s.get_klines failed: %s", self.__class__.__name__, e)
            return []

    # ── 积分/返利套利扩展方法（带缓存 + 真实API） ──────

    # 子类可覆盖的费率配置类变量（作为 fallback 默认值）
    _fee_tier_config: Dict[str, Any] = {
        "tier_name": "VIP0",
        "maker_rate": 0.0002,
        "taker_rate": 0.0005,
        "rebate_rate": 0.0,
    }
    _base_rebate_rate: float = 0.0

    def _get_cache(self):
        """Lazy import cache to avoid circular imports."""
        from backend.services.rebate_arb.incentive_cache import incentive_cache
        return incentive_cache

    def _get_config(self):
        """Lazy import config."""
        try:
            from backend.config.rebate_config_loader import rebate_config
            return rebate_config
        except Exception:
            return None

    async def get_fee_tier(self) -> ExchangeFeeTier:
        """获取当前费率等级 — 优先从CCXT API获取，带TTL缓存和 fallback"""
        cache = self._get_cache()
        cache_key = f"{self._ccxt_id}_fee_tier"

        # Check cache
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Try real API
        if self._exchange is not None:
            try:
                # CCXT unified: fetchTradingFees() returns {symbol: {maker, taker, ...}}
                fees = await self._exchange.fetch_trading_fees()
                # Use BTC/USDT:USDT (futures) or BTC/USDT (spot) as reference
                ref_symbols = ["BTC/USDT:USDT", "BTC/USDT", "ETH/USDT:USDT"]
                maker = None
                taker = None
                for sym in ref_symbols:
                    if sym in fees:
                        maker = float(fees[sym].get("maker", 0) or 0)
                        taker = float(fees[sym].get("taker", 0) or 0)
                        break

                if maker is not None:
                    # Detect VIP tier from maker rate
                    tier_name = self._detect_vip_tier(maker, taker)
                    rebate_rate = abs(maker) if maker < 0 else 0.0
                    effective_maker = maker if maker >= 0 else 0.0

                    tier = ExchangeFeeTier(
                        exchange=self._ccxt_id,
                        tier_name=tier_name,
                        maker_rate=effective_maker,
                        taker_rate=taker,
                        rebate_rate=rebate_rate,
                    )
                    config = self._get_config()
                    ttl = config.cache_ttls.fee_tier_seconds if config else 3600
                    cache.set(cache_key, tier, ttl)
                    logger.info(
                        "[%s] Fee tier fetched: maker=%.5f%% taker=%.5f%% tier=%s",
                        self._ccxt_id, maker * 100, taker * 100, tier_name
                    )
                    return tier
            except Exception as e:
                logger.warning(
                    "[%s] fetchTradingFees failed: %s, using defaults",
                    self._ccxt_id, e
                )

        # Fallback to class defaults
        tier = ExchangeFeeTier(
            exchange=self._ccxt_id,
            tier_name=self._fee_tier_config.get("tier_name", "VIP0"),
            maker_rate=self._fee_tier_config.get("maker_rate", 0.0002),
            taker_rate=self._fee_tier_config.get("taker_rate", 0.0005),
            rebate_rate=self._fee_tier_config.get("rebate_rate", 0.0),
        )
        return tier

    def _detect_vip_tier(self, maker: float, taker: float) -> str:
        """Heuristic VIP tier detection from fee rates."""
        if maker < 0:
            return "MM"  # Market maker (negative fee = rebate)
        if maker <= 0.00005:
            return "VIP5+"
        if maker <= 0.0001:
            return "VIP3-4"
        if maker <= 0.00016:
            return "VIP1-2"
        return "VIP0"

    async def get_points_snapshot(self) -> ExchangePointsSnapshot:
        """获取积分快照 — 默认返回全0快照，需交易所子类覆盖"""
        return ExchangePointsSnapshot(
            exchange=self._ccxt_id,
        )

    async def get_rebate_info(self) -> ExchangeRebateInfo:
        """获取返利配置 — 基于 fee_tier 推导，子类可覆盖"""
        cache = self._get_cache()
        cache_key = f"{self._ccxt_id}_rebate_info"

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Derive from fee tier
        fee_tier = await self.get_fee_tier()
        rebate_rate = fee_tier.rebate_rate if fee_tier.rebate_rate > 0 else self._base_rebate_rate

        info = ExchangeRebateInfo(
            exchange=self._ccxt_id,
            base_rebate_rate=self._base_rebate_rate,
            current_rebate_rate=rebate_rate,
        )

        config = self._get_config()
        ttl = config.cache_ttls.rebate_info_seconds if config else 600
        cache.set(cache_key, info, ttl)
        return info

    async def get_incentive_summary(self) -> ExchangeIncentiveSummary:
        """获取激励政策汇总 — 组合 get_fee_tier + get_points_snapshot + get_rebate_info"""
        import time

        fee_tier = await self.get_fee_tier()
        points = await self.get_points_snapshot()
        rebate = await self.get_rebate_info()
        return ExchangeIncentiveSummary(
            exchange=self._ccxt_id,
            exchange_type=self._exchange_type,
            fee_tier=fee_tier,
            points=points,
            rebate=rebate,
            is_connected=self._exchange is not None,
            last_update=time.time(),
        )

    async def get_active_campaigns(self) -> List[Dict]:
        """获取进行中的活动 — 默认返回空列表，需交易所子类覆盖"""
        return []

    # ── Simple Earn（Binance sapi；其它 ccxt 所默认不支持）────────────────

    async def list_simple_earn_flexible(self, asset: str = "USDT") -> List[Dict[str, Any]]:
        """Binance Simple Earn 活期产品列表。非 binance 或 ccxt 不可用 → []。"""
        if self._ccxt_id not in ("binance", "binanceusdm") or self._exchange is None:
            return []
        try:
            params = {"asset": str(asset).upper()}
            raw = await self._exchange.sapi_get_simple_earn_flexible_list(params)
            rows = (raw or {}).get("rows") or []
            out: List[Dict[str, Any]] = []
            for r in rows[:20]:
                if not isinstance(r, dict):
                    continue
                out.append({
                    "product_id": r.get("productId"),
                    "asset": r.get("asset"),
                    "latest_annual_rate": r.get("latestAnnualPercentageRate"),
                    "min_purchase": r.get("minPurchaseAmount"),
                    "can_purchase": r.get("canPurchase"),
                    "can_redeem": r.get("canRedeem"),
                })
            return out
        except Exception as exc:
            logger.debug("%s.list_simple_earn_flexible: %s", self.__class__.__name__, exc)
            return []

    async def subscribe_simple_earn_flexible(self, asset: str, amount: float) -> Dict[str, Any]:
        """申购 Binance Simple Earn 活期。amount 为 USDT 数量。"""
        if self._ccxt_id not in ("binance", "binanceusdm") or self._exchange is None:
            return {"ok": False, "error": "not_binance"}
        amt = float(amount or 0)
        if amt <= 0:
            return {"ok": False, "error": "amount<=0"}
        products = await self.list_simple_earn_flexible(asset)
        pid = None
        for p in products:
            if p.get("can_purchase") and str(p.get("asset", "")).upper() == str(asset).upper():
                pid = p.get("product_id")
                break
        if not pid:
            return {"ok": False, "error": "no_product"}
        try:
            res = await self._exchange.sapi_post_simple_earn_flexible_subscribe({
                "productId": pid, "amount": str(amt),
            })
            return {"ok": True, "product_id": pid, "amount": amt, "raw": res}
        except Exception as exc:
            logger.warning("%s.subscribe_simple_earn_flexible failed: %s", self.__class__.__name__, exc)
            return {"ok": False, "error": str(exc)}

    # ── Cleanup ───────────────────────────────────

    async def close(self):
        """Release CCXT resources."""
        if self._exchange is not None:
            try:
                await self._exchange.close()
            except Exception:
                pass


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
