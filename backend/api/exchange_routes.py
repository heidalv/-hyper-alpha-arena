"""
Exchange Hub & Cross-Exchange API Routes

  Credential Management:
    GET    /api/exchange/credentials          — 列出已配置交易所凭证
    POST   /api/exchange/credentials          — 添加/更新凭证
    DELETE /api/exchange/credentials/{id}     — 删除凭证
    POST   /api/exchange/credentials/{id}/test — 测试连接

  Exchange Status & Data:
    GET  /api/exchange/statuses               — 所有交易所连接状态
    GET  /api/exchange/supported              — 可用交易所列表
    GET  /api/exchange/{exchange}/balance      — 余额
    GET  /api/exchange/{exchange}/positions    — 持仓
    GET  /api/exchange/positions/all           — 跨所统一持仓

  Cross-Exchange Arbitrage:
    GET  /api/exchange/cross-arb/spreads       — 跨所价差扫描
    GET  /api/exchange/cross-arb/funding-rates — 跨所资金费率对比
    GET  /api/exchange/cross-arb/trades        — 套利交易记录
    GET  /api/exchange/cross-arb/exposure      — 风险敞口
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exchange", tags=["Exchange Hub"])

SUPPORTED_EXCHANGES = [
    {"id": "hyperliquid", "name": "Hyperliquid", "supports_spot": False, "supports_futures": True, "needs_passphrase": False},
    {"id": "binance", "name": "Binance", "supports_spot": True, "supports_futures": True, "needs_passphrase": False},
    {"id": "bybit", "name": "Bybit", "supports_spot": True, "supports_futures": True, "needs_passphrase": False},
    {"id": "okx", "name": "OKX", "supports_spot": True, "supports_futures": True, "needs_passphrase": True},
    {"id": "gateio", "name": "Gate.io", "supports_spot": True, "supports_futures": True, "needs_passphrase": False},
    {"id": "asterdex", "name": "Asterdex", "supports_spot": False, "supports_futures": True, "needs_passphrase": False},
]


def _get_manager():
    from backend.services.exchange.exchange_manager import get_exchange_manager
    return get_exchange_manager()


# ════════════════════════════════════════════════════════
#  Supported exchanges
# ════════════════════════════════════════════════════════

@router.get("/supported")
async def get_supported_exchanges():
    """返回系统支持的交易所列表。"""
    return SUPPORTED_EXCHANGES


# ════════════════════════════════════════════════════════
#  Credential CRUD
# ════════════════════════════════════════════════════════

class CredentialCreate(BaseModel):
    account_id: Optional[int] = None
    # [2026-08-28] 编辑语义: 携带 id 时精确更新该凭证(多API场景)
    id: Optional[int] = None
    # user_id 已废弃：一律绑定 JWT 当前用户，忽略客户端传入
    user_id: Optional[int] = None
    exchange: str
    label: str = ""
    # [2026-08-28] 凭证级代理(币安API IP白名单出口);空=用环境变量
    proxy_url: Optional[str] = None
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    testnet: bool = True
    enabled: bool = True
    # [2026-09] Asterdex 积分一体化开关（仅 asterdex 有意义）：
    # 实盘成交「顺路」吃 Rh 积分（maker-first 挂单 + 逐笔计量）。
    # points_config: {"maker_first": bool, "maker_timeout_s": float,
    #                 "asset_points_enabled": bool}
    points_enabled: Optional[bool] = None
    points_config: Optional[Dict[str, Any]] = None


def _mask_key(enc) -> Optional[str]:
    """脱敏展示 API Key 前6后4,无密钥返回 None。"""
    if not enc:
        return None
    try:
        from backend.utils.encryption import decrypt_private_key
        raw = decrypt_private_key(enc) or ""
    except Exception:
        return None
    if len(raw) <= 10:
        return raw[:2] + "***"
    return f"{raw[:6]}...{raw[-4:]}"


@router.get("/credentials")
async def list_credentials(
    request: Request,
    account_id: int = Query(default=0),
):
    """列出当前登录用户的交易所凭证（不返回密钥明文）。"""
    from backend.core.request_identity import require_user_tenant

    uid, _tid = require_user_tenant(request)
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ExchangeCredential
        db = SessionLocal()
        try:
            q = db.query(ExchangeCredential).filter(ExchangeCredential.user_id == uid)
            if account_id > 0:
                q = q.filter(ExchangeCredential.account_id == account_id)
            creds = q.all()
            return [
                {
                    "id": c.id,
                    "account_id": c.account_id,
                    "user_id": c.user_id,
                    "exchange": c.exchange,
                    "label": c.label,
                    "testnet": c.testnet,
                    "enabled": c.enabled,
                    "has_key": bool(c.api_key_encrypted),
                    "has_secret": bool(c.api_secret_encrypted),
                    "has_passphrase": bool(c.passphrase_encrypted),
                    "proxy_url": c.proxy_url,
                    "api_key_masked": _mask_key(c.api_key_encrypted),
                    # [2026-09] 积分开关（仅 asterdex 有意义）
                    "points_enabled": bool(getattr(c, "points_enabled", False)),
                    "points_config": getattr(c, "points_config", None),
                    "created_at": str(c.created_at) if c.created_at else None,
                }
                for c in creds
            ]
        finally:
            db.close()
    except Exception as e:
        logger.error("[Exchange] list_credentials error: %s", e)
        return []


@router.post("/credentials")
async def save_credential(body: CredentialCreate, request: Request):
    """添加或更新当前登录用户的交易所凭证。"""
    from backend.core.request_identity import require_user_tenant

    uid, tid = require_user_tenant(request)
    if body.exchange not in [e["id"] for e in SUPPORTED_EXCHANGES if e["id"] != "hyperliquid"]:
        raise HTTPException(400, f"Unsupported exchange: {body.exchange}. Hyperliquid uses its own wallet system.")

    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ExchangeCredential
        from backend.utils.encryption import encrypt_private_key, decrypt_private_key
        db = SessionLocal()
        try:
            existing = None
            # [2026-08-28] 编辑语义: 携带 id 时精确命中该凭证(多API场景)
            if body.id:
                existing = db.query(ExchangeCredential).filter(
                    ExchangeCredential.id == body.id,
                    ExchangeCredential.user_id == uid,
                ).first()
            if existing is None:
                existing = db.query(ExchangeCredential).filter(
                    ExchangeCredential.user_id == uid,
                    ExchangeCredential.exchange == body.exchange,
                ).first()
            if not existing and body.account_id:
                existing = db.query(ExchangeCredential).filter(
                    ExchangeCredential.user_id == uid,
                    ExchangeCredential.account_id == body.account_id,
                    ExchangeCredential.exchange == body.exchange,
                ).first()

            enc_key = encrypt_private_key(body.api_key) if body.api_key else ""
            enc_secret = encrypt_private_key(body.api_secret) if body.api_secret else ""
            enc_pass = encrypt_private_key(body.passphrase) if body.passphrase else ""

            if existing:
                old_account_id = existing.account_id or 0
                existing.label = body.label
                existing.proxy_url = (body.proxy_url or "").strip() or None
                # [2026-08-28] 编辑语义: 密钥字段留空=保持不变(不清空已有密钥)
                if body.api_key:
                    existing.api_key_encrypted = enc_key
                if body.api_secret:
                    existing.api_secret_encrypted = enc_secret
                if body.passphrase:
                    existing.passphrase_encrypted = enc_pass
                existing.testnet = body.testnet
                existing.enabled = body.enabled
                existing.user_id = uid
                # [2026-09] 积分开关（仅 asterdex 凭证生效）
                if body.exchange == "asterdex":
                    if body.points_enabled is not None:
                        existing.points_enabled = bool(body.points_enabled)
                    if body.points_config is not None:
                        existing.points_config = body.points_config or None
                # [2026-08-28] 允许 POST 重新绑定账户(等效 /bind 端点)
                if body.account_id is not None:
                    existing.account_id = body.account_id
                # DB 列 tenant_id（迁移 0004）若存在则 stamp，避免 NULL=全局可见
                if hasattr(existing, "tenant_id"):
                    existing.tenant_id = tid
                db.commit()
                cred_id = existing.id
            else:
                kwargs = dict(
                    account_id=body.account_id,
                    user_id=uid,
                    exchange=body.exchange,
                    label=body.label,
                    proxy_url=(body.proxy_url or "").strip() or None,
                    api_key_encrypted=enc_key,
                    api_secret_encrypted=enc_secret,
                    passphrase_encrypted=enc_pass,
                    testnet=body.testnet,
                    enabled=body.enabled,
                    # [2026-09] 积分开关：仅 asterdex 凭证生效，其它所恒 False/None
                    points_enabled=(
                        bool(body.points_enabled) if body.exchange == "asterdex" else False
                    ),
                    points_config=(
                        (body.points_config or None) if body.exchange == "asterdex" else None
                    ),
                )
                if "tenant_id" in ExchangeCredential.__table__.columns:
                    kwargs["tenant_id"] = tid
                cred = ExchangeCredential(**kwargs)
                db.add(cred)
                db.commit()
                db.refresh(cred)
                cred_id = cred.id

            # 缓存刷新: 移除旧绑定键,按最终绑定重建客户端
            final_account_id = (existing.account_id if existing else body.account_id) or 0
            mgr = _get_manager()
            if existing is not None and old_account_id != final_account_id:
                mgr.remove_client(body.exchange, old_account_id)
            if body.enabled:
                # 编辑场景: 空密钥=沿用已存密钥(解密回填,避免清空客户端缓存)
                _ak, _sk, _pp = body.api_key, body.api_secret, body.passphrase
                if existing is not None and (not _ak or not _sk):
                    _ak = _ak or (decrypt_private_key(existing.api_key_encrypted) if existing.api_key_encrypted else "")
                    _sk = _sk or (decrypt_private_key(existing.api_secret_encrypted) if existing.api_secret_encrypted else "")
                    _pp = _pp or (decrypt_private_key(existing.passphrase_encrypted) if existing.passphrase_encrypted else "")
                mgr.create_client(
                    exchange=body.exchange,
                    account_id=final_account_id,
                    api_key=_ak,
                    secret=_sk,
                    password=_pp,
                    testnet=body.testnet,
                    proxy_url=body.proxy_url or "",
                )

            # [2026-09-03] Aster 收益优化开关（points_enabled / maker_first / tiers）
            # 保存后立刻生效：live_points_engine 的策略缓存 60s，不失效会让
            # 用户"开了开关但下一单仍是市价"。
            if body.exchange == "asterdex":
                try:
                    from backend.services.rebate_arb.live_points_engine import live_points_engine
                    live_points_engine.invalidate_policy_cache()
                    from backend.services.rebate_arb.live_income_ledger import invalidate_ledger_cache
                    invalidate_ledger_cache()
                except Exception as _inv_err:  # noqa: BLE001
                    logger.debug("[Exchange] points policy cache invalidate skipped: %s", _inv_err)

            return {"id": cred_id, "status": "saved", "exchange": body.exchange, "user_id": uid}
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Exchange] save_credential error: %s", e)
        raise HTTPException(500, str(e))


@router.delete("/credentials/{cred_id}")
async def delete_credential(cred_id: int, request: Request):
    """删除当前用户自己的凭证。"""
    from backend.core.request_identity import require_user_tenant

    uid, _tid = require_user_tenant(request)
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ExchangeCredential
        db = SessionLocal()
        try:
            cred = db.query(ExchangeCredential).filter(
                ExchangeCredential.id == cred_id,
                ExchangeCredential.user_id == uid,
            ).first()
            if not cred:
                raise HTTPException(404, "Credential not found")
            mgr = _get_manager()
            mgr.remove_client(cred.exchange, cred.account_id or 0)
            db.delete(cred)
            db.commit()
            return {"status": "deleted", "id": cred_id}
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Exchange] delete_credential error: %s", e)
        raise HTTPException(500, str(e))


class CredentialBind(BaseModel):
    account_id: Optional[int] = None


@router.put("/credentials/{cred_id}/bind")
async def bind_credential(cred_id: int, body: CredentialBind, request: Request):
    """绑定/解绑凭证到账户(account_id=None 解绑为全局凭证)。

    [2026-08-28] 账户↔API凭证关联: 实盘执行按 account_id 优先选择凭证
    (exchange_manager.get_or_create_global_client)。
    """
    from backend.core.request_identity import require_user_tenant

    uid, _tid = require_user_tenant(request)
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ExchangeCredential
        from backend.utils.encryption import decrypt_private_key

        db = SessionLocal()
        try:
            cred = db.query(ExchangeCredential).filter(
                ExchangeCredential.id == cred_id,
                ExchangeCredential.user_id == uid,
            ).first()
            if not cred:
                raise HTTPException(404, "Credential not found")
            old_account_id = cred.account_id or 0
            cred.account_id = body.account_id
            db.commit()

            mgr = _get_manager()
            mgr.remove_client(cred.exchange, old_account_id)
            if cred.enabled:
                api_key = decrypt_private_key(cred.api_key_encrypted) if cred.api_key_encrypted else ""
                api_secret = decrypt_private_key(cred.api_secret_encrypted) if cred.api_secret_encrypted else ""
                passphrase = decrypt_private_key(cred.passphrase_encrypted) if cred.passphrase_encrypted else ""
                mgr.create_client(
                    exchange=cred.exchange,
                    account_id=cred.account_id or 0,
                    api_key=api_key,
                    secret=api_secret,
                    password=passphrase,
                    testnet=cred.testnet,
                    proxy_url=cred.proxy_url or "",
                )
            return {
                "status": "bound",
                "id": cred.id,
                "account_id": cred.account_id,
                "exchange": cred.exchange,
            }
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Exchange] bind_credential error: %s", e)
        raise HTTPException(500, str(e))


class ProxyConfigCreate(BaseModel):
    name: str
    proxy_url: str
    note: str = ""


def _probe_egress(proxy_url: str) -> str:
    """经指定代理探测公网出口 IP(3s超时,含socks5需PySocks);失败返回空串。"""
    import urllib.request as _urllib

    try:
        opener = _urllib.build_opener(_urllib.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        with opener.open("https://api.ipify.org?format=json", timeout=3) as r:
            return json.loads(r.read().decode("utf-8", "replace")).get("ip") or ""
    except Exception:
        return ""


@router.get("/proxy-configs")
def list_proxy_configs():
    """列出已配置的代理(设置→Socks5代理)。"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ExchangeProxyConfig
        db = SessionLocal()
        try:
            return [
                {
                    "id": c.id, "name": c.name, "proxy_url": c.proxy_url,
                    "egress_ip": c.egress_ip, "note": c.note, "enabled": c.enabled,
                    "created_at": str(c.created_at) if c.created_at else None,
                }
                for c in db.query(ExchangeProxyConfig).order_by(ExchangeProxyConfig.id).all()
            ]
        finally:
            db.close()
    except Exception as e:
        logger.error("[Exchange] list_proxy_configs error: %s", e)
        return []


