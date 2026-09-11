# -*- coding: utf-8 -*-
"""闲置资金理财 — Binance Simple Earn 活期（观察 / 半自动申购赎回）。"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "cashflow" / "idle_earn"


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def idle_enabled() -> bool:
    return _env_true("CASHFLOW_IDLE_EARN_ENABLED", True)


def live_subscribe() -> bool:
    """true = 真调 Binance Simple Earn API；false = 只记录拟申购。"""
    return _env_true("CASHFLOW_IDLE_EARN_LIVE", False)


async def _binance_client_for_account(account_id: int):
    from backend.database.connection import SessionLocal
    from backend.database.models import Account
    from backend.services.exchange.exchange_manager import exchange_manager

    db = SessionLocal()
    try:
        acct = db.query(Account).filter(Account.id == int(account_id)).first()
    finally:
        db.close()
    if acct is None:
        return None, None
    ex = str(getattr(acct, "selected_exchange", "") or "binance").lower()
    if ex not in ("binance", "binanceusdm"):
        return None, acct
    try:
        client = await exchange_manager.get_client(acct)
        return client, acct
    except Exception as exc:
        logger.debug("[idle_earn] client account=%s: %s", account_id, exc)
        return None, acct


async def list_flexible_products(client, asset: str = "USDT") -> List[Dict[str, Any]]:
    """Binance Simple Earn 活期产品列表（ccxt 系 adapter 可选实现）。"""
    fn = getattr(client, "list_simple_earn_flexible", None)
    if fn is None:
        return []
    try:
        rows = await fn(asset)
        return rows if isinstance(rows, list) else []
    except Exception as exc:
        logger.warning("[idle_earn] list products failed: %s", exc)
        return []


async def idle_earn_for_account(account_id: int) -> Dict[str, Any]:
    """扫描账户闲置 USDT，超过阈值则申购（或 dry 记录）。"""
    min_idle = _env_float("CASHFLOW_IDLE_MIN_USDT", 200.0)
    max_sub = _env_float("CASHFLOW_IDLE_MAX_SUBSCRIBE_USDT", 5000.0)
    reserve = _env_float("CASHFLOW_IDLE_RESERVE_USDT", 100.0)

    client, acct = await _binance_client_for_account(account_id)
    row: Dict[str, Any] = {"account_id": account_id, "ok": False}
    if client is None:
        row["reason"] = "no_binance_client"
        return row

    bal_fn = getattr(client, "get_balance", None) or getattr(client, "fetch_balance", None)
    free_usdt = 0.0
    try:
        if bal_fn:
            bal = await bal_fn() if callable(bal_fn) else {}
            if isinstance(bal, dict):
                usdt = bal.get("USDT") or bal.get("total", {}).get("USDT") or {}
                free_usdt = float(usdt.get("free") or usdt.get("total") or 0)
    except Exception as exc:
        row["reason"] = f"balance:{exc}"[:120]
        return row

    products = await list_flexible_products(client, "USDT")
    subscribable = max(0.0, min(free_usdt - reserve, max_sub))
    row.update({
        "free_usdt": round(free_usdt, 2),
        "subscribable": round(subscribable, 2),
        "min_idle": min_idle,
        "products": products[:5],
        "live": live_subscribe(),
    })
    if subscribable < min_idle:
        row.update({"ok": True, "action": "skip", "reason": "below_min_idle"})
        return row

    if not live_subscribe():
        row.update({"ok": True, "action": "dry_subscribe", "amount": round(subscribable, 2)})
        return row

    sub_fn = getattr(client, "subscribe_simple_earn_flexible", None)
    if sub_fn is None:
        row.update({"ok": False, "reason": "adapter_no_subscribe"})
        return row
    try:
        res = await sub_fn("USDT", subscribable)
        row.update({"ok": True, "action": "subscribed", "amount": round(subscribable, 2), "result": res})
    except Exception as exc:
        row.update({"ok": False, "action": "subscribe_failed", "error": str(exc)[:200]})
    return row


async def idle_earn_tick() -> Dict[str, Any]:
    if not idle_enabled():
        return {"ok": False, "skipped": True, "reason": "CASHFLOW_IDLE_EARN_ENABLED=false"}
    ids_raw = os.getenv("CASHFLOW_IDLE_EARN_ACCOUNT_IDS", "").strip()
    if ids_raw:
        ids = [int(x.strip()) for x in ids_raw.split(",") if x.strip().isdigit()]
    else:
        ids = []
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import Account
            from backend.core.tenant import set_system_identity

            set_system_identity()
            db = SessionLocal()
            try:
                rows = db.query(Account).filter(Account.trading_mode == "live").all()
                ids = [
                    int(a.id) for a in rows
                    if str(getattr(a, "selected_exchange", "") or "").lower().startswith("binance")
                ]
            finally:
                db.close()
        except Exception:
            ids = []

    results: List[Dict[str, Any]] = []
    for aid in ids:
        try:
            results.append(await idle_earn_for_account(aid))
        except Exception as exc:
            results.append({"account_id": aid, "ok": False, "error": str(exc)[:160]})

    payload = {"ok": True, "ts_ms": int(time.time() * 1000), "live": live_subscribe(), "results": results}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def idle_earn_status() -> Dict[str, Any]:
    p = DATA_DIR / "latest.json"
    if not p.exists():
        return {"ok": False, "reason": "尚未运行 idle_earn 任务"}
    try:
        return {"ok": True, **json.loads(p.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}


def scheduled_idle_earn() -> Dict[str, Any]:
    import asyncio

    logger.info("[cashflow.idle] tick live=%s", live_subscribe())
    return asyncio.run(idle_earn_tick())
