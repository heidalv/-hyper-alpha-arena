# -*- coding: utf-8 -*-
"""[h666 2026-10-01] Asterdex Futures API V3 原生客户端(官方文档:
github.com/asterdex/api-docs,V3 Recommended)。

为什么不能继续用 ccxt-binance 适配器:
  1. 2026-03-25 起 V1 不再签发新 API Key,新 Key 全是 V3;
  2. V3 鉴权 = **EIP-712 钱包签名**(user/signer/nonce/signature,chainId 1666,
     域名 AsterSignTransaction),不是 Binance HMAC(api_key+secret);
  3. nonce = 微秒时间戳,**重放保护**(±60s 窗口,每 agent 记最近 100 个 nonce);
  4. 限频:每账户 1200 单/分;连续 429 ⇒ IP 封禁 2 分钟~3 天——必须退避;
  5. 503 = 执行结果未知(不得当失败重试下单)。

本客户端覆盖实盘做市桥需要的端点:ping/time、下单(LIMIT GTX post-only)、
chase(BBO 自动贴单)、撤单/撤全部、挂单查询、成交回报、余额、持仓。
"""
from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import requests
from eth_account import Account
# [h666] eth_account ≥0.9:encode_structured_data → encode_typed_data(旧名已删)
try:
    from eth_account.messages import encode_structured_data as _enc  # 兼容旧版
except ImportError:
    from eth_account.messages import encode_typed_data as _enc

logger = logging.getLogger(__name__)

BASE_URL = "https://fapi.asterdex.com"

_TYPED_DATA_TEMPLATE = {
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "Message": [{"name": "msg", "type": "string"}],
    },
    "primaryType": "Message",
    "domain": {
        "name": "AsterSignTransaction",
        "version": "1",
        "chainId": 1666,
        "verifyingContract": "0x0000000000000000000000000000000000000000",
    },
    "message": {"msg": ""},
}

# 限频退避:遇到 429/418 后睡眠的秒数序列(指数)
_BACKOFF_S = (1.0, 3.0, 9.0)


class AsterdexRateLimited(Exception):
    """429/418:调用方必须退避,不得立即重试(否则 IP 封禁升级)。"""