@router.post("/proxy-configs")
def save_proxy_config(body: ProxyConfigCreate):
    """添加代理配置(同 URL 去重更新)。"""
    name = (body.name or "").strip()
    url = (body.proxy_url or "").strip()
    if not name or not url:
        raise HTTPException(400, "需要 name 和 proxy_url")
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ExchangeProxyConfig
        db = SessionLocal()
        try:
            row = db.query(ExchangeProxyConfig).filter(
                ExchangeProxyConfig.proxy_url == url,
            ).first()
            if row:
                row.name = name
                row.note = (body.note or "")[:255]
                db.commit()
                return {"id": row.id, "status": "updated"}
            row = ExchangeProxyConfig(name=name, proxy_url=url, note=(body.note or "")[:255])
            db.add(row)
            db.commit()
            db.refresh(row)
            return {"id": row.id, "status": "created"}
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Exchange] save_proxy_config error: %s", e)
        raise HTTPException(500, str(e))


@router.delete("/proxy-configs/{cfg_id}")
def delete_proxy_config(cfg_id: int):
    """删除代理配置。"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ExchangeProxyConfig
        db = SessionLocal()
        try:
            row = db.query(ExchangeProxyConfig).filter(ExchangeProxyConfig.id == cfg_id).first()
            if not row:
                raise HTTPException(404, "Proxy config not found")
            db.delete(row)
            db.commit()
            return {"status": "deleted", "id": cfg_id}
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Exchange] delete_proxy_config error: %s", e)
        raise HTTPException(500, str(e))


@router.post("/proxy-configs/{cfg_id}/test")
def test_proxy_config(cfg_id: int):
    """探测该代理的出口 IP 并回写(供币安 IP 白名单)。"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ExchangeProxyConfig
        db = SessionLocal()
        try:
            row = db.query(ExchangeProxyConfig).filter(ExchangeProxyConfig.id == cfg_id).first()
            if not row:
                raise HTTPException(404, "Proxy config not found")
            ip = _probe_egress(row.proxy_url)
            if ip:
                row.egress_ip = ip
                db.commit()
            return {"ok": bool(ip), "egress_ip": ip or None, "proxy_url": row.proxy_url}
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Exchange] test_proxy_config error: %s", e)
        raise HTTPException(500, str(e))


