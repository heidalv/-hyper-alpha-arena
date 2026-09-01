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
import asyncio
import time

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


def _credential_exists(db, account) -> bool:
    """凭证是否存在：环境变量 或 exchange_credentials 表（账户级/全局）。"""
    exchange = _normalize_exchange(
        getattr(account, "selected_exchange", None) or "asterdex"
    )
    if _keys_configured(exchange):
        return True
    try:
        from backend.database.models import ExchangeCredential

        q = db.query(ExchangeCredential).filter(
            ExchangeCredential.exchange == exchange,
            ExchangeCredential.enabled == True,  # noqa: E712
        )
        c = (
            q.filter(ExchangeCredential.account_id == account.id).first()
            or q.filter(
                (ExchangeCredential.account_id.is_(None))
                | (ExchangeCredential.account_id == 0)
            ).first()
        )
        return bool(c)
    except Exception:
        return False


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


# [2026-08-28 修复] 实盘轮询缓存：10s TTL —— 页面 3s 轮询命中缓存，避免打爆币安限速
_LIVE_POLL_CACHE: dict = {}
_LIVE_POLL_TTL = 10.0


def _live_cached(key: str):
    import time as _t
    hit = _LIVE_POLL_CACHE.get(key)
    if hit and _t.time() - hit[0] <= _LIVE_POLL_TTL:
        return hit[1]
    return None


_TPSL_ATTACH_CACHE = {"ts": 0.0, "acct": 0, "data": {}}


def _live_store(key: str, val) -> None:
    import time as _t
    _LIVE_POLL_CACHE[key] = (_t.time(), val)


# [2026-09-01 F33] stale-while-revalidate：实盘页面切换 10s+ 的根因是 10s TTL
# 过期后的同步重算（走币安 REST×代理链，冷路径 9.2s）。改为过期即返旧值 +
# 后台协程单飞刷新（独立 DB 会话），页面切换永不等币安。
_live_refreshing: set = set()
_live_refresh_lock = __import__("threading").Lock()


def _live_cached_entry(key: str):
    import time as _t
    hit = _LIVE_POLL_CACHE.get(key)
    if not hit:
        return None, False
    return hit[1], (_t.time() - hit[0] <= _LIVE_POLL_TTL)


def _spawn_live_refresh(cache_key: str, coro_fn, account_id: int) -> None:
    import asyncio as _aio
    import time as _t
    with _live_refresh_lock:
        if cache_key in _live_refreshing:
            return
        _live_refreshing.add(cache_key)

    async def _run():
        try:
            payload = await coro_fn(account_id)
            _live_store(cache_key, payload)
        except Exception as _e:
            logger.debug("[Live] 后台刷新 %s 失败(保留旧值): %s", cache_key, _e)
        finally:
            with _live_refresh_lock:
                _live_refreshing.discard(cache_key)

    try:
        _aio.get_running_loop().create_task(_run())
    except RuntimeError:
        _t.sleep(0.1)
        try:
            _aio.get_running_loop().create_task(_run())
        except RuntimeError:
            pass


def _tier_for_symbols(db, account_id: int) -> dict:
    """该账户运行中实盘会话的周期币池 {symbol: [tier...]}（fixed_symbols_by_tier）。"""
    out: dict = {}
    try:
        from backend.database.models import FullAutoSession

        sess = db.query(FullAutoSession).filter(
            FullAutoSession.account_id == account_id,
            FullAutoSession.trading_mode == "live",
            FullAutoSession.status == "running",
        ).order_by(FullAutoSession.started_at.desc()).first()
        if not sess:
            return out
        import json as _j

        raw = getattr(sess, "fixed_symbols_by_tier", None)
        if isinstance(raw, str):
            try:
                raw = _j.loads(raw)
            except Exception:
                raw = {}
        for _tier, _syms in (raw or {}).items():
            for _s in (_syms or []):
                _b = str(_s).upper().strip()
                if _b:
                    out.setdefault(_b, []).append(_tier)
    except Exception:
        pass
    return out


def _read_user_stream_snapshot() -> Optional[dict]:
    """[2026-08-28 用户数据流] 读实时快照文件（<20s 新鲜），无则 None（REST 兜底）。"""
    import json as _j
    import os as _o
    import time as _t

    try:
        _p = _o.path.join(
            _o.path.dirname(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))),
            "data", "live_user_stream_snapshot.json",
        )
        if not _o.path.exists(_p):
            return None
        if _t.time() - _o.path.getmtime(_p) > 20.0:
            return None
        with open(_p, encoding="utf-8") as _f:
            return _j.load(_f)
    except Exception:
        return None


