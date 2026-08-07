"""全局用户交易对配置 — 运行时唯一来源 (system_configs.user_trading_pairs).

所有模块读取/写入手动配置交易对应通过本模块，禁止再使用写死的 DEFAULT/CORE 列表。
DB 首次空库时会一次性写入 INITIAL_SEED_TRADING_PAIRS，之后仅以 DB 为准。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

TRADING_PAIRS_CONFIG_KEY = "user_trading_pairs"

# 仅 DB 首次空库时写入；不是运行时 fallback
INITIAL_SEED_TRADING_PAIRS = ["BTC", "ETH", "SOL", "BNB", "VIRTUAL", "ASTER", "XPL"]

_cache: tuple[float, list[str]] = (0.0, [])
_CACHE_TTL_SEC = 60.0


def normalize_symbol_list(value: Any) -> list[str]:
    """大写、去重，保留顺序。"""
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
        sym = str(item or "").strip().upper()
        if not sym or sym in seen:
            continue
        symbols.append(sym)
        seen.add(sym)
    return symbols


def invalidate_trading_pairs_cache() -> None:
    global _cache
    _cache = (0.0, [])


def _read_from_db(db) -> Optional[list[str]]:
    from backend.database.models import SystemConfig

    cfg = db.query(SystemConfig).filter(
        SystemConfig.key == TRADING_PAIRS_CONFIG_KEY
    ).first()
    if not cfg or not cfg.value:
        return None
    try:
        parsed = json.loads(cfg.value)
    except Exception as exc:
        logger.warning("[TradingPairs] invalid JSON in %s: %s", TRADING_PAIRS_CONFIG_KEY, exc)
        return None
    if not isinstance(parsed, list):
        return None
    symbols = normalize_symbol_list(parsed)
    return symbols if symbols else None


def ensure_trading_pairs_seeded(db=None) -> list[str]:
    """DB 无配置时写入 INITIAL_SEED_TRADING_PAIRS 并返回。"""
    from backend.database.connection import SessionLocal
    from backend.database.models import SystemConfig

    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        existing = _read_from_db(db)
        if existing:
            return existing

        seeded = normalize_symbol_list(INITIAL_SEED_TRADING_PAIRS)
        cfg = SystemConfig(
            key=TRADING_PAIRS_CONFIG_KEY,
            value=json.dumps(seeded),
            description="用户配置的常用交易对列表（全局）",
        )
        db.add(cfg)
        db.commit()
        invalidate_trading_pairs_cache()
        logger.info("[TradingPairs] 首次初始化全局交易对: %s", seeded)
        return seeded
    finally:
        if own_session:
            db.close()


def get_user_trading_pairs(*, force_refresh: bool = False, db=None) -> list[str]:
    """读取全局 user_trading_pairs；空库时自动 seed。"""
    global _cache
    now = time.time()
    if (
        not force_refresh
        and _cache[1]
        and now - _cache[0] < _CACHE_TTL_SEC
    ):
        return list(_cache[1])

    from backend.database.connection import SessionLocal

    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        symbols = _read_from_db(db)
        if not symbols:
            if own_session:
                db.close()
                db = None
                own_session = False
            symbols = ensure_trading_pairs_seeded()
        else:
            symbols = normalize_symbol_list(symbols)
    finally:
        if own_session and db is not None:
            db.close()

    _cache = (now, symbols)
    return list(symbols)


def get_user_trading_pairs_set(*, force_refresh: bool = False) -> frozenset[str]:
    return frozenset(get_user_trading_pairs(force_refresh=force_refresh))


def save_user_trading_pairs(symbols: Iterable[str], db=None) -> list[str]:
    """保存全局交易对并刷新缓存。"""
    from backend.database.connection import SessionLocal
    from backend.database.models import SystemConfig

    cleaned = normalize_symbol_list(list(symbols))
    if not cleaned:
        raise ValueError("至少需要保留一个交易对")

    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        cfg = db.query(SystemConfig).filter(
            SystemConfig.key == TRADING_PAIRS_CONFIG_KEY
        ).first()
        payload = json.dumps(cleaned)
        if cfg:
            cfg.value = payload
        else:
            db.add(SystemConfig(
                key=TRADING_PAIRS_CONFIG_KEY,
                value=payload,
                description="用户配置的常用交易对列表（全局）",
            ))
        db.commit()
        invalidate_trading_pairs_cache()
        global _cache
        _cache = (time.time(), cleaned)
        return cleaned
    finally:
        if own_session:
            db.close()


def is_user_configured_symbol(symbol: str) -> bool:
    """是否为全局手动配置的交易对（AI 自动选币注入的不在此列）。"""
    base = _normalize_symbol_key(symbol).upper()
    return bool(base) and base in get_user_trading_pairs_set()


def _normalize_symbol_key(s: str) -> str:
    if not s:
        return ""
    u = s.strip().lower()
    for suf in ("usdt", "usdc", "usd"):
        if u.endswith(suf):
            u = u[: -len(suf)]
            break
    return u
