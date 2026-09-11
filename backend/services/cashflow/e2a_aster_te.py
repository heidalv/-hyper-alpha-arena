# -*- coding: utf-8 -*-
"""E2a — Aster Trade & Earn 定时刷新与落盘（wrap live_income_ledger）。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "cashflow" / "aster_te"


def _env_true(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def te_enabled() -> bool:
    return _env_true("CASHFLOW_ASTER_TE_ENABLED", True)


def _ensure_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _list_aster_live_accounts() -> List[Any]:
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import Account
        from backend.core.tenant import set_system_identity

        set_system_identity()
        db = SessionLocal()
        try:
            rows = db.query(Account).filter(
                Account.trading_mode == "live",
            ).all()
            out = [
                a for a in rows
                if str(getattr(a, "selected_exchange", "") or "").lower() in ("asterdex", "aster")
            ]
            return out
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[cashflow.e2a] 列举 Aster 账户失败: %s", exc)
        return []


def _resolve_client(account) -> Any:
    try:
        from backend.api.live_trading_routes import _maybe_client

        client, _ = _maybe_client(account)
        return client
    except Exception as exc:
        logger.debug("[cashflow.e2a] client %s: %s", getattr(account, "id", "?"), exc)
        return None


async def refresh_account_ledger(account_id: int, *, days: int = 7) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.database.models import Account
    from backend.services.rebate_arb.live_income_ledger import build_live_income_ledger

    db = SessionLocal()
    try:
        acct = db.query(Account).filter(Account.id == int(account_id)).first()
    finally:
        db.close()
    if acct is None:
        return {"account_id": account_id, "ok": False, "error": "account_not_found"}
    client = _resolve_client(acct)
    if client is None or not hasattr(client, "get_margin_assets"):
        return {"account_id": account_id, "ok": False, "error": "client_unavailable"}
    try:
        ledger = await asyncio.wait_for(
            build_live_income_ledger(client, days=days, cache_key=f"asterdex:{account_id}:{days}"),
            timeout=40.0,
        )
    except Exception as exc:
        return {"account_id": account_id, "ok": False, "error": str(exc)[:200]}
    path = _ensure_dir() / f"account_{account_id}.json"
    payload = {"account_id": account_id, "ok": True, "ledger": ledger}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"account_id": account_id, "ok": True, "path": str(path), "trade_and_earn": (ledger or {}).get("trade_and_earn")}


async def refresh_all_aster_ledgers(*, days: int = 7) -> Dict[str, Any]:
    if not te_enabled():
        return {"ok": False, "skipped": True, "reason": "CASHFLOW_ASTER_TE_ENABLED=false"}
    accounts = _list_aster_live_accounts()
    results: List[Dict[str, Any]] = []
    for acct in accounts:
        aid = int(getattr(acct, "id", 0) or 0)
        if aid <= 0:
            continue
        try:
            results.append(await refresh_account_ledger(aid, days=days))
        except Exception as exc:
            results.append({"account_id": aid, "ok": False, "error": str(exc)[:160]})
    ok_n = sum(1 for r in results if r.get("ok"))
    summary_path = _ensure_dir() / "latest.json"
    summary = {"ok": ok_n > 0 or not accounts, "accounts": len(accounts), "refreshed": ok_n, "results": results}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def aster_te_status() -> Dict[str, Any]:
    latest = DATA_DIR / "latest.json"
    if not latest.exists():
        return {"ok": False, "reason": "尚未刷新（等待 cashflow_aster_te 任务）"}
    try:
        return {"ok": True, **json.loads(latest.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}


def scheduled_aster_te() -> Dict[str, Any]:
    logger.info("[cashflow.e2a] refresh aster T&E ledgers")
    return asyncio.run(refresh_all_aster_ledgers())