# [2026-08-28 P2] 保证金模式切换防抖（币安 marginType 约 5s 一次）+ 订单速率限流（300/10s）
_MARGIN_TYPE_LAST_CALL: dict = {}
_ORDER_RATE_WINDOW: list = []
_RECENT_ORDER_SYMBOLS: dict = {}  # {account_id: {symbol: ts}} 最近下单/挂单的币（orders 查询用）


def _order_rate_ok() -> bool:
    import time as _t
    _now = _t.time()
    _cut = _now - 10.0
    while _ORDER_RATE_WINDOW and _ORDER_RATE_WINDOW[0] < _cut:
        _ORDER_RATE_WINDOW.pop(0)
    return len(_ORDER_RATE_WINDOW) < 300


def _maybe_client(account: Account) -> tuple:
    """返回 (client 或 None, exchange)：走 ExchangeManager（账户级/全局凭证，认凭证表）。
    [2026-08-28] 凭证可能存于 exchange_credentials（账户级 account_id / 全局 user），
    而非 .env 明文——此前 live 接口只认 .env 导致 keys_configured=false、数据空白。
    """
    exchange = _normalize_exchange(
        getattr(account, "selected_exchange", None) or "asterdex"
    )
    try:
        from backend.services.exchange.exchange_manager import get_exchange_manager
        mgr = get_exchange_manager()
        client = mgr.get_client(exchange, getattr(account, "id", 0) or 0)
        if client is None:
            market_type = getattr(account, "binance_market_type", None) or "usdt_m"
            client = mgr.get_or_create_global_client(
                exchange, user_id=account.user_id or 1, account_id=account.id,
                market_type=market_type if exchange == "binance" else None,
            )
        return client, exchange
    except Exception as exc:
        logger.warning("[Live] _maybe_client %s failed: %s", exchange, exc)
        return None, exchange


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
        "margin_type": getattr(p, "margin_type", None),       # cross/isolated
        "isolated": getattr(p, "isolated", None),             # 逐仓? 全仓 false
        "maint_margin": float(getattr(p, "maint_margin", 0) or 0),
        "margin_ratio": float(getattr(p, "margin_ratio", 0) or 0),  # 保证金率%
        "tp_price": getattr(p, "tp_price", None),             # 交易所条件单止盈价
        "sl_price": getattr(p, "sl_price", None),             # 交易所条件单止损价
    }


async def _attach_tpsl_to_rows(db, account_id: int, rows: list) -> None:
    """[2026-08-29 持仓止盈止损展示] 拉交易所 TP/SL 条件单挂到持仓行上。

    币安的 TP/SL 是独立条件单（不在 positionRisk 里），持仓页要展示必须
    单独 fetch_open_orders 匹配。失败静默（行上 tp/sl 保持 None 显示"—"），
    不影响持仓主数据。
    """
    if not rows:
        return
    # [2026-08-30 防卡顿] TP/SL 条件单 60s TTL 缓存：挂单不常变，没必要每次
    # 持仓轮询都逐 symbol 拉（3 笔仓 = 3 次 REST 经代理 ≈ 3-9s 尖刺）。
    global _TPSL_ATTACH_CACHE
    _now2 = time.time()
    if (_TPSL_ATTACH_CACHE["data"] and _TPSL_ATTACH_CACHE["acct"] == int(account_id)
            and _now2 - _TPSL_ATTACH_CACHE["ts"] <= 60.0):
        tpsl = _TPSL_ATTACH_CACHE["data"]
        for r in rows:
            _m = tpsl.get(str(r.get("symbol") or "").upper()) or {}
            if _m.get("tp"):
                r["tp_price"] = float(_m["tp"])
            if _m.get("sl"):
                r["sl_price"] = float(_m["sl"])
        return
    try:
        account = _get_account(db, account_id)
        if account is None:
            return
        client, _ex = _maybe_client(account)
        if client is None or not hasattr(client, "get_open_tpsl_orders"):
            return
        tpsl = await client.get_open_tpsl_orders(
            [str(r.get("symbol") or "").upper() for r in rows]
        )
        if not isinstance(tpsl, dict):
            return
        _TPSL_ATTACH_CACHE.update(ts=_now2, acct=int(account_id), data=tpsl)
        for r in rows:
            _m = tpsl.get(str(r.get("symbol") or "").upper()) or {}
            if _m.get("tp"):
                r["tp_price"] = float(_m["tp"])
            if _m.get("sl"):
                r["sl_price"] = float(_m["sl"])
    except Exception as exc:
        logger.debug("[Live] TP/SL 条件单拉取跳过 account=%s: %s", account_id, exc)


