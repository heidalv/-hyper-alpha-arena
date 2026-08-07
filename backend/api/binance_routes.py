"""
Binance 手动交易 API（阶段 3.3 恢复 + OrderAlgo 接线）。

背景: binance_routes.py 曾缺失（main.py try/except 兜底 None），前端
BinanceManualTrading 的 8 个端点全部 404，手动下单表单不可用。本文件恢复：

    POST   /binance/accounts/{id}/setup            保存/更新凭证
    GET    /binance/accounts/{id}/config           读取配置
    POST   /binance/accounts/{id}/enable           启用
    POST   /binance/accounts/{id}/disable          停用
    DELETE /binance/accounts/{id}/config           删除配置
    GET    /binance/accounts/{id}/balance          余额
    GET    /binance/accounts/{id}/positions        持仓
    POST   /binance/accounts/{id}/orders           下单（阶段 3.2 OrderAlgo 切片）
    POST   /binance/accounts/{id}/close-position   平仓

凭证复用 ExchangeCredential（user_id + exchange="binance"，与 /api/exchange/credentials 同源），
market_type / max_leverage 附加配置存 SystemConfig（key=binance_cfg:{user_id}）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.core.request_identity import require_user_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/binance", tags=["binance"])

EXCHANGE_NAME = "binance"


# ────────────────────────────────────────────────────────────────
# 请求模型
# ────────────────────────────────────────────────────────────────

class BinanceSetupRequest(BaseModel):
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    market_type: str = "futures"  # spot / futures
    testnet: bool = True
    max_leverage: Optional[int] = 20


class BinanceOrderRequest(BaseModel):
    symbol: str
    side: str  # buy / sell
    amount: float
    order_type: str = "market"  # market / limit
    price: Optional[float] = None
    leverage: Optional[int] = None
    reduce_only: bool = False
    # 阶段 3.2: 执行算法（MARKET/TWAP/POV/FUNDING_IS/SOR）
    algo: str = "MARKET"
    algo_config: Optional[dict] = None


# ────────────────────────────────────────────────────────────────
# 工具函数
# ────────────────────────────────────────────────────────────────

def _get_db():
    from backend.database.connection import SessionLocal
    return SessionLocal()


def _load_cfg(db, user_id: int) -> dict:
    """读取附加配置（market_type/max_leverage）。"""
    from backend.database.models import SystemConfig
    row = db.query(SystemConfig).filter(
        SystemConfig.key == f"binance_cfg:{user_id}",
    ).first()
    if row and row.value:
        try:
            return json.loads(row.value)
        except Exception:
            pass
    return {}


def _save_cfg(db, user_id: int, cfg: dict) -> None:
    from backend.database.models import SystemConfig
    row = db.query(SystemConfig).filter(
        SystemConfig.key == f"binance_cfg:{user_id}",
    ).first()
    raw = json.dumps(cfg, ensure_ascii=False)
    if row:
        row.value = raw
    else:
        db.add(SystemConfig(key=f"binance_cfg:{user_id}", value=raw))
    db.commit()


def _get_credential(db, user_id: int):
    """按 user_id + exchange 找凭证（全局凭证，同 AI 交易员统一执行）。"""
    from backend.database.models import ExchangeCredential
    return db.query(ExchangeCredential).filter(
        ExchangeCredential.user_id == user_id,
        ExchangeCredential.exchange == EXCHANGE_NAME,
    ).first()


def _get_client(user_id: int):
    from backend.services.exchange.exchange_manager import get_exchange_manager
    mgr = get_exchange_manager()
    return mgr.get_or_create_global_client(EXCHANGE_NAME, user_id=user_id)


def _fingerprint(api_key: str) -> Optional[str]:
    if not api_key:
        return None
    return hashlib.sha256(api_key.strip().encode()).hexdigest()[:16]


def _account_or_404(db, account_id: int):
    from backend.database.models import Account
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, f"Account {account_id} not found")
    return account


# ────────────────────────────────────────────────────────────────
# 配置管理
# ────────────────────────────────────────────────────────────────

@router.post("/accounts/{account_id}/setup")
async def setup_binance_account(account_id: int, body: BinanceSetupRequest, request: Request):
    """保存/更新币安凭证（空密钥保留原值）。"""
    uid, tid = require_user_tenant(request)
    from backend.database.models import ExchangeCredential
    from backend.utils.encryption import encrypt_private_key

    db = _get_db()
    try:
        _account_or_404(db, account_id)
        cred = _get_credential(db, uid)

        if cred:
            if body.api_key:
                cred.api_key_encrypted = encrypt_private_key(body.api_key)
            if body.api_secret:
                cred.api_secret_encrypted = encrypt_private_key(body.api_secret)
            cred.testnet = body.testnet
            cred.enabled = cred.enabled
            if hasattr(cred, "tenant_id"):
                cred.tenant_id = tid
            db.commit()
            cred_id = cred.id
        else:
            kwargs = dict(
                user_id=uid,
                account_id=account_id,
                exchange=EXCHANGE_NAME,
                api_key_encrypted=encrypt_private_key(body.api_key or ""),
                api_secret_encrypted=encrypt_private_key(body.api_secret or ""),
                testnet=body.testnet,
                enabled=False,
            )
            if "tenant_id" in ExchangeCredential.__table__.columns:
                kwargs["tenant_id"] = tid
            cred = ExchangeCredential(**kwargs)
            db.add(cred)
            db.commit()
            db.refresh(cred)
            cred_id = cred.id

        # 附加配置（market_type/max_leverage）
        cfg = _load_cfg(db, uid)
        cfg["market_type"] = body.market_type
        cfg["max_leverage"] = body.max_leverage or 20
        _save_cfg(db, uid, cfg)

        logger.info("[Binance] setup saved: user=%d account=%d cred=%d", uid, account_id, cred_id)
        return {"success": True, "message": "配置已保存", "id": cred_id}
    finally:
        db.close()


@router.get("/accounts/{account_id}/config")
async def get_binance_config(account_id: int, request: Request):
    uid, _tid = require_user_tenant(request)
    db = _get_db()
    try:
        _account_or_404(db, account_id)
        cred = _get_credential(db, uid)
        cfg = _load_cfg(db, uid)
        if not cred:
            return {
                "configured": False,
                "enabled": False,
                "market_type": "futures",
                "testnet": True,
                "max_leverage": 20,
                "api_key_fingerprint": None,
            }
        return {
            "configured": True,
            "enabled": bool(cred.enabled),
            "market_type": cfg.get("market_type", "futures"),
            "testnet": bool(cred.testnet),
            "max_leverage": int(cfg.get("max_leverage", 20) or 20),
            "api_key_fingerprint": _fingerprint(
                _decrypt(cred.api_key_encrypted)
            ) if cred.api_key_encrypted else None,
        }
    finally:
        db.close()


def _decrypt(raw: str) -> str:
    from backend.utils.encryption import decrypt_private_key
    try:
        return decrypt_private_key(raw) or ""
    except Exception:
        return ""


@router.post("/accounts/{account_id}/enable")
async def enable_binance_trading(account_id: int, request: Request):
    uid, tid = require_user_tenant(request)
    db = _get_db()
    try:
        _account_or_404(db, account_id)
        cred = _get_credential(db, uid)
        if not cred:
            raise HTTPException(404, "未配置币安凭证，请先完成配置")
        cred.enabled = True
        if hasattr(cred, "tenant_id"):
            cred.tenant_id = tid
        db.commit()

        # 预热客户端（验证密钥有效）
        from backend.utils.encryption import decrypt_private_key
        mgr = _get_manager()
        mgr.create_client(
            exchange=EXCHANGE_NAME,
            account_id=0,
            api_key=decrypt_private_key(cred.api_key_encrypted) or "",
            secret=decrypt_private_key(cred.api_secret_encrypted) or "",
            testnet=cred.testnet,
        )
        logger.info("[Binance] enabled: user=%d", uid)
        return {"success": True, "message": "币安交易已启用"}
    finally:
        db.close()


def _get_manager():
    from backend.services.exchange.exchange_manager import get_exchange_manager
    return get_exchange_manager()


@router.post("/accounts/{account_id}/disable")
async def disable_binance_trading(account_id: int, request: Request):
    uid, _tid = require_user_tenant(request)
    db = _get_db()
    try:
        _account_or_404(db, account_id)
        cred = _get_credential(db, uid)
        if cred:
            cred.enabled = False
            db.commit()
        return {"success": True, "message": "币安交易已停用"}
    finally:
        db.close()


@router.delete("/accounts/{account_id}/config")
async def delete_binance_config(account_id: int, request: Request):
    uid, _tid = require_user_tenant(request)
    db = _get_db()
    try:
        _account_or_404(db, account_id)
        cred = _get_credential(db, uid)
        if cred:
            _get_manager().remove_client(EXCHANGE_NAME, 0)
            db.delete(cred)
        cfg = _load_cfg(db, uid)
        if cfg:
            from backend.database.models import SystemConfig
            row = db.query(SystemConfig).filter(
                SystemConfig.key == f"binance_cfg:{uid}",
            ).first()
            if row:
                db.delete(row)
        db.commit()
        return {"success": True, "message": "币安配置已删除"}
    finally:
        db.close()


# ────────────────────────────────────────────────────────────────
# 账户数据
# ────────────────────────────────────────────────────────────────

@router.get("/accounts/{account_id}/balance")
async def get_binance_balance(account_id: int, request: Request):
    uid, _tid = require_user_tenant(request)
    db = _get_db()
    try:
        _account_or_404(db, account_id)
    finally:
        db.close()

    client = _get_client(uid)
    if client is None:
        raise HTTPException(400, "币安未配置或未启用（请先在「交易所配置」添加全局凭证）")
    try:
        bal = asyncio.run(client.get_balance())
    except Exception as e:
        logger.error("[Binance] get_balance error: %s", e)
        raise HTTPException(502, f"获取余额失败: {e}")
    return {
        "total_balance": round(bal.total_equity, 6),
        "total_equity": round(bal.total_equity, 6),
        "available_balance": round(bal.available_balance, 6),
        "margin_used": round(bal.frozen_margin, 6),
        "frozen_balance": round(bal.frozen_margin, 6),
        "unrealized_pnl": round(bal.unrealized_pnl, 6),
        "currency": "USDT",
    }


@router.get("/accounts/{account_id}/positions")
async def get_binance_positions(
    account_id: int, request: Request,
    force_refresh: bool = Query(default=False),
):
    uid, _tid = require_user_tenant(request)
    db = _get_db()
    try:
        _account_or_404(db, account_id)
    finally:
        db.close()

    client = _get_client(uid)
    if client is None:
        raise HTTPException(400, "币安未配置或未启用")
    try:
        positions = asyncio.run(client.get_positions())
    except Exception as e:
        logger.error("[Binance] get_positions error: %s", e)
        raise HTTPException(502, f"获取持仓失败: {e}")
    out = []
    for p in positions or []:
        out.append({
            "symbol": p.symbol,
            "side": p.side,
            "size": p.size,
            "entry_price": p.entry_price,
            "mark_price": p.mark_price,
            "liquidation_price": p.liquidation_price,
            "unrealized_pnl": p.unrealized_pnl,
            "leverage": p.leverage,
            "margin": p.margin,
            "notional": p.notional_value,
        })
    return {"positions": out}


# ────────────────────────────────────────────────────────────────
# 交易操作（阶段 3.2: OrderAlgo 切片）
# ────────────────────────────────────────────────────────────────

@router.post("/accounts/{account_id}/orders")
async def place_binance_order(account_id: int, body: BinanceOrderRequest, request: Request):
    """下单。algo != MARKET 时按 algo.py 切片分片执行。"""
    uid, _tid = require_user_tenant(request)
    db = _get_db()
    try:
        _account_or_404(db, account_id)
    finally:
        db.close()

    client = _get_client(uid)
    if client is None:
        raise HTTPException(400, "币安未配置或未启用")

    from backend.services.exchange.base_exchange_client import (
        ExchangeOrder, OrderSide, OrderType,
    )
    from backend.services.exchange.algo_exec import build_algo_slices, execute_slices

    side = OrderSide.BUY if body.side.lower() == "buy" else OrderSide.SELL
    otype = OrderType.LIMIT if body.order_type.lower() == "limit" else OrderType.MARKET
    algo = (body.algo or "MARKET").upper()

    # FUNDING_IS: 用交易所 funding rate（BaseExchangeClient 提供）
    funding_rate_8h = None
    if algo == "FUNDING_IS":
        try:
            funding_rate_8h = asyncio.run(client.get_funding_rate(body.symbol))
        except Exception as e:
            logger.warning("[Binance] funding rate unavailable: %s", e)

    children, meta = build_algo_slices(
        float(body.amount or 0), algo, body.algo_config,
        funding_rate_8h=funding_rate_8h,
    )
    if not children:
        raise HTTPException(400, f"algo {algo} 切片为空")
    if meta.get("fallback"):
        logger.warning("[Binance] %s %s 降级: %s", body.symbol, body.side, meta["fallback"])

    def _place_slice(qty: float, is_last: bool):
        order = ExchangeOrder(
            order_id="",
            symbol=body.symbol,
            side=side,
            order_type=otype,
            size=qty,
            price=body.price if otype == OrderType.LIMIT else None,
            leverage=body.leverage or 1,
            reduce_only=body.reduce_only,
            # 仅最后一片携带 TP/SL？手动下单无 TP/SL 参数 → 全 None
        )
        r = asyncio.run(client.place_order(order))
        logger.info(
            "[Binance] algo=%s slice=%s %s %s qty=%.6f -> %s",
            algo, "LAST" if is_last else "..", body.symbol, body.side, qty, r,
        )
        return r

    out = execute_slices(children, _place_slice, log_prefix="[Binance:algo]")
    if not out["results"]:
        raise HTTPException(502, f"全部子单失败: {out['errors']}")
    last = out["results"][-1]

    # 归一化结果（兼容前端 BinanceOrderResult）
    order_id = None
    if isinstance(last, dict):
        order_id = last.get("id") or last.get("order_id") or last.get("orderId")
    return {
        "status": "success" if not out["errors"] else "error",
        "order_id": str(order_id) if order_id else None,
        "symbol": body.symbol,
        "side": body.side,
        "amount": body.amount,
        "filled": sum(float(r.get("filled", 0) or 0) for r in out["results"] if isinstance(r, dict)) or None,
        "price": body.price,
        "algo": algo,
        "algo_meta": meta,
        "slices": out,
        "exchange": EXCHANGE_NAME,
        "market_type": "futures",
        "error": "; ".join(out["errors"]) or None,
    }


@router.post("/accounts/{account_id}/close-position")
async def close_binance_position(account_id: int, request: Request, symbol: str = Query(...)):
    """平仓（reduce_only 市价单，方向由当前持仓决定）。"""
    uid, _tid = require_user_tenant(request)
    db = _get_db()
    try:
        _account_or_404(db, account_id)
    finally:
        db.close()

    client = _get_client(uid)
    if client is None:
        raise HTTPException(400, "币安未配置或未启用")

    from backend.services.exchange.base_exchange_client import (
        ExchangeOrder, OrderSide, OrderType,
    )

    try:
        positions = asyncio.run(client.get_positions())
    except Exception as e:
        logger.error("[Binance] get_positions error: %s", e)
        raise HTTPException(502, f"获取持仓失败: {e}")

    pos = next((p for p in (positions or []) if p.symbol == symbol), None)
    if pos is None:
        raise HTTPException(404, f"无 {symbol} 持仓")

    close_side = OrderSide.SELL if pos.side == "long" else OrderSide.BUY
    order = ExchangeOrder(
        order_id="",
        symbol=symbol,
        side=close_side,
        order_type=OrderType.MARKET,
        size=abs(pos.size),
        leverage=1,
        reduce_only=True,
    )
    try:
        result = asyncio.run(client.place_order(order))
    except Exception as e:
        logger.error("[Binance] close_position error: %s", e)
        raise HTTPException(502, f"平仓失败: {e}")
    return {
        "status": "success",
        "order_id": str(result.get("id") or result.get("order_id") or "") if isinstance(result, dict) else None,
        "symbol": symbol,
        "side": "sell" if pos.side == "long" else "buy",
        "amount": abs(pos.size),
        "exchange": EXCHANGE_NAME,
        "market_type": "futures",
    }
