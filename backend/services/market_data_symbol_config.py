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


def lane_symbols(*, statuses: tuple[str, ...] = ("active",)) -> tuple[list[str], list[str]]:
    """[F302 2026-09-16] 车道正在交易的标的（`lane_registry.meta.symbols`）。

    为什么必须并入行情采集的标的来源：
      车道的标的宇宙由 `lane_registry.meta.symbols` 决定（`get_runner` 读它），
      而**行情采集**此前只看 `full_auto_sessions` / 系统配置里的用户自选
      ⇒ 两者**结构性解耦**。现场后果（2026-09-16）：`mm_asterdex` 的宇宙从
      LINK/ADA 换成 ZEC/ASTER/SOL/DOGE/TAO 后，`market_orderbook_snapshots`
      里**依然只有旧标的**（ZEC/ASTER/TAO 一行都没有），于是：
        · `replay._load_series` 读不到它们的历史 ⇒ 回放/验收全瞎；
        · `mm_anchor_vol_baseline.py` 对它们恒返回 0.0 ⇒ σ 闸基准无效；
        · `runner.backfill_mid_hist` 补不到冷启动窗口。

    返回 `(symbols, sources)`；sources 用于诊断（哪些车道贡献了标的）。
    任何异常 ⇒ 返回空（不阻断其它来源，采集宁可少也不要挂）。
    """
    try:
        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT lane_id, status, meta_json FROM lane_registry"
                " WHERE status = ANY(:st)"
            ), {"st": list(statuses)}).mappings().all()
    except Exception:
        return [], []

    out: list[str] = []
    sources: list[str] = []
    for r in rows:
        meta = r.get("meta_json")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        syms = normalize_symbols((meta or {}).get("symbols"))
        if syms:
            out.extend(syms)
            sources.append("lane_registry.%s" % r.get("lane_id"))
    return normalize_symbols(out), sources


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

    # [F302 2026-09-16] 并入**车道正在交易的标的**。
    # 动机：车道宇宙由 `lane_registry.meta.symbols` 决定，而行情采集此前只看
    # 会话/用户自选 ⇒ 换宇宙后行情跟不上（现场：ZEC/ASTER/TAO 无快照 ⇒ 回放瞎、
    # vol 基准恒 0、冷启动窗口补不到）。放在会话之后并入，语义是"用户交易的东西
    # ∪ 车道交易的东西"，两边都不断供。
    lane_syms, lane_srcs = lane_symbols()
    if lane_syms:
        configured_symbols.extend(lane_syms)
        configured_sources.extend(lane_srcs)

    symbols = normalize_symbols(configured_symbols)
    if symbols:
        return symbols, {
            "mode": "account_selected",
            "source": configured_sources,
            "session_id": session_id,
            "lane_symbols": lane_syms,
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
            "lane_symbols": lane_syms,
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