def _serialize_account(account: Account, db=None) -> Dict[str, Any]:
    exchange = _normalize_exchange(getattr(account, "selected_exchange", None) or "asterdex")
    keys_ok = _credential_exists(db, account) if db is not None else _keys_configured(exchange)
    return {
        "id": account.id,
        "name": getattr(account, "name", ""),
        "trading_mode": getattr(account, "trading_mode", "live"),
        "exchange": exchange,
        "is_active": _as_bool(getattr(account, "is_active", False)),
        "auto_trading_enabled": _as_bool(getattr(account, "auto_trading_enabled", False)),
        "keys_configured": keys_ok,
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
    return {"accounts": [_serialize_account(a, db) for a in accounts]}


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
    # [2026-08-28 修复] 凭证判定走凭证表（账户级/全局），且只对启用中的实盘账户要求；
    # 已停用/软删的旧 live 账户不参与门禁（此前 env 判定 + 全量账户导致永远 False）。
    _live_active = [a for a in accounts if _as_bool(getattr(a, "is_active", False))]
    keys_ok = all(_credential_exists(db, a) for a in _live_active) if _live_active else False
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


async def _compute_live_balance(account_id: int) -> Dict[str, Any]:
    """余额计算主体（独立 DB 会话，供前台/后台刷新共用）。"""
    from backend.database.connection import SessionLocal as _SL
    with _SL() as db:
        # [2026-08-30 权益数据源根治 + 防卡顿] 总权益/可用必须直接取币安 REST
        # （含持仓浮盈）。但 UI 不再各自直连 REST——与交易循环共用同一个共享
        # 快照获取器（fetch_live_account_snapshot: 币安 REST + 10s TTL），避免
        # 多路 REST 抢同一本地代理导致全线卡顿。数据仍是币安实时值。
        try:
            from backend.services.full_auto.live_trading import (
                fetch_live_account_snapshot as _fetch_snap,
            )
            import asyncio as _aio
            _snap2 = await _aio.to_thread(_fetch_snap, db, account_id)
            if _snap2 and float(_snap2.get("total_equity") or 0) > 0:
                _pos2 = _snap2.get("positions") or []
                _upnl2 = sum(float(x.get("unrealized_pnl") or 0) for x in _pos2)
                _frozen_pct2 = float(_snap2.get("margin_usage_percent") or 0)
                _eq2 = float(_snap2["total_equity"])
                _frozen2 = _frozen_pct2 * _eq2 / 100.0
                _payload = {
                    "account_id": account_id,
                    "exchange": "binance",
                    "total_equity": round(_eq2, 2),
                    "available_balance": round(float(_snap2.get("available_balance") or 0), 2),
                    "frozen_margin": round(_frozen2, 2),
                    "unrealized_pnl": round(_upnl2, 4),
                    "position_count": len(_pos2),
                    "keys_configured": True,
                    "updated_at": None,
                    "source": _snap2.get("source") or "binance_rest",
                }
                _live_store(f"bal:{account_id}", _payload)
                return _payload
        except Exception as _rest_err:
            import logging as _lg
            _lg.getLogger(__name__).warning("[Live] 共享快照获取失败，退回用户流快照: %s", _rest_err)
        # 快照兜底（REST 失败时）
        _snap = _read_user_stream_snapshot()
        if _snap and int(_snap.get("account_id") or 0) == account_id:
            _bals = _snap.get("balances") or {}
            _usdt = _bals.get("USDT") or {}
            _levmap = _snap.get("leverage_map") or {}
            _poss = _snap.get("positions") or {}
            _frozen = sum(float(v.get("initial_margin") or 0) for v in _levmap.values())
            _upnl = sum(float(v.get("up") or 0) for v in _poss.values())
            _payload = {
                "account_id": account_id,
                "exchange": "binance",
                "total_equity": round(float(_usdt.get("cw") or 0), 2),
                "available_balance": round(float(_usdt.get("wb") or 0), 2),
                "frozen_margin": round(_frozen, 2),
                "unrealized_pnl": round(_upnl, 4),
                "position_count": len([k for k, v in _poss.items() if abs(float(v.get("amt") or 0)) > 0]),
                "keys_configured": True,
                "updated_at": None,
                "source": "user_stream",
            }
            _live_store(f"bal:{account_id}", _payload)
            return _payload
        account = _get_account(db, account_id)
        # [2026-08-28] 走凭证表感知客户端
        client, exchange = _maybe_client(account)
        if client is None:
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
                "message": "未配置 API Key（凭证表/环境变量均缺失）",
            }
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
        payload = {
            "account_id": account_id,
            "exchange": exchange,
            "total_equity": round(equity, 2),
            "available_balance": round(float(getattr(bal, "available_balance", 0) or 0), 2),
            "frozen_margin": round(margin, 2),
            "unrealized_pnl": round(upnl, 4),
            "position_count": len(positions or []),
            "keys_configured": True,
            "updated_at": None,
        }
        _live_store(f"bal:{account_id}", payload)
        return payload


