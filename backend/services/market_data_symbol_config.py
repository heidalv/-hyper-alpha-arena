"""Resolve market-data symbols from the user's active trading configuration."""

from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import text

from backend.database.connection import SessionLocal


AUTO_SYMBOL_MODES = {"", "auto", "configured", "account_selected", "session", "user_configured"}


def normalize_symbols(value: Any) -> list[str]:
    """Return uppercase, de-duplicated symbols while preserving user order."""
    if value is None:
        raw_items: list[Any] = []
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
                raw_items = decoded if isinstance(decoded, list) else [stripped]
            except Exception:
                raw_items = stripped.split(",")
        else:
            raw_items = stripped.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    symbols: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        symbol = str(item or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        symbols.append(symbol)
        seen.add(symbol)
    return symbols


def resolve_configured_symbols(env_name: str, *, fallback_env_name: str | None = None) -> tuple[list[str], dict[str, Any]]:
    """Resolve symbols for market-data services.

    The default source is the user's configured trading universe: watchlist,
    saved trading pairs, and the active full-auto session. Environment
    variables are only a fixed override when set to an explicit comma-separated
    list.
    """
    requested = os.getenv(env_name, "account_selected").strip()
    requested_key = requested.lower()

    if requested_key not in AUTO_SYMBOL_MODES:
        symbols = normalize_symbols(requested)
        return symbols, {"mode": "fixed_env", "env": env_name, "symbols": symbols}

    try:
        with SessionLocal() as db:
            session_rows = db.execute(
                text("""
                SELECT session_id, symbols, auto_coin_symbols, started_at
                FROM full_auto_sessions
                WHERE status = 'running'
                ORDER BY started_at DESC
                """)
            ).mappings().all()
            config_rows = db.execute(
                text("""
                SELECT key, value
                FROM system_configs
                WHERE key IN ('hyperliquid_selected_symbols', 'user_trading_pairs')
                """)
            ).mappings().all()
    except Exception as exc:
        session_rows = []
        config_rows = []
        db_error = f"{type(exc).__name__}: {exc}"
    else:
        db_error = ""

    configured_symbols: list[str] = []
    configured_sources: list[str] = []
    for key in ("hyperliquid_selected_symbols", "user_trading_pairs"):
        row = next((dict(item) for item in config_rows if item.get("key") == key), None)
        row_symbols = normalize_symbols(row.get("value") if row else [])
        if row_symbols:
            configured_sources.append(f"system_configs.{key}")
            configured_symbols.extend(row_symbols)

    session_id = None
    if session_rows:
        # 合并所有 running 会话的 symbols（不只取最新一个）。
        # 历史 bug：LIMIT 1 只取 started_at 最新的会话，导致其他并行会话
        # 交易的 symbol（如 JTO）不被采集，行情缺失却在交易。
        session_id = dict(session_rows[0]).get("session_id")
        for row in session_rows:
            row = dict(row)
            session_symbols = normalize_symbols(row.get("symbols"))
            auto_symbols = normalize_symbols(row.get("auto_coin_symbols"))
            if session_symbols:
                configured_sources.append("full_auto_sessions.symbols")
                configured_symbols.extend(session_symbols)
            if auto_symbols:
                configured_sources.append("full_auto_sessions.auto_coin_symbols")
                configured_symbols.extend(auto_symbols)

    symbols = normalize_symbols(configured_symbols)
    if symbols:
        return symbols, {
            "mode": "account_selected",
            "source": configured_sources,
            "session_id": session_id,
            "symbols": symbols,
        }

    fallback_value = os.getenv(fallback_env_name, "") if fallback_env_name else ""
    fallback_symbols = normalize_symbols(fallback_value)
    if fallback_symbols:
        return fallback_symbols, {
            "mode": "fallback_env",
            "env": fallback_env_name,
            "reason": "no_running_session_symbols",
            "error": db_error,
            "symbols": fallback_symbols,
        }

    return [], {
        "mode": "empty",
        "reason": "no_running_session_symbols",
        "error": db_error,
        "symbols": [],
    }


def symbols_csv(symbols: list[str]) -> str:
    return ",".join(symbols)