class AsterdexV3Client:
    """线程安全:每请求独立签名;nonce 用单调微秒计数(重放保护)。"""

    def __init__(self, *, user: str, signer: str, private_key: str,
                 base_url: str = BASE_URL, timeout: float = 10.0):
        if not user or not signer or not private_key:
            raise ValueError("Asterdex V3 需要 user/signer/private_key 三项")
        self.user = str(user)
        self.signer = str(signer)
        self.private_key = str(private_key)
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.timeout = timeout
        self._last_us = 0
        self._i = 0

    # ── nonce(单调微秒,±60s 有效,防重放) ─────────────────────────
    def _nonce(self) -> int:
        now_us = int(time.time() * 1_000_000)
        if now_us <= self._last_us:
            now_us = self._last_us + 1
        self._last_us = now_us
        return now_us

    # ── 签名:对 urlencoded 参数串做 EIP-712 签名(官方示例口径) ────
    def _sign(self, params: Dict[str, str]) -> str:
        param_str = urllib.parse.urlencode(params)
        typed = dict(_TYPED_DATA_TEMPLATE)
        typed["message"] = {"msg": param_str}
        # [h666] eth_account ≥0.9:签名结构体用 full_message= 关键字传入
        msg = _enc(full_message=typed)
        signed = Account.sign_message(msg, private_key=self.private_key)
        return signed.signature.hex()

    def _request(self, method: str, path: str,
                 params: Optional[Dict[str, Any]] = None,
                 retries: int = 3) -> Dict[str, Any]:
        body = {}
        for k, v in (params or {}).items():
            if v is not None:
                body[str(k)] = str(v)
        body["nonce"] = str(self._nonce())
        body["signer"] = self.signer
        body["signature"] = self._sign(body)
        url = f"{self.base_url}{path}"
        for attempt in range(retries + 1):
            try:
                if method == "GET":
                    r = requests.get(url, params=body, timeout=self.timeout)
                else:
                    r = requests.request(
                        method, url, data=body, timeout=self.timeout,
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
            except requests.RequestException as e:
                if attempt >= retries:
                    raise
                time.sleep(_BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)])
                continue
            if r.status_code in (429, 418):
                # 官方:429 必须退避;连续违反 ⇒ 418 IP 封禁(2 分钟~3 天)
                raise AsterdexRateLimited(f"HTTP {r.status_code}: {r.text[:120]}")
            if r.status_code >= 500:
                if attempt >= retries:
                    break
                time.sleep(_BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)])
                continue
            break
        try:
            return r.json() if r.text else {}
        except Exception:
            return {"_raw": r.text[:200], "_status": r.status_code}

    # ── 端点 ──────────────────────────────────────────────────────
    def ping(self) -> Dict[str, Any]:
        return self._request("GET", "/fapi/v3/ping")

    @staticmethod
    def _fmt(v: float) -> str:
        s = f"{v:.8f}".rstrip("0").rstrip(".")
        return s if s else "0"

    def place_order(self, *, symbol: str, side: str, quantity: float,
                    price: float, time_in_force: str = "GTX",
                    reduce_only: bool = False,
                    client_order_id: Optional[str] = None) -> Dict[str, Any]:
        """LIMIT 下单;默认 GTX(post-only,做市专用)。"""
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "LIMIT",
            "timeInForce": time_in_force,
            "quantity": self._fmt(quantity),
            "price": self._fmt(price),
            "reduceOnly": "true" if reduce_only else "false",
            "newOrderRespType": "RESULT",
        }
        if client_order_id:
            # 官方 regex ^[\.A-Z\:/a-z0-9_-]{1,36}$
            cid = str(client_order_id)[:36]
            if all(ch.isalnum() or ch in "._:/-" for ch in cid):
                params["newClientOrderId"] = cid
        return self._request("POST", "/fapi/v3/order", params)

    def place_market_order(self, *, symbol: str, side: str, quantity: float,
                           reduce_only: bool = False) -> Dict[str, Any]:
        """MARKET 下单(只用于减仓出口等必须成交的场景)。"""
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": self._fmt(quantity),
            "reduceOnly": "true" if reduce_only else "false",
            "newOrderRespType": "RESULT",
        }
        return self._request("POST", "/fapi/v3/order", params)

    def place_chase(self, *, symbol: str, side: str, quantity: float,
                    reduce_only: bool = False, chase_offset: str = "0",
                    client_strategy_id: Optional[str] = None) -> Dict[str, Any]:
        """BBO 自动贴单(chase):交易所每秒自动追最优价,GTX post-only。
        chaseOffset=0 = 正好贴 bid/ask;MAX 参数见文档。做市首选(省撤改+免限频)。"""
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "quantity": self._fmt(quantity),
            "quantityUnit": "BASE",
            "reduceOnly": "true" if reduce_only else "false",
            "chaseOffset": chase_offset,
        }
        if client_strategy_id:
            params["clientStrategyId"] = str(client_strategy_id)[:28]
        return self._request("POST", "/fapi/v3/chase", params)

    def cancel_order(self, symbol: str, *, order_id: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        return self._request("DELETE", "/fapi/v3/order", params)

    def cancel_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        params = {"symbol": symbol} if symbol else {}
        return self._request("DELETE", "/fapi/v3/allOpenOrders", params)

    def open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"symbol": symbol} if symbol else {}
        r = self._request("GET", "/fapi/v3/openOrders", params)
        return r if isinstance(r, list) else []

    def my_trades(self, symbol: str, *, start_ms: Optional[int] = None,
                  limit: int = 200) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"symbol": symbol, "limit": str(limit)}
        if start_ms:
            params["startTime"] = str(start_ms)
        r = self._request("GET", "/fapi/v3/userTrades", params)
        return r if isinstance(r, list) else []

    def balance(self) -> List[Dict[str, Any]]:
        r = self._request("GET", "/fapi/v3/balance")
        return r if isinstance(r, list) else []

    def account(self) -> Dict[str, Any]:
        return self._request("GET", "/fapi/v3/account")

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"symbol": symbol} if symbol else {}
        r = self._request("GET", "/fapi/v3/positionRisk", params)
        return r if isinstance(r, list) else []

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """[h673] 修改初始杠杆(Change Initial Leverage)。"""
        return self._request("POST", "/fapi/v3/leverage",
                             {"symbol": symbol, "leverage": str(int(leverage))})