@router.get("/balance/{account_id}")
async def get_live_balance(account_id: int, db: Session = Depends(get_db)):
    # [2026-09-01 F33] stale-while-revalidate：过期即返旧值 + 后台单飞刷新
    _cached, _fresh = _live_cached_entry(f"bal:{account_id}")
    if _cached is not None:
        if _fresh:
            return _cached
        _spawn_live_refresh(f"bal:{account_id}", _compute_live_balance, account_id)
        return _cached
    return await _compute_live_balance(account_id)


async def _compute_live_positions(account_id: int) -> Dict[str, Any]:
    """持仓计算主体（独立 DB 会话，供前台/后台刷新共用）。"""
    from backend.database.connection import SessionLocal as _SL
    with _SL() as db:
        # [2026-08-28 用户数据流] 实时快照优先（零币安调用）
        _snap = _read_user_stream_snapshot()
        if _snap and int(_snap.get("account_id") or 0) == account_id:
            _poss = _snap.get("positions") or {}
            _levmap = _snap.get("leverage_map") or {}
            _rows = []
            for _base, _p in _poss.items():
                if abs(float(_p.get("amt") or 0)) <= 0:
                    continue
                _lv = _levmap.get(_base) or {}
                _notional = abs(float(_p.get("amt") or 0)) * float(_p.get("ep") or 0)
                _tiers = _tier_for_symbols(db, account_id).get(_base) or []
                _rows.append({
                    "symbol": _base,
                    "side": "long" if float(_p.get("amt") or 0) > 0 else "short",
                    "size": abs(float(_p.get("amt") or 0)),
                    "entry_price": float(_p.get("ep") or 0),
                    "mark_price": float(_p.get("mp") or 0) or float(_p.get("ep") or 0),
                    "unrealized_pnl": float(_p.get("up") or 0),
                    "margin": float(_lv.get("initial_margin") or 0),
                    "leverage": float(_lv.get("leverage") or 1) or 1,
                    "liquidation_price": _lv.get("liquidation_price"),
                    "margin_type": _p.get("mt") or _lv.get("margin_type"),
                    "isolated": bool(_lv.get("isolated")) if _lv.get("isolated") is not None else None,
                    "maint_margin": 0.0,
                    "margin_ratio": 0.0,
                    "tier": _tiers or None,
                    "source": "user_stream",
                    "tp_price": None,
                    "sl_price": None,
                })
            # [2026-08-29] 附交易所 TP/SL 条件单（user_stream 快照不含挂单，需 REST 拉）
            await _attach_tpsl_to_rows(db, account_id, _rows)
            _payload = {"positions": _rows, "exchange": "binance", "source": "user_stream"}
            _live_store(f"pos:{account_id}", _payload)
            return _payload
        account = _get_account(db, account_id)
        client, exchange = _maybe_client(account)
        if client is None:
            return {"positions": [], "exchange": exchange, "keys_configured": False, "message": "未配置 API Key"}
        try:
            positions = await client.get_positions()
        except Exception as exc:
            logger.warning("[Live] positions fetch failed account=%s: %s", account_id, exc)
            raise HTTPException(status_code=502, detail=f"获取持仓失败: {str(exc)[:120]}")
        rows = [_serialize_position(p) for p in (positions or [])]
        # [2026-08-28] 附周期归属（运行会话 tier 币池）
        _tier_map = _tier_for_symbols(db, account_id)
        for _r in rows:
            _r["tier"] = _tier_map.get(str(_r.get("symbol") or "").upper()) or None
        # 叠加数据中心最新价
        try:
            from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
            for r in rows:
                live = asterdex_ticker_poller.get_price(r["symbol"])
                if live and live > 0:
                    r["last_price"] = round(float(live), 8)
        except Exception:
            pass
        # [2026-08-29] REST 路径同样附 TP/SL 条件单
        await _attach_tpsl_to_rows(db, account_id, rows)
        _payload = {"positions": rows, "exchange": exchange}
        _live_store(f"pos:{account_id}", _payload)
        return _payload


