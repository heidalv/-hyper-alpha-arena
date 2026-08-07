"""
实盘交易 API — 参照模拟交易模块设计

数据全部走统一交易所 adapter（Asterdex/Binance/OKX/Hyperliquid）：
- GET  /api/live/accounts            实盘账户列表 + API Key 状态
- GET  /api/live/balance/{id}        账户余额 + 持仓聚合
- GET  /api/live/positions/{id}      实时持仓
- GET  /api/live/orders/{id}         挂单列表
- POST /api/live/order               手动下单（市价/限价 + TP/SL）
- POST /api/live/close               平仓（reduce-only 市价）

安全：账户停用或未配置 API Key 时拒绝下单。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database.connection import get_db
from backend.database.models import Account
from backend.services.exchange.base_exchange_client import (
    ExchangeOrder,
    OrderSide,
    OrderType,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/live", tags=["Live Trading"])

_POINTS_PERSIST_MIN_INTERVAL = timedelta(minutes=10)

_KEY_ENV_BY_EXCHANGE = {
    "asterdex": ("ASTERDEX_API_KEY", "ASTERDEX_API_SECRET"),
    "binance": ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
    "okx": ("OKX_API_KEY", "OKX_API_SECRET"),
    "bybit": ("BYBIT_API_KEY", "BYBIT_API_SECRET"),
    "gateio": ("GATEIO_API_KEY", "GATEIO_API_SECRET"),
    "hyperliquid": ("HYPERLIQUID_API_KEY", "HYPERLIQUID_API_SECRET"),
}


def _normalize_exchange(exchange: Optional[str]) -> str:
    ex = (exchange or "asterdex").strip().lower()
    return "asterdex" if ex == "aster" else ex


def _as_bool(v) -> bool:
    """兼容字符串布尔（'true'/'false'）与原生 bool。"""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


def _keys_configured(exchange: str) -> bool:
    pair = _KEY_ENV_BY_EXCHANGE.get(_normalize_exchange(exchange))
    if not pair:
        return False
    return bool(os.getenv(pair[0]) and os.getenv(pair[1]))


def _ccxt_symbol(exchange: str, symbol: str) -> str:
    base = (symbol or "").upper().split("-")[0].split("/")[0]
    if _normalize_exchange(exchange) == "hyperliquid":
        return f"{base}/USDC:USDC"
    return f"{base}/USDT:USDT"


def _get_account(db: Session, account_id: int) -> Account:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="账户不存在")
    return account


def _get_client(account: Account):
    """获取账户对应交易所的 adapter 客户端。"""
    exchange = _normalize_exchange(
        getattr(account, "selected_exchange", None) or "asterdex"
    )
    try:
        from backend.services.exchange.exchange_manager import get_exchange_manager
        mgr = get_exchange_manager()
        client = mgr.get_client(exchange, getattr(account, "id", 0) or 0)
        if client is not None:
            return client, exchange
    except Exception as exc:
        logger.warning("[Live] get_client %s failed: %s", exchange, exc)
    raise HTTPException(status_code=503, detail=f"交易所客户端不可用: {exchange}")


def _serialize_position(p) -> Dict[str, Any]:
    sym = str(getattr(p, "symbol", "") or "")
    base = sym.split("/")[0].split("-")[0].upper()
    return {
        "symbol": base,
        "side": getattr(p, "side", ""),
        "size": float(getattr(p, "size", 0) or 0),
        "entry_price": float(getattr(p, "entry_price", 0) or 0),
        "mark_price": float(getattr(p, "mark_price", 0) or 0),
        "unrealized_pnl": float(getattr(p, "unrealized_pnl", 0) or 0),
        "margin": float(getattr(p, "margin", 0) or 0),
        "leverage": float(getattr(p, "leverage", 1) or 1),
        "liquidation_price": (
            float(getattr(p, "liquidation_price", 0) or 0)
            if getattr(p, "liquidation_price", None)
            else None
        ),
    }


def _serialize_account(account: Account) -> Dict[str, Any]:
    exchange = _normalize_exchange(getattr(account, "selected_exchange", None) or "asterdex")
    return {
        "id": account.id,
        "name": getattr(account, "name", ""),
        "trading_mode": getattr(account, "trading_mode", "live"),
        "exchange": exchange,
        "is_active": _as_bool(getattr(account, "is_active", False)),
        "auto_trading_enabled": _as_bool(getattr(account, "auto_trading_enabled", False)),
        "keys_configured": _keys_configured(exchange),
    }


def _persist_points_snapshot(db: Session, account_id: int, summary) -> None:
    """积分/激励快照落库（节流：同一交易所 10 分钟内只写一次），供「积分记录」查询。"""
    try:
        from backend.database.models import RebateIncentiveSnapshotDB
        now = datetime.now(timezone.utc)
        last = (
            db.query(RebateIncentiveSnapshotDB)
            .filter(RebateIncentiveSnapshotDB.exchange == "asterdex")
            .order_by(RebateIncentiveSnapshotDB.snapshot_time.desc())
            .first()
        )
        if last and last.snapshot_time:
            last_dt = last.snapshot_time
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if now - last_dt < _POINTS_PERSIST_MIN_INTERVAL:
                return
        import json as _json
        row = RebateIncentiveSnapshotDB(
            exchange="asterdex",
            snapshot_time=now.replace(tzinfo=None),
            fee_tier_name=getattr(summary.fee_tier, "tier_name", "pro"),
            maker_rate=float(getattr(summary.fee_tier, "maker_rate", 0) or 0),
            taker_rate=float(getattr(summary.fee_tier, "taker_rate", 0) or 0),
            rebate_rate=float(getattr(summary.rebate, "current_rebate_rate", 0) or 0),
            points_balance=float(getattr(summary.points, "points_balance", 0) or 0),
            points_multiplier=float(getattr(summary.points, "points_multiplier", 1) or 1),
            volume_30d=float(getattr(summary.fee_tier, "volume_30d_usd", 0) or 0),
            data_json=_json.dumps({
                "account_id": account_id,
                "season": getattr(summary.points, "season", ""),
                "qualifying_days": getattr(summary.points, "qualifying_days", 0),
                "required_days": getattr(summary.points, "required_days", 2),
                "daily_points_rate": getattr(summary.points, "daily_points_rate", 0),
                "airdrop_eligible": bool(getattr(summary.points, "airdrop_eligible", False)),
                "estimated_airdrop_value": float(getattr(summary.points, "estimated_airdrop_value", 0) or 0),
                "volume_7d": float(getattr(summary.rebate, "trading_volume_7d", 0) or 0),
                "projected_weekly_rebate": float(getattr(summary.rebate, "projected_weekly_rebate", 0) or 0),
            }, ensure_ascii=False),
        )
        db.add(row)
        db.commit()
    except Exception as exc:
        logger.warning("[Live] points snapshot persist failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


@router.get("/accounts")
def list_live_accounts(db: Session = Depends(get_db)):
    """实盘账户列表（含 API Key 配置状态）。"""
    accounts = db.query(Account).filter(Account.trading_mode == "live").all()
    return {"accounts": [_serialize_account(a) for a in accounts]}


@router.get("/readiness")
def live_readiness(db: Session = Depends(get_db)):
    """M7 实盘就绪检查：五项全绿才允许实盘开关。"""
    accounts = db.query(Account).filter(Account.trading_mode == "live").all()
    if not accounts:
        return {
            "ready": False,
            "checks": {
                "api_keys": False,
                "account_active": False,
                "c7_reconcile_drift": -1,
                "conditional_order_backtest": "not_run",
                "promotion_samples": "insufficient",
            },
            "message": "无实盘账户",
        }
    keys_ok = all(
        _keys_configured(_normalize_exchange(getattr(a, "selected_exchange", None) or "asterdex"))
        for a in accounts
    )
    active_ok = any(_as_bool(getattr(a, "is_active", False)) for a in accounts)
    checks = {
        "api_keys": keys_ok,
        "account_active": active_ok,
        "c7_reconcile_drift": -1,          # 待事件源对账清零（-1=未验证）
        "conditional_order_backtest": "not_run",
        "promotion_samples": "insufficient",
    }
    return {
        "ready": False,  # 任何未验证项都不允许实盘
        "checks": checks,
        "message": "实盘前必须：配置Key、启用账户、C7对账清零、条件单回测通过、晋升样本充足",
    }


@router.get("/balance/{account_id}")
async def get_live_balance(account_id: int, db: Session = Depends(get_db)):
    account = _get_account(db, account_id)
    exchange = _normalize_exchange(getattr(account, "selected_exchange", None) or "asterdex")
    if not _keys_configured(exchange):
        return {
            "account_id": account_id,
            "exchange": exchange,
            "total_equity": 0,
            "available_balance": 0,
            "frozen_margin": 0,
            "unrealized_pnl": 0,
            "position_count": 0,
            "keys_configured": False,
            "updated_at": None,
            "message": "未配置 API Key",
        }
    client, exchange = _get_client(account)
    try:
        bal = await client.get_balance()
        positions = await client.get_positions()
    except Exception as exc:
        logger.warning("[Live] balance fetch failed account=%s: %s", account_id, exc)
        raise HTTPException(status_code=502, detail=f"获取余额失败: {str(exc)[:120]}")

    upnl = float(getattr(bal, "unrealized_pnl", 0) or 0)
    margin = float(getattr(bal, "frozen_margin", 0) or 0)
    for p in positions or []:
        upnl += float(getattr(p, "unrealized_pnl", 0) or 0)
        margin += float(getattr(p, "margin", 0) or 0)
    equity = float(getattr(bal, "total_equity", 0) or 0)
    return {
        "account_id": account_id,
        "exchange": exchange,
        "total_equity": round(equity, 2),
        "available_balance": round(float(getattr(bal, "available_balance", 0) or 0), 2),
        "frozen_margin": round(margin, 2),
        "unrealized_pnl": round(upnl, 4),
        "position_count": len(positions or []),
        "keys_configured": _keys_configured(exchange),
        "updated_at": None,
    }


@router.get("/positions/{account_id}")
async def get_live_positions(account_id: int, db: Session = Depends(get_db)):
    account = _get_account(db, account_id)
    exchange = _normalize_exchange(getattr(account, "selected_exchange", None) or "asterdex")
    if not _keys_configured(exchange):
        return {"positions": [], "exchange": exchange, "keys_configured": False, "message": "未配置 API Key"}
    client, exchange = _get_client(account)
    try:
        positions = await client.get_positions()
    except Exception as exc:
        logger.warning("[Live] positions fetch failed account=%s: %s", account_id, exc)
        raise HTTPException(status_code=502, detail=f"获取持仓失败: {str(exc)[:120]}")
    rows = [_serialize_position(p) for p in (positions or [])]
    # 叠加数据中心最新价
    try:
        from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
        for r in rows:
            live = asterdex_ticker_poller.get_price(r["symbol"])
            if live and live > 0:
                r["last_price"] = round(float(live), 8)
    except Exception:
        pass
    return {"positions": rows, "exchange": exchange}


@router.get("/orders/{account_id}")
async def get_live_orders(account_id: int, db: Session = Depends(get_db)):
    account = _get_account(db, account_id)
    exchange = _normalize_exchange(getattr(account, "selected_exchange", None) or "asterdex")
    if not _keys_configured(exchange):
        return {"orders": [], "exchange": exchange, "keys_configured": False, "message": "未配置 API Key"}
    client, exchange = _get_client(account)
    orders: List[Dict[str, Any]] = []
    try:
        raw_ex = getattr(client, "_exchange", None)
        if raw_ex is not None and hasattr(raw_ex, "fetch_open_orders"):
            raw = await raw_ex.fetch_open_orders()
            for o in raw or []:
                sym = str(o.get("symbol") or "")
                orders.append({
                    "id": str(o.get("id") or ""),
                    "symbol": sym.split("/")[0].upper(),
                    "side": o.get("side", ""),
                    "type": o.get("type", ""),
                    "price": float(o.get("price") or 0),
                    "amount": float(o.get("amount") or 0),
                    "filled": float(o.get("filled") or 0),
                    "status": o.get("status", ""),
                    "timestamp": o.get("timestamp"),
                })
    except Exception as exc:
        logger.warning("[Live] orders fetch failed account=%s: %s", account_id, exc)
    return {"orders": orders, "exchange": exchange}


@router.post("/order")
async def place_live_order(payload: dict, db: Session = Depends(get_db)):
    account_id = int(payload.get("account_id") or 0)
    account = _get_account(db, account_id)
    if not _as_bool(getattr(account, "is_active", False)):
        raise HTTPException(status_code=403, detail="实盘账户已停用，禁止下单")
    exchange = _normalize_exchange(getattr(account, "selected_exchange", None) or "asterdex")
    if not _keys_configured(exchange):
        raise HTTPException(status_code=400, detail=f"{exchange} 未配置 API Key，无法实盘下单")

    symbol = str(payload.get("symbol") or "").upper().strip()
    side = str(payload.get("side") or "").lower()
    if not symbol or side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="symbol/side 参数错误")
    try:
        quantity = float(payload.get("quantity") or 0)
        leverage = int(float(payload.get("leverage") or 1))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="quantity/leverage 参数错误")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity 必须大于 0")

    order_type = str(payload.get("order_type") or "market").lower()
    if order_type not in ("market", "limit"):
        raise HTTPException(status_code=400, detail="order_type 仅支持 market/limit")
    price = None
    if order_type == "limit":
        try:
            price = float(payload.get("price") or 0)
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            raise HTTPException(status_code=400, detail="限价单需要 price")

    tp = None
    sl = None
    for key in ("tp_price", "tp", "take_profit"):
        if payload.get(key):
            try:
                tp = float(payload[key])
                break
            except (TypeError, ValueError):
                pass
    for key in ("sl_price", "sl", "stop_loss"):
        if payload.get(key):
            try:
                sl = float(payload[key])
                break
            except (TypeError, ValueError):
                pass

    client, _ = _get_client(account)
    order = ExchangeOrder(
        order_id=f"manual_{account_id}_{int(__import__('time').time() * 1000)}",
        symbol=_ccxt_symbol(exchange, symbol),
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        order_type=OrderType.MARKET if order_type == "market" else OrderType.LIMIT,
        size=quantity,
        price=price,
        sl=sl,
        tp=tp,
        leverage=leverage,
    )
    try:
        result = await client.place_order(order)
    except Exception as exc:
        logger.error("[Live] place_order failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"下单失败: {str(exc)[:160]}")
    return {
        "success": result.get("status") != "error",
        "result": result,
        "symbol": symbol,
        "side": side,
        "exchange": exchange,
    }


@router.post("/close")
async def close_live_position(payload: dict, db: Session = Depends(get_db)):
    account_id = int(payload.get("account_id") or 0)
    account = _get_account(db, account_id)
    if not _as_bool(getattr(account, "is_active", False)):
        raise HTTPException(status_code=403, detail="实盘账户已停用，禁止交易")
    exchange = _normalize_exchange(getattr(account, "selected_exchange", None) or "asterdex")
    if not _keys_configured(exchange):
        raise HTTPException(status_code=400, detail=f"{exchange} 未配置 API Key，无法实盘平仓")

    symbol = str(payload.get("symbol") or "").upper().strip()
    side = str(payload.get("side") or "").lower()  # 持仓方向 long/short
    if not symbol or side not in ("long", "short"):
        raise HTTPException(status_code=400, detail="symbol/side(long/short) 参数错误")
    quantity = None
    if payload.get("quantity"):
        try:
            quantity = float(payload["quantity"])
        except (TypeError, ValueError):
            quantity = None

    client, _ = _get_client(account)
    if not quantity:
        try:
            positions = await client.get_positions()
            ccxt_sym = _ccxt_symbol(exchange, symbol)
            for p in positions or []:
                if str(p.symbol).split("/")[0].upper() == symbol:
                    quantity = float(p.size)
                    break
        except Exception:
            pass
    if not quantity or quantity <= 0:
        raise HTTPException(status_code=400, detail="未找到持仓数量，请显式传入 quantity")

    close_side = OrderSide.SELL if side == "long" else OrderSide.BUY
    order = ExchangeOrder(
        order_id=f"close_{account_id}_{int(__import__('time').time() * 1000)}",
        symbol=_ccxt_symbol(exchange, symbol),
        side=close_side,
        order_type=OrderType.MARKET,
        size=quantity,
        leverage=1,
        reduce_only=True,
    )
    try:
        result = await client.place_order(order)
    except Exception as exc:
        logger.error("[Live] close_position failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"平仓失败: {str(exc)[:160]}")
    return {
        "success": result.get("status") != "error",
        "result": result,
        "symbol": symbol,
        "side": side,
        "exchange": exchange,
    }


@router.get("/asterdex/points/{account_id}")
async def get_asterdex_points(account_id: int, db: Session = Depends(get_db)):
    """Asterdex 合约交易积分 + 收益预期（含历史记录）。

    数据来源：Asterdex Rh 积分 API + 费率/返佣 API（经统一 adapter 聚合）。
    收益预期：
      - 返佣：7日交易量 × 当前返佣率 → 周/月/年化
      - 积分：日积分率 → 周/月积分；空投预估价值来自交易所
    """
    account = _get_account(db, account_id)
    exchange = _normalize_exchange(getattr(account, "selected_exchange", None) or "asterdex")
    if exchange != "asterdex":
        raise HTTPException(status_code=400, detail="仅 asterdex 交易所支持积分查询")
    if not _keys_configured("asterdex"):
        return {
            "keys_configured": False,
            "message": "未配置 Asterdex API Key",
            "points": None,
            "projection": None,
            "history": [],
        }

    client, _ = _get_client(account)
    try:
        summary = await client.get_incentive_summary()
    except Exception as exc:
        logger.warning("[Live] asterdex points fetch failed account=%s: %s", account_id, exc)
        raise HTTPException(status_code=502, detail=f"获取积分数据失败: {str(exc)[:150]}")

    pts = summary.points
    fee = summary.fee_tier
    reb = summary.rebate

    weekly_rebate = float(getattr(reb, "projected_weekly_rebate", 0) or 0)
    monthly_rebate = weekly_rebate * 4.33
    yearly_rebate = weekly_rebate * 52
    daily_points = float(getattr(pts, "daily_points_rate", 0) or 0)
    volume_7d = float(getattr(reb, "trading_volume_7d", 0) or 0)
    multiplier = float(getattr(pts, "points_multiplier", 1) or 1)

    # 若交易所未给日积分率，用「7日交易量 × 乘数 × 0.001」作保守估算（标注 estimated）
    estimated_daily_points = daily_points
    points_estimated = False
    if daily_points <= 0 and volume_7d > 0:
        estimated_daily_points = volume_7d / 7.0 * multiplier * 0.001
        points_estimated = True

    points_data = {
        "points_balance": round(float(getattr(pts, "points_balance", 0) or 0), 2),
        "points_multiplier": multiplier,
        "season": getattr(pts, "season", "") or "",
        "qualifying_days": int(getattr(pts, "qualifying_days", 0) or 0),
        "required_days": int(getattr(pts, "required_days", 2) or 2),
        "qualification_pct": round(float(getattr(pts, "qualification_pct", 0) or 0), 4),
        "airdrop_eligible": bool(getattr(pts, "airdrop_eligible", False)),
        "estimated_airdrop_value": round(float(getattr(pts, "estimated_airdrop_value", 0) or 0), 2),
        "daily_points_rate": round(daily_points, 4),
    }
    projection = {
        "volume_7d_usd": round(volume_7d, 2),
        "rebate_rate": round(float(getattr(reb, "current_rebate_rate", 0) or 0), 8),
        "weekly_rebate_usd": round(weekly_rebate, 2),
        "monthly_rebate_usd": round(monthly_rebate, 2),
        "yearly_rebate_usd": round(yearly_rebate, 2),
        "daily_points": round(estimated_daily_points, 4),
        "points_estimated": points_estimated,
        "weekly_points": round(estimated_daily_points * 7, 2),
        "monthly_points": round(estimated_daily_points * 30, 2),
        "total_estimated_monthly_value": round(
            float(getattr(summary, "total_estimated_monthly_value", 0) or 0), 2
        ),
    }

    try:
        _persist_points_snapshot(db, account_id, summary)
    except Exception:
        pass

    history: List[Dict[str, Any]] = []
    try:
        from backend.database.models import RebateIncentiveSnapshotDB
        rows = (
            db.query(RebateIncentiveSnapshotDB)
            .filter(RebateIncentiveSnapshotDB.exchange == "asterdex")
            .order_by(RebateIncentiveSnapshotDB.snapshot_time.desc())
            .limit(30)
            .all()
        )
        for r in rows:
            import json as _json
            meta = {}
            try:
                meta = _json.loads(r.data_json or "{}")
            except Exception:
                pass
            history.append({
                "snapshot_time": str(r.snapshot_time),
                "points_balance": round(float(r.points_balance or 0), 2),
                "points_multiplier": float(r.points_multiplier or 1),
                "airdrop_eligible": bool(meta.get("airdrop_eligible", False)),
                "estimated_airdrop_value": round(float(meta.get("estimated_airdrop_value", 0) or 0), 2),
                "volume_7d_usd": round(float(meta.get("volume_7d", 0) or 0), 2),
                "rebate_rate": float(r.rebate_rate or 0),
            })
    except Exception as exc:
        logger.debug("[Live] points history query failed: %s", exc)

    return {
        "keys_configured": True,
        "exchange": "asterdex",
        "points": points_data,
        "projection": projection,
        "history": history,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
