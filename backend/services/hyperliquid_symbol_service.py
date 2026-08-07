"""
Hyperliquid symbol management utilities.

Handles:
- Fetching tradable symbol metadata from Hyperliquid meta API
- Persisting available symbols + user-selected watchlist in SystemConfig
- Exposing helpers for other services (prompt generation, execution, etc.)
- Keeping market data stream in sync with selected symbols
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from backend.database.models import SystemConfig, Account
from backend.services.market_data_symbol_config import normalize_symbols

logger = logging.getLogger(__name__)

AVAILABLE_SYMBOLS_KEY = "hyperliquid_available_symbols"
SELECTED_SYMBOLS_KEY = "hyperliquid_selected_symbols"
MAX_WATCHLIST_SYMBOLS = 10
SYMBOL_REFRESH_TASK_ID = "hyperliquid_symbol_refresh"

DEFAULT_SYMBOLS: List[Dict[str, str]] = [
    {"symbol": "BTC", "name": "Bitcoin"},
]

META_ENDPOINTS = {
    "testnet": "https://api.hyperliquid-testnet.xyz/info",
    "mainnet": "https://api.hyperliquid.xyz/info",
}


def _load_config_value(db: Session, key: str) -> Optional[str]:
    config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return config.value if config else None


def _save_config_value(db: Session, key: str, value: str) -> None:
    config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if not config:
        config = SystemConfig(key=key, value=value)
        db.add(config)
    else:
        config.value = value
    db.commit()


def _parse_symbol_json(value: Optional[str]) -> List[Dict[str, str]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            result = []
            for entry in parsed:
                if not isinstance(entry, dict):
                    continue
                symbol = str(entry.get("symbol") or "").upper()
                if not symbol:
                    continue
                result.append(
                    {
                        "symbol": symbol,
                        "name": entry.get("name") or symbol,
                        "type": entry.get("type") or entry.get("category"),
                    }
                )
            return result
    except json.JSONDecodeError:
        logger.warning("Failed to decode stored Hyperliquid symbols; falling back to defaults")
    return []


def _serialize_symbols(symbols: List[Dict[str, str]]) -> str:
    sanitized = []
    seen = set()
    for entry in symbols:
        symbol = str(entry.get("symbol") or "").upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        sanitized.append(
            {
                "symbol": symbol,
                "name": entry.get("name") or symbol,
                "type": entry.get("type") or entry.get("category"),
            }
        )
    return json.dumps(sanitized)


def _validate_symbol_tradability(symbol: str, environment: str = "testnet") -> bool:
    """
    Test if a symbol can actually fetch price data (i.e., is tradable).

    Uses silent validation method that doesn't log errors for invalid symbols.
    """
    try:
        from services.hyperliquid_market_data import get_hyperliquid_client_for_environment
        client = get_hyperliquid_client_for_environment(environment)
        return client.check_symbol_tradability(symbol)
    except Exception:
        return False


def fetch_remote_symbols(environment: str = "testnet") -> List[Dict[str, str]]:
    """Call Hyperliquid meta endpoint to retrieve tradable universe."""
    url = META_ENDPOINTS.get(environment, META_ENDPOINTS["testnet"])
    try:
        resp = requests.post(url, json={"type": "meta"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        universe = data.get("universe") or data.get("universeSpot") or []
    except Exception as err:
        logger.warning("Failed to fetch Hyperliquid meta info: %s", err)
        return []

    results: List[Dict[str, str]] = []
    seen = set()
    invalid_count = 0

    for entry in universe:
        if not isinstance(entry, dict):
            continue
        raw_symbol = entry.get("name") or entry.get("symbol")
        if not raw_symbol:
            continue
        symbol = str(raw_symbol).upper()
        if symbol in seen:
            continue
        seen.add(symbol)

        # Validate symbol is actually tradable
        if not _validate_symbol_tradability(symbol, environment):
            logger.debug(f"Skipping symbol {symbol} (not tradable on Hyperliquid)")
            invalid_count += 1
            continue

        results.append(
            {
                "symbol": symbol,
                "name": entry.get("displayName") or entry.get("name") or symbol,
                "type": entry.get("type") or entry.get("szType") or entry.get("assetType"),
            }
        )

    if invalid_count > 0:
        logger.info(f"Filtered out {invalid_count} delisted/non-tradable symbols during Hyperliquid symbol refresh")

    return results


def refresh_hyperliquid_symbols(environment: str = "testnet") -> List[Dict[str, str]]:
    """Refresh available symbol list from Hyperliquid.

    Merges remote symbols with any user-added custom entries so they
    are never silently dropped.
    """
    remote_symbols = fetch_remote_symbols(environment)
    if not remote_symbols:
        logger.warning("No symbols fetched from Hyperliquid meta; keeping existing list")

    with SessionLocal() as db:
        if remote_symbols:
            remote_set = {s["symbol"] for s in remote_symbols}
            existing = _parse_symbol_json(_load_config_value(db, AVAILABLE_SYMBOLS_KEY))
            custom_entries = [e for e in existing if e["symbol"] not in remote_set]

            merged = remote_symbols + custom_entries
            _save_config_value(db, AVAILABLE_SYMBOLS_KEY, _serialize_symbols(merged))
            logger.info("Hyperliquid symbol catalog refreshed (%d remote + %d custom)",
                        len(remote_symbols), len(custom_entries))
        else:
            stored = _parse_symbol_json(_load_config_value(db, AVAILABLE_SYMBOLS_KEY))
            if not stored:
                _save_config_value(db, AVAILABLE_SYMBOLS_KEY, _serialize_symbols(DEFAULT_SYMBOLS))
    return get_available_symbols()


def _ensure_watchlist_valid(db: Session, available: List[Dict[str, str]]) -> None:
    available_set = {item["symbol"] for item in available}
    raw_value = _load_config_value(db, SELECTED_SYMBOLS_KEY)
    if not raw_value:
        # Populate defaults
        default = [entry["symbol"] for entry in available[:MAX_WATCHLIST_SYMBOLS]] or [
            item["symbol"] for item in DEFAULT_SYMBOLS
        ]
        _save_config_value(db, SELECTED_SYMBOLS_KEY, json.dumps(default))
        return

    try:
        symbols = json.loads(raw_value)
        if not isinstance(symbols, list):
            raise ValueError("Selection is not a list")
    except Exception:
        logger.warning("Invalid Hyperliquid watchlist stored; resetting to defaults")
        default = [entry["symbol"] for entry in available[:MAX_WATCHLIST_SYMBOLS]] or [
            item["symbol"] for item in DEFAULT_SYMBOLS
        ]
        _save_config_value(db, SELECTED_SYMBOLS_KEY, json.dumps(default))
        return

    filtered = [str(sym).upper() for sym in symbols if str(sym).upper() in available_set]

    if filtered:
        _save_config_value(db, SELECTED_SYMBOLS_KEY, json.dumps(filtered[:MAX_WATCHLIST_SYMBOLS]))
        return

    if symbols:
        # Previously selected symbols are no longer available -> fall back to defaults
        default = [entry["symbol"] for entry in available[:MAX_WATCHLIST_SYMBOLS]] or [
            item["symbol"] for item in DEFAULT_SYMBOLS
        ]
        _save_config_value(db, SELECTED_SYMBOLS_KEY, json.dumps(default))
    else:
        # User intentionally cleared watchlist, keep empty
        _save_config_value(db, SELECTED_SYMBOLS_KEY, json.dumps([]))


def get_available_symbols() -> List[Dict[str, str]]:
    """Return cached available Hyperliquid symbols."""
    with SessionLocal() as db:
        stored = _parse_symbol_json(_load_config_value(db, AVAILABLE_SYMBOLS_KEY))
        if stored:
            return stored
        # Seed defaults if missing
        _save_config_value(db, AVAILABLE_SYMBOLS_KEY, _serialize_symbols(DEFAULT_SYMBOLS))
        _ensure_watchlist_valid(db, DEFAULT_SYMBOLS)
        return DEFAULT_SYMBOLS.copy()


def get_available_symbols_info() -> Dict[str, Optional[str]]:
    """Return available symbols plus last update timestamp."""
    with SessionLocal() as db:
        config = db.query(SystemConfig).filter(SystemConfig.key == AVAILABLE_SYMBOLS_KEY).first()
        symbols = _parse_symbol_json(config.value if config else None)
        updated_at = config.updated_at.isoformat() if config and config.updated_at else None
        if not symbols:
            symbols = DEFAULT_SYMBOLS.copy()
        return {"symbols": symbols, "updated_at": updated_at}


def get_available_symbol_map() -> Dict[str, Dict[str, str]]:
    """Return mapping of symbol -> metadata."""
    return {entry["symbol"]: entry for entry in get_available_symbols()}


def get_selected_symbols() -> List[str]:
    """Return user-selected Hyperliquid symbols.

    Merges the watchlist (hyperliquid_selected_symbols) with
    user-configured trading pairs (user_trading_pairs) so that
    any symbol added via Settings → Trading Config is automatically
    included in market data streaming, kline collection, and AI decisions.
    """
    with SessionLocal() as db:
        raw_value = _load_config_value(db, SELECTED_SYMBOLS_KEY)
        if raw_value is None:
            default = [entry["symbol"] for entry in get_available_symbols()[:MAX_WATCHLIST_SYMBOLS]]
            _save_config_value(db, SELECTED_SYMBOLS_KEY, json.dumps(default))
            selected = default
        else:
            try:
                selected = json.loads(raw_value)
            except json.JSONDecodeError:
                selected = []

        # Merge user_trading_pairs into watchlist
        try:
            tp_value = _load_config_value(db, "user_trading_pairs")
            if tp_value:
                user_pairs = json.loads(tp_value)
                if isinstance(user_pairs, list):
                    existing = set(s.upper() for s in selected)
                    for sym in user_pairs:
                        s = str(sym).upper()
                        if s and s not in existing:
                            selected.append(s)
                            existing.add(s)
        except Exception:
            pass

        # The active full-auto session is the source of truth for the user's
        # currently configured trading universe.
        try:
            row = db.execute(
                text("""
                SELECT symbols
                FROM full_auto_sessions
                WHERE status = 'running'
                ORDER BY started_at DESC
                LIMIT 1
                """)
            ).mappings().first()
            session_symbols = normalize_symbols(row["symbols"] if row else [])
            existing = set(str(s).upper() for s in selected)
            for sym in session_symbols:
                if sym not in existing:
                    selected.append(sym)
                    existing.add(sym)
        except Exception:
            pass

        return [str(s).upper() for s in selected]


def update_selected_symbols(symbols: List[str]) -> List[str]:
    """Persist new watchlist.

    Custom symbols not yet in the cached available list are automatically
    added so subsequent reads/filters won't silently drop them.
    """
    unique_symbols: List[str] = []
    seen = set()
    for symbol in symbols:
        symbol_upper = str(symbol).upper()
        if symbol_upper in seen:
            continue
        seen.add(symbol_upper)
        unique_symbols.append(symbol_upper)

    if len(unique_symbols) > MAX_WATCHLIST_SYMBOLS:
        raise ValueError(f"Cannot monitor more than {MAX_WATCHLIST_SYMBOLS} symbols")

    with SessionLocal() as db:
        available = _parse_symbol_json(_load_config_value(db, AVAILABLE_SYMBOLS_KEY))
        available_set = {entry["symbol"] for entry in available}

        new_entries = [
            {"symbol": s, "name": s}
            for s in unique_symbols
            if s not in available_set
        ]
        if new_entries:
            available.extend(new_entries)
            _save_config_value(db, AVAILABLE_SYMBOLS_KEY, _serialize_symbols(available))
            logger.info("Auto-added custom symbols to available list: %s",
                        ", ".join(e["symbol"] for e in new_entries))

        _save_config_value(db, SELECTED_SYMBOLS_KEY, json.dumps(unique_symbols))

    logger.info("Hyperliquid watchlist updated: %s", ", ".join(unique_symbols) or "none")
    refresh_market_symbols()
    return unique_symbols


def get_symbol_display(symbol: str) -> str:
    """Friendly display name for symbol."""
    symbol_upper = symbol.upper()
    metadata = get_available_symbol_map()
    entry = metadata.get(symbol_upper)
    if entry:
        return entry.get("name") or symbol_upper
    return symbol_upper


def schedule_symbol_refresh_task(interval_seconds: int = 7200) -> None:
    """Register periodic symbol refresh job."""
    from services.scheduler import task_scheduler

    def _task():
        try:
            refreshed = refresh_hyperliquid_symbols(environment="mainnet")
            logger.debug("Symbol refresh task ran; %d symbols available", len(refreshed))
        except Exception as err:
            logger.warning("Hyperliquid symbol refresh failed: %s", err)

    # Remove existing task if present to avoid duplicates
    task_scheduler.remove_task(SYMBOL_REFRESH_TASK_ID)
    task_scheduler.add_interval_task(
        task_func=_task,
        interval_seconds=interval_seconds,
        task_id=SYMBOL_REFRESH_TASK_ID,
    )


def _has_active_paper_accounts() -> bool:
    """Return True if any active AI account is still running in paper mode."""
    with SessionLocal() as db:
        paper_account = (
            db.query(Account.id)
            .filter(
                Account.is_active == "true",
                Account.auto_trading_enabled == "true",
                Account.account_type == "AI",
                Account.hyperliquid_environment.is_(None),
            )
            .first()
        )
        return paper_account is not None


def build_market_symbols() -> List[str]:
    """计算共享市场数据 symbol 列表（Paper + 用户自选）"""
    paper_symbols: List[str] = []
    if _has_active_paper_accounts():
        try:
            from services.trading_commands import AI_TRADING_SYMBOLS
        except Exception:
            paper_symbols = [entry["symbol"] for entry in DEFAULT_SYMBOLS]
        else:
            paper_symbols = list(AI_TRADING_SYMBOLS)

    return sorted(set(paper_symbols + get_selected_symbols()))


def refresh_market_symbols() -> List[str]:
    """更新 Hub / Legacy 轮询的 symbol 列表"""
    combined = build_market_symbols()

    try:
        from backend.services.market_price_service import sync_market_symbols
        sync_market_symbols(combined, interval_seconds=1.5)
    except Exception as err:
        logger.warning("Unable to refresh market symbols: %s", err)

    try:
        from services.market_flow_collector import market_flow_collector
        hyperliquid_symbols = get_selected_symbols()
        market_flow_collector.refresh_subscriptions(hyperliquid_symbols)
    except Exception as err:
        logger.warning("Unable to update market flow collector: %s", err)

    return combined