@router.get("/positions/{account_id}")
async def get_live_positions(account_id: int, db: Session = Depends(get_db)):
    # [2026-09-01 F33] stale-while-revalidate：过期即返旧值 + 后台单飞刷新
    _cached, _fresh = _live_cached_entry(f"pos:{account_id}")
    if _cached is not None:
        if _fresh:
            return _cached
        _spawn_live_refresh(f"pos:{account_id}", _compute_live_positions, account_id)
        return _cached
    return await _compute_live_positions(account_id)


@router.get("/orders/{account_id}")
async def get_live_orders(account_id: int, db: Session = Depends(get_db)):
    _cached = _live_cached(f"ord:{account_id}")
    if _cached is not None:
        return _cached
    account = _get_account(db, account_id)
    client, exchange = _maybe_client(account)
    if client is None:
        return {"orders": [], "exchange": exchange, "keys_configured": False, "message": "未配置 API Key"}
    orders: List[Dict[str, Any]] = []
    try:
        raw_ex = getattr(client, "_exchange", None)
        if raw_ex is not None and hasattr(raw_ex, "fetch_open_orders"):
            # [2026-08-28 P1 官方文档] openOrders 必须带 symbol：
            # 无符号查询 weight=40 且触发 10 倍严格限速（此前返回空/报错）。
            # 按当前持仓币逐一查询（weight=1/次），无持仓返回空。
            raw: List[Any] = []
            try:
                _pos_now = await client.get_positions()
            except Exception:
                _pos_now = []
            _syms: set = {str(getattr(_p, "symbol", "") or "") for _p in (_pos_now or [])}
            _recent = _RECENT_ORDER_SYMBOLS.get(account_id) or {}
            _cut = __import__("time").time() - 1800
            for _rs, _rts in list(_recent.items()):
                if _rts >= _cut and _rs:
                    _syms.add(f"{_rs}/USDT:USDT")
            for _ps in sorted(_syms):
                if not _ps:
                    continue
                try:
                    _os = await raw_ex.fetch_open_orders(_ps)
                    if _os:
                        raw.extend(_os)
                except Exception:
                    continue
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


@router.post("/margin-type/{account_id}")
async def set_live_margin_type(account_id: int, payload: dict, db: Session = Depends(get_db)):
    """[P2] 全仓/逐仓切换（币安 POST /fapi/v1/marginType，约 5 秒一次限速 → 防抖）。"""
    import time as _t

    account = _get_account(db, account_id)
    client, exchange = _maybe_client(account)
    if client is None:
        raise HTTPException(status_code=400, detail="未配置 API Key，无法切换保证金模式")
    symbol = str(payload.get("symbol") or "").upper().strip()
    margin_type = str(payload.get("margin_type") or "").lower()
    if not symbol or margin_type not in ("cross", "crossed", "isolated"):
        raise HTTPException(status_code=400, detail="symbol/margin_type 参数错误（margin_type: cross 或 isolated）")
    _key = f"{account_id}:{symbol}"
    _last = _MARGIN_TYPE_LAST_CALL.get(_key, 0)
    _now = _t.time()
    if _now - _last < 5.0:
        raise HTTPException(
            status_code=429,
            detail=f"保证金模式切换限速：每 5 秒一次，请稍后再试（{round(5.0 - (_now - _last), 1)}s）",
        )
    _MARGIN_TYPE_LAST_CALL[_key] = _now
    _ccxt_sym = _ccxt_symbol(exchange, symbol)
    _ok = await client.set_margin_type(_ccxt_sym, margin_type)
    if not _ok:
        raise HTTPException(status_code=502, detail="保证金模式切换失败（请确认无持仓/挂单，或重试）")
    _res = "cross" if margin_type in ("cross", "crossed") else "isolated"
    return {"success": True, "symbol": symbol, "margin_type": _res}