@router.post("/credentials/{cred_id}/test")
async def test_credential(cred_id: int, request: Request):
    """测试交易所连接。"""
    from backend.core.request_identity import require_user_tenant

    uid, _tid = require_user_tenant(request)
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ExchangeCredential
        from backend.utils.encryption import decrypt_private_key
        db = SessionLocal()
        try:
            cred = db.query(ExchangeCredential).filter(
                ExchangeCredential.id == cred_id,
                ExchangeCredential.user_id == uid,
            ).first()
            if not cred:
                raise HTTPException(404, "Credential not found")

            api_key = decrypt_private_key(cred.api_key_encrypted) if cred.api_key_encrypted else ""
            api_secret = decrypt_private_key(cred.api_secret_encrypted) if cred.api_secret_encrypted else ""
            passphrase = decrypt_private_key(cred.passphrase_encrypted) if cred.passphrase_encrypted else ""

            mgr = _get_manager()
            client = mgr.create_client(
                exchange=cred.exchange,
                account_id=cred.account_id,
                api_key=api_key,
                secret=api_secret,
                password=passphrase,
                testnet=cred.testnet,
            )
            result = await mgr.check_health(cred.exchange, cred.account_id)
            return result
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Exchange] test_credential error: %s", e)
        return {"exchange": "unknown", "connected": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  Exchange Status
# ════════════════════════════════════════════════════════

@router.get("/statuses")
async def get_exchange_statuses():
    """获取所有已配置交易所的连接状态。"""
    mgr = _get_manager()

    hl_status = {
        "exchange": "hyperliquid",
        "connected": True,
        "supports_spot": False,
        "supports_futures": True,
        "total_equity": None,
        "available_balance": None,
    }
    try:
        from backend.services.exchange_config import get_active_exchange
        get_active_exchange()
    except Exception:
        hl_status["connected"] = False

    statuses = [hl_status]

    health = await mgr.check_all_health()
    for h in health:
        statuses.append({
            "exchange": h.get("exchange", ""),
            "connected": h.get("connected", False),
            "supports_spot": True,
            "supports_futures": True,
            "total_equity": h.get("total_equity"),
            "available_balance": h.get("available_balance"),
            "error": h.get("error"),
        })

    return statuses


# ════════════════════════════════════════════════════════
#  Balance & Positions
# ════════════════════════════════════════════════════════

@router.get("/{exchange}/balance")
async def get_exchange_balance(exchange: str, account_id: int = Query(default=0)):
    """获取指定交易所余额。"""
    mgr = _get_manager()
    client = mgr.get_client(exchange, account_id)
    if client is None:
        raise HTTPException(404, f"Exchange {exchange} not configured")
    try:
        bal = await client.get_balance()
        return {
            "exchange": exchange,
            "total_equity": bal.total_equity,
            "available_balance": bal.available_balance,
            "frozen_margin": bal.frozen_margin,
            "unrealized_pnl": bal.unrealized_pnl,
            "margin_ratio": bal.margin_ratio,
        }
    except Exception as e:
        logger.error("[Exchange] balance error: %s", e)
        raise HTTPException(500, str(e))


@router.get("/{exchange}/positions")
async def get_exchange_positions(exchange: str, account_id: int = Query(default=0)):
    """获取指定交易所持仓。"""
    mgr = _get_manager()
    client = mgr.get_client(exchange, account_id)
    if client is None:
        raise HTTPException(404, f"Exchange {exchange} not configured")
    try:
        positions = await client.get_positions()
        return [
            {
                "symbol": p.symbol,
                "side": p.side,
                "size": p.size,
                "entry_price": p.entry_price,
                "mark_price": p.mark_price,
                "unrealized_pnl": p.unrealized_pnl,
                "leverage": p.leverage,
                "exchange": exchange,
            }
            for p in positions
        ]
    except Exception as e:
        logger.error("[Exchange] positions error: %s", e)
        raise HTTPException(500, str(e))


@router.get("/positions/all")
async def get_all_positions():
    """获取跨交易所统一持仓视图。"""
    mgr = _get_manager()
    all_positions: List[Dict] = []

    tasks = []
    for key, client in mgr.get_all_clients().items():
        exchange = key.split(":")[0]
        tasks.append((exchange, client.get_positions()))

    if tasks:
        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
        for (exchange, _), result in zip(tasks, results):
            if isinstance(result, list):
                for p in result:
                    all_positions.append({
                        "symbol": p.symbol,
                        "side": p.side,
                        "size": p.size,
                        "entry_price": p.entry_price,
                        "unrealized_pnl": p.unrealized_pnl,
                        "leverage": p.leverage,
                        "exchange": exchange,
                    })

    return all_positions


# ════════════════════════════════════════════════════════
#  Unified Asset View
# ════════════════════════════════════════════════════════

@router.get("/unified-assets")
async def get_unified_assets():
    """跨交易所统一资产视图 — dashboard 用。"""
    mgr = _get_manager()
    clients = mgr.get_all_clients()

    exchange_assets = []
    grand_total = 0.0
    grand_available = 0.0

    tasks = []
    for key, client in clients.items():
        exchange = key.split(":")[0]
        account_id = int(key.split(":")[1]) if ":" in key else 0
        tasks.append((exchange, account_id, client.get_balance()))

    if tasks:
        results = await asyncio.gather(
            *[t[2] for t in tasks], return_exceptions=True
        )
        for (exchange, account_id, _), result in zip(tasks, results):
            if hasattr(result, "total_equity"):
                bal = result
                grand_total += bal.total_equity
                grand_available += bal.available_balance
                exchange_assets.append({
                    "exchange": exchange,
                    "account_id": account_id,
                    "total_equity": round(bal.total_equity, 2),
                    "available_balance": round(bal.available_balance, 2),
                    "frozen_margin": round(bal.frozen_margin, 2),
                    "margin_ratio": round(bal.margin_ratio * 100, 2),
                })
            else:
                exchange_assets.append({
                    "exchange": exchange,
                    "account_id": account_id,
                    "total_equity": 0,
                    "error": str(result),
                })

    return {
        "grand_total_equity": round(grand_total, 2),
        "grand_available_balance": round(grand_available, 2),
        "exchange_count": len(exchange_assets),
        "exchanges": exchange_assets,
    }
