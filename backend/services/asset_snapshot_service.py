"""
Record account asset snapshots on price updates.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from backend.database.models import Account, AccountAssetSnapshot, Position
from backend.services.asset_curve_calculator import invalidate_asset_curve_cache
from backend.services.market_data import get_last_price
from backend.api.ws import broadcast_arena_asset_update, manager

logger = logging.getLogger(__name__)

SNAPSHOT_RETENTION_HOURS = 24 * 30  # Keep 30 days of asset snapshots


def _resolved_account_model(account: Account) -> str:
    """[2026-08-15 LLM 统一重构] 展示用模型名：统一配置中心解析，失败返回空串。"""
    try:
        from backend.services.llm_config_service import resolve_llm_for_legacy_account

        cfg = resolve_llm_for_legacy_account(account, usage="legacy", tier="quick")
        return str(cfg.model or "") if cfg else ""
    except Exception:
        return ""


def _get_active_accounts(db: Session) -> List[Account]:
    # Arena AI + Paper 都需要权益快照；仅排除明确停用账户
    return (
        db.query(Account)
        .filter(Account.is_active == "true")
        .all()
    )


# Global variable to track last snapshot time
_last_snapshot_time = 0

def handle_price_update(event: Dict[str, Any]) -> None:
    """Persist account asset snapshots based on the latest price event."""
    global _last_snapshot_time

    # Limit to once per 60 seconds
    import time
    current_time = time.time()
    if current_time - _last_snapshot_time < 60:
        return

    _last_snapshot_time = current_time

    session = SessionLocal()
    try:
        accounts = _get_active_accounts(session)
        if not accounts:
            return

        trigger_symbol = event.get("symbol")
        trigger_market = event.get("market", "CRYPTO")
        event_time: datetime = event.get("event_time") or datetime.now(tz=timezone.utc)

        snapshots: List[AccountAssetSnapshot] = []
        symbol_totals = defaultdict(float)
        accounts_payload: List[Dict[str, Any]] = []
        total_available_cash = 0.0
        total_frozen_cash = 0.0
        total_positions_value = 0.0
        price_cache: Dict[str, float] = {}

        for account in accounts:
            try:
                trading_mode = (getattr(account, "trading_mode", None) or "").lower()
                account_type = (getattr(account, "account_type", None) or "").upper()

                # Paper 账户：权益以 paper_balances 为准（与仪表盘数字同源）
                if trading_mode == "paper" or account_type == "PAPER":
                    try:
                        from backend.services.paper_trading_engine import paper_engine

                        live = paper_engine.get_balance(session, account.id) or {}
                        available_cash = float(live.get("available_balance") or 0)
                        frozen_cash = float(live.get("frozen_margin") or 0)
                        upnl = float(live.get("unrealized_pnl") or 0)
                        total_assets = float(live.get("total_equity") or (available_cash + frozen_cash + upnl))
                        positions_value = frozen_cash + upnl
                    except Exception as paper_err:
                        logger.debug("paper snapshot fallback %s: %s", account.id, paper_err)
                        available_cash = float(account.current_cash or 0.0)
                        frozen_cash = float(account.frozen_cash or 0.0)
                        positions_value = 0.0
                        total_assets = available_cash + frozen_cash

                    total_available_cash += available_cash
                    total_frozen_cash += frozen_cash
                    total_positions_value += positions_value
                    accounts_payload.append(
                        {
                            "account_id": account.id,
                            "account_name": account.name,
                            # [2026-08-15 LLM 统一重构] 展示模型以统一配置中心解析为准
                            "model": _resolved_account_model(account),
                            "available_cash": round(available_cash, 2),
                            "frozen_cash": round(frozen_cash, 2),
                            "positions_value": round(positions_value, 2),
                            "total_assets": round(total_assets, 2),
                        }
                    )
                    snapshots.append(
                        AccountAssetSnapshot(
                            account_id=account.id,
                            total_assets=total_assets,
                            cash=available_cash,
                            positions_value=positions_value,
                            trigger_symbol=trigger_symbol,
                            trigger_market=trigger_market,
                            event_time=event_time,
                        )
                    )
                    continue

                positions = (
                    session.query(Position)
                    .filter(Position.account_id == account.id)
                    .all()
                )

                positions_value = 0.0
                for position in positions:
                    symbol_key = (position.symbol or "").upper()
                    market_key = position.market or "CRYPTO"
                    cache_key = f"{symbol_key}.{market_key}"

                    try:
                        if cache_key in price_cache:
                            price = price_cache[cache_key]
                        else:
                            price = float(get_last_price(symbol_key, market_key))
                            price_cache[cache_key] = price
                    except Exception as price_err:
                        logger.debug(
                            "Skipping valuation for %s.%s: %s",
                            symbol_key,
                            market_key,
                            price_err,
                        )
                        continue

                    current_value = price * float(position.quantity or 0.0)
                    positions_value += current_value
                    symbol_totals[symbol_key] += current_value

                available_cash = float(account.current_cash or 0.0)
                frozen_cash = float(account.frozen_cash or 0.0)
                total_assets = positions_value + available_cash

                total_available_cash += available_cash
                total_frozen_cash += frozen_cash
                total_positions_value += positions_value

                accounts_payload.append(
                    {
                        "account_id": account.id,
                        "account_name": account.name,
                        "model": _resolved_account_model(account),
                        "available_cash": round(available_cash, 2),
                        "frozen_cash": round(frozen_cash, 2),
                        "positions_value": round(positions_value, 2),
                        "total_assets": round(total_assets, 2),
                    }
                )

                snapshot = AccountAssetSnapshot(
                    account_id=account.id,
                    total_assets=total_assets,
                    cash=available_cash,
                    positions_value=positions_value,
                    trigger_symbol=trigger_symbol,
                    trigger_market=trigger_market,
                    event_time=event_time,
                )
                snapshots.append(snapshot)
            except Exception as account_err:
                logger.warning(
                    "Failed to compute snapshot for account %s: %s",
                    account.name,
                    account_err,
                )

        if snapshots:
            session.bulk_save_objects(snapshots)
            session.commit()
            invalidate_asset_curve_cache()

        if manager.has_connections():
            update_payload = {
                "generated_at": event_time.isoformat(),
                "totals": {
                    "available_cash": round(total_available_cash, 2),
                    "frozen_cash": round(total_frozen_cash, 2),
                    "positions_value": round(total_positions_value, 2),
                    "total_assets": round(
                        total_available_cash + total_frozen_cash + total_positions_value, 2
                    ),
                },
                "symbols": {symbol: round(value, 2) for symbol, value in symbol_totals.items()},
                "accounts": accounts_payload,
            }
            try:
                manager.schedule_task(broadcast_arena_asset_update(update_payload))
            except Exception as broadcast_err:
                logger.debug("Failed to schedule arena asset broadcast: %s", broadcast_err)

        _purge_old_snapshots(session, cutoff_hours=SNAPSHOT_RETENTION_HOURS)
    except Exception as err:
        session.rollback()
        logger.error("Failed to record asset snapshots: %s", err)
    finally:
        session.close()


def _purge_old_snapshots(session: Session, cutoff_hours: int) -> None:
    """Remove snapshots older than retention window to control storage."""
    cutoff_time = datetime.now(tz=timezone.utc) - timedelta(hours=cutoff_hours)
    deleted = (
        session.query(AccountAssetSnapshot)
        .filter(AccountAssetSnapshot.event_time < cutoff_time)
        .delete(synchronize_session=False)
    )
    if deleted:
        session.commit()
        logger.debug("Purged %d old asset snapshots", deleted)
