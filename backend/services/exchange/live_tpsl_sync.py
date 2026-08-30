"""实盘止盈止损挂单同步。

纸盘账本改了 SL/TP 之后，把同一价格写到交易所条件单。
失败不影响纸盘；只在价格真变时打交易所（0.1% 内视为没变）。
回滚：LIVE_TPSL_SYNC=false。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_PRICE_EPS = 0.001  # 0.1%，与 Hyperliquid update_tpsl 一致
_MIN_INTERVAL_SEC = 8.0
_lock = threading.Lock()
_last_ok: Dict[Tuple[int, str, str], Tuple[float, float]] = {}
_failed: set[Tuple[int, str, str]] = set()
_last_try_ts: Dict[Tuple[int, str, str], float] = {}


def reset_sync_state_for_tests() -> None:
    with _lock:
        _last_ok.clear()
        _failed.clear()
        _last_try_ts.clear()


def sync_enabled() -> bool:
    try:
        from backend.config import settings as _settings
        return bool(getattr(_settings, "LIVE_TPSL_SYNC", True))
    except Exception:
        import os
        return str(os.getenv("LIVE_TPSL_SYNC", "true")).strip().lower() in (
            "1", "true", "yes", "on",
        )


def _default_ex() -> str:
    """[2026-08-31] 默认交易所=币安（.env DEFAULT_EXCHANGE=binance），
    历史硬编码 asterdex 退役。"""
    try:
        from backend.config import settings as _settings
        return str(
            getattr(_settings, "DEFAULT_EXCHANGE", None) or "binance"
        ).strip().lower() or "binance"
    except Exception:
        import os as _os_tp
        return str(_os_tp.getenv("DEFAULT_EXCHANGE", "binance") or "binance").strip().lower() or "binance"


def _norm_ex(exchange: Optional[str]) -> str:
    ex = (exchange or _default_ex()).strip().lower()
    return "asterdex" if ex == "aster" else ex


def _base_symbol(symbol: str) -> str:
    return str(symbol or "").split("/")[0].split("-")[0].upper()


def _key(account_id: int, symbol: str, side: str) -> Tuple[int, str, str]:
    return (int(account_id or 0), _base_symbol(symbol), str(side or "").lower())


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _same_price(a: float, b: float) -> bool:
    if a <= 0 and b <= 0:
        return True
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= _PRICE_EPS


def _run_async(factory: Callable):
    """在可能已有 event loop 的线程里跑一段异步（交易所适配器是 async）。"""
    def _go():
        return asyncio.run(factory())

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    if not in_loop:
        return _go()

    box: Dict[str, Any] = {}

    def _worker():
        try:
            box["v"] = _go()
        except Exception as exc:
            box["e"] = exc

    t = threading.Thread(target=_worker, name="live-tpsl-sync", daemon=True)
    t.start()
    t.join(30)
    if t.is_alive():
        raise TimeoutError("live tpsl sync timeout")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def _resolve_client(account) -> Tuple[Any, str]:
    exchange = _norm_ex(getattr(account, "selected_exchange", None) or _default_ex())
    from backend.services.exchange.exchange_manager import get_exchange_manager
    mgr = get_exchange_manager()
    client = mgr.get_client(exchange, getattr(account, "id", 0) or 0)
    if client is None:
        market_type = getattr(account, "binance_market_type", None) or "usdt_m"
        client = mgr.get_or_create_global_client(
            exchange,
            user_id=getattr(account, "user_id", None) or 1,
            account_id=getattr(account, "id", 0) or 0,
            market_type=market_type if exchange == "binance" else None,
        )
    if client is None and exchange == "hyperliquid":
        client = mgr.create_client("hyperliquid", getattr(account, "id", 0) or 0)
    return client, exchange


def _sync_hyperliquid(db, client, pos, tp: float, sl: float) -> Dict[str, Any]:
    inner = getattr(client, "_client", None) or client
    updater = getattr(inner, "update_tpsl", None)
    if updater is None:
        return {"ok": False, "error": "no update_tpsl"}
    side = str(getattr(pos, "side", "long") or "long").lower()
    result = updater(
        db,
        _base_symbol(getattr(pos, "symbol", "")),
        new_tp_price=tp if tp > 0 else None,
        new_sl_price=sl if sl > 0 else None,
        position_size=abs(_f(getattr(pos, "size", 0))),
        is_long=side in ("long", "buy"),
    )
    if isinstance(result, dict) and result.get("success") is False:
        return {"ok": False, "error": result.get("errors") or result}
    return {"ok": True, "via": "hyperliquid", "raw": result}


def _sync_ccxt(client, pos, tp: float, sl: float) -> Dict[str, Any]:
    replacer = getattr(client, "replace_tpsl_orders", None)
    if replacer is None:
        return {"ok": False, "error": "no replace_tpsl_orders"}
    side = str(getattr(pos, "side", "long") or "long").lower()
    return _run_async(lambda: replacer(
        getattr(pos, "symbol", ""),
        side=side,
        quantity=abs(_f(getattr(pos, "size", 0))),
        tp_price=tp if tp > 0 else None,
        sl_price=sl if sl > 0 else None,
    ))


def maybe_sync_live_tpsl(db, pos, *, force: bool = False) -> Dict[str, Any]:
    """账户是 live 且止盈止损变了，才改交易所挂单。"""
    empty = {"ok": False, "skipped": True}
    if not sync_enabled() or pos is None or db is None:
        return {**empty, "reason": "disabled_or_missing"}
    account_id = int(getattr(pos, "account_id", 0) or 0)
    if account_id <= 0:
        return {**empty, "reason": "no_account"}

    try:
        from backend.database.models import Account
        account = db.query(Account).filter(Account.id == account_id).first()
    except Exception as exc:
        logger.debug("[LiveTpSlSync] 读账户失败: %s", exc)
        return {**empty, "reason": "account_lookup"}
    if account is None:
        return {**empty, "reason": "no_account"}
    if str(getattr(account, "trading_mode", "") or "").strip().lower() != "live":
        return {**empty, "reason": "paper"}

    tp = _f(getattr(pos, "tp_price", 0))
    sl = _f(getattr(pos, "sl_price", 0))
    if tp <= 0 and sl <= 0:
        return {**empty, "reason": "no_prices"}

    key = _key(account_id, getattr(pos, "symbol", ""), getattr(pos, "side", ""))
    with _lock:
        last = _last_ok.get(key)
        if last and _same_price(last[0], tp) and _same_price(last[1], sl):
            _failed.discard(key)
            return {**empty, "reason": "unchanged"}
        if not force and key not in _failed:
            return {**empty, "reason": "not_dirty"}
        import time as _t
        now = _t.time()
        prev_try = _last_try_ts.get(key, 0.0)
        if now - prev_try < _MIN_INTERVAL_SEC:
            _failed.add(key)
            return {**empty, "reason": "rate_limit"}
        _last_try_ts[key] = now

    try:
        client, exchange = _resolve_client(account)
        if client is None:
            with _lock:
                _failed.add(key)
            return {**empty, "reason": "no_client", "exchange": exchange}
        if exchange == "hyperliquid" or hasattr(getattr(client, "_client", None), "update_tpsl"):
            result = _sync_hyperliquid(db, client, pos, tp, sl)
        else:
            result = _sync_ccxt(client, pos, tp, sl)
        ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
        if ok:
            with _lock:
                _last_ok[key] = (tp, sl)
                _failed.discard(key)
            logger.info(
                "[LiveTpSlSync] %s %s %s tp=%.6f sl=%.6f via=%s",
                getattr(pos, "symbol", "?"), getattr(pos, "side", "?"),
                exchange, tp, sl, (result or {}).get("via") or exchange,
            )
            return {"ok": True, "exchange": exchange, "raw": result}
        with _lock:
            _failed.add(key)
        logger.warning("[LiveTpSlSync] %s 同步未成功: %s", getattr(pos, "symbol", "?"), result)
        return {"ok": False, "exchange": exchange, "raw": result}
    except Exception as exc:
        with _lock:
            _failed.add(key)
        logger.warning(
            "[LiveTpSlSync] %s 同步失败（纸盘已改，交易所稍后重试）: %s",
            getattr(pos, "symbol", "?"), exc,
        )
        return {"ok": False, "error": str(exc)}