@router.post("/order")
async def place_live_order(payload: dict, db: Session = Depends(get_db)):
    account_id = int(payload.get("account_id") or 0)
    account = _get_account(db, account_id)
    if not _as_bool(getattr(account, "is_active", False)):
        raise HTTPException(status_code=403, detail="实盘账户已停用，禁止下单")
    # [2026-08-28 修复] 凭证判定走凭证表（账户级/全局），与环境变量无关
    client, exchange = _maybe_client(account)
    if client is None:
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
    if order_type not in ("market", "limit", "post_only"):
        raise HTTPException(status_code=400, detail="order_type 仅支持 market/limit/post_only")
    price = None
    if order_type in ("limit", "post_only"):
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

    # client 已在凭证判定处取得（_maybe_client）
    # [2026-08-28 P2 官方设计] 下单前 filters 校验（数量精度/最小下单量/最小名义）
    try:
        _mk = client._exchange.market(_ccxt_symbol(exchange, symbol))
        _l = _mk.get("limits") or {}
        _amt_min = float((_l.get("amount") or {}).get("min") or 0)
        _amt_max = float((_l.get("amount") or {}).get("max") or 0)
        _cost_min = float((_l.get("cost") or {}).get("min") or 0)
        _prec = int((_mk.get("precision") or {}).get("amount") or 0)
        _prec_price = int((_mk.get("precision") or {}).get("price") or 0)
        if _amt_min and quantity < _amt_min:
            raise HTTPException(status_code=400, detail=f"数量小于最小下单量 {_amt_min}")
        if _amt_max and quantity > _amt_max:
            raise HTTPException(status_code=400, detail=f"数量超过最大下单量 {_amt_max}")
        if order_type == "limit" and price and _cost_min and price * quantity < _cost_min:
            raise HTTPException(status_code=400, detail=f"名义价值低于最小要求 {_cost_min} USDT")
        if _prec_price and price and len(str(price).split(".")[-1]) > _prec_price:
            raise HTTPException(status_code=400, detail=f"价格精度需 ≤{_prec_price} 位小数")
    except HTTPException:
        raise
    except Exception:
        pass  # market 不存在等情况交由交易所报错
    # 订单速率限流（300/10s，官方 order rate limit）
    if not _order_rate_ok():
        raise HTTPException(status_code=429, detail="下单过于频繁（300 单/10 秒上限），请稍后再试")
    _ORDER_RATE_WINDOW.append(__import__("time").time())
    _RECENT_ORDER_SYMBOLS.setdefault(account_id, {})[symbol] = __import__("time").time()
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
        post_only=(order_type == "post_only"),
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


@router.post("/cancel")
async def cancel_live_order(payload: dict, db: Session = Depends(get_db)):
    """取消挂单（post_only/limit 未成交单的"平仓"方式）。"""
    account_id = int(payload.get("account_id") or 0)
    account = _get_account(db, account_id)
    client, exchange = _maybe_client(account)
    if client is None:
        raise HTTPException(status_code=400, detail=f"{exchange} 未配置 API Key，无法取消挂单")
    symbol = str(payload.get("symbol") or "").upper().strip()
    order_id = str(payload.get("order_id") or "").strip()
    if not symbol or not order_id:
        raise HTTPException(status_code=400, detail="symbol/order_id 参数缺失")
    _ccxt_sym = _ccxt_symbol(exchange, symbol)
    ok = await client.cancel_order(order_id, _ccxt_sym)
    if not ok:
        raise HTTPException(status_code=502, detail="取消失败（挂单可能已成交或不存在）")
    return {"success": True, "symbol": symbol, "order_id": order_id}


@router.post("/close")
async def close_live_position(payload: dict, db: Session = Depends(get_db)):
    account_id = int(payload.get("account_id") or 0)
    account = _get_account(db, account_id)
    if not _as_bool(getattr(account, "is_active", False)):
        raise HTTPException(status_code=403, detail="实盘账户已停用，禁止交易")
    client, exchange = _maybe_client(account)
    if client is None:
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
        reduce_only=False,  # hedge: positionSide+反向 side 即减仓（reduceOnly 会触发 -1106）
        position_side=("LONG" if side == "long" else "SHORT"),
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
