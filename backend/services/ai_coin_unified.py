# -*- coding: utf-8 -*-
"""AI 选币统一状态层（2026-08-28 归一）。

背景：历史上有两套并行 AI 选币——短线 auto_coin（平台看板跟投，状态存
full_auto_sessions.auto_coin_symbols + 选择器内存池）与中线 AI 候选
（平台看板 midlong approve，状态存 ai_mid_sticky/*.json）。两套各自存储、
消费点不一致，导致选出的币进不了 master 决策层（有选无析无交易）。

本模块 = AI 选币的 factor_active_set：
  - 单一状态存储：backend/services/data/ai_coin_unified/<session_id>.json
    （short/mid 两档同住一份文件）
  - 单一读取入口：get_ai_coin_symbols(session_id, db, tier) —— 所有循环
    （scalp/midlong/mlto/trading_cycle/master）只认这一个入口；
  - tier 只是字段：short 给短线车道，mid 给中线车道。

选择逻辑各自保留（短线=短线看板信号，中线=midlong approve + sticky 重采样），
但写入统一走 _write_state；旧存储（DB 列 / ai_mid_sticky 文件）只作迁移源与
向后兼容镜像。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "ai_coin_unified",
)
_lock = threading.Lock()


def _state_path(session_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))
    return os.path.join(_BASE_DIR, f"{safe}.json")


def _read_state(session_id: str) -> Dict[str, Any]:
    try:
        path = _state_path(session_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug("[AiCoinUnified] read state fail %s: %s", session_id, e)
    return {}


def _write_state(session_id: str, state: Dict[str, Any]) -> None:
    with _lock:
        try:
            os.makedirs(_BASE_DIR, exist_ok=True)
            tmp = _state_path(session_id) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _state_path(session_id))
        except Exception as e:
            logger.warning("[AiCoinUnified] write state fail %s: %s", session_id, e)


_HARD_DENY_SYMBOLS = frozenset({
    # 股票 / ETF / 传统资产永续（交易所目录里也有，必须硬拒绝）
    "TSLA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META", "NFLX",
    "AMD", "INTC", "COIN", "MSTR", "HOOD", "CRCL",
    "EWY", "SPY", "QQQ", "IWM", "DIA", "SOXL", "SOXS", "TQQQ", "SQQQ",
    "SKHYNIX", "SNDK", "NATGAS", "GOLD", "SILVER", "CL", "GC",
})


def _ai_quality_thresholds() -> Dict[str, float]:
    """流动性 / 新鲜度门槛（可用环境变量覆盖）。"""
    try:
        from backend.config.settings import AUTO_COIN_MIN_VOLUME_24H
        min_vol = float(AUTO_COIN_MIN_VOLUME_24H)
    except Exception:
        min_vol = 2_000_000.0
    min_vol = float(os.getenv("AI_COIN_MIN_VOLUME_24H", str(min_vol)) or min_vol)
    require_hl = str(os.getenv("AI_COIN_REQUIRE_HYPERLIQUID", "1")).strip().lower() not in (
        "0", "false", "no", "off",
    )
    max_age_h = float(os.getenv("AI_COIN_MAX_KLINE_AGE_H", "6") or 6)
    max_rank = int(float(os.getenv("AI_COIN_MAX_VOLUME_RANK", "120") or 120))
    return {
        "min_vol": max(0.0, min_vol),
        "require_hl": 1.0 if require_hl else 0.0,
        "max_age_sec": max(600.0, max_age_h * 3600.0),
        "max_rank": max(10, max_rank),
    }


def _liquidity_snapshot() -> Dict[str, Dict[str, Any]]:
    """{SYM: {volume_24h, exchanges, rank}}；失败返回空 dict（调用方 fail-open）。"""
    try:
        from backend.services.data_center import data_center
        tickers = data_center.get_all_market_tickers() or {}
    except Exception as exc:
        logger.debug("[AiCoinUnified] ticker snapshot skip: %s", exc)
        return {}
    ranked = sorted(
        tickers.items(),
        key=lambda kv: -float((kv[1] or {}).get("volume_24h") or 0.0),
    )
    out: Dict[str, Dict[str, Any]] = {}
    for i, (sym, data) in enumerate(ranked):
        u = str(sym or "").strip().upper()
        if not u or not isinstance(data, dict):
            continue
        out[u] = {
            "volume_24h": float(data.get("volume_24h") or 0.0),
            "exchanges": [str(x).lower() for x in (data.get("exchanges") or [])],
            "rank": i + 1,
        }
    return out


def _kline_ages_sec(symbols: List[str]) -> Dict[str, float]:
    """1m K 线最新年龄（秒）；查库失败则返回空（fail-open）。"""
    if not symbols:
        return {}
    try:
        from sqlalchemy import text
        from backend.database.connection import MarketSessionLocal
    except Exception:
        return {}
    ages: Dict[str, float] = {}
    now = time.time()
    try:
        with MarketSessionLocal() as db:
            for s in symbols:
                row = db.execute(
                    text(
                        "SELECT MAX(timestamp) FROM crypto_klines "
                        "WHERE symbol=:s AND period='1m'"
                    ),
                    {"s": s},
                ).scalar()
                if row is None:
                    ages[s] = 1e18
                else:
                    ages[s] = max(0.0, now - float(row))
    except Exception as exc:
        logger.debug("[AiCoinUnified] kline age skip: %s", exc)
        return {}
    return ages


def filter_tradeable_ai_symbols(symbols: List[str]) -> List[str]:
    """AI 选币可读/可写过滤：股票 ETF / 目录 / 流动性 / 新鲜度。

    目录或行情快照拉取失败时 fail-open（只应用硬拒绝表），避免 DC 抖动清空选币。
    """
    raw = [str(s).strip().upper() for s in (symbols or []) if s]
    if not raw:
        return []
    hard = [s for s in raw if s not in _HARD_DENY_SYMBOLS]
    denied_hard = [s for s in raw if s in _HARD_DENY_SYMBOLS]
    if denied_hard:
        logger.info("[AiCoinUnified] hard-deny non-crypto: %s", denied_hard)

    allowed: set = set()
    try:
        from backend.services.exchange_config import get_active_exchange
        from backend.services.market_scanner import MarketScanner
        ex = (get_active_exchange() or "binance").strip().lower()
        catalog = MarketScanner.get_all_tradable_symbols(ex) or []
        for c in catalog:
            u = str(c or "").strip().upper()
            if not u:
                continue
            allowed.add(u)
            if u.endswith("USDT") and len(u) > 4:
                allowed.add(u[:-4])
            if u.endswith("USD") and len(u) > 3:
                allowed.add(u[:-3])
    except Exception as exc:
        logger.debug("[AiCoinUnified] catalog filter skip: %s", exc)
        return hard

    if not allowed:
        return hard

    in_catalog: List[str] = []
    dropped_cat: List[str] = []
    for s in hard:
        if s in allowed or f"{s}USDT" in allowed:
            in_catalog.append(s)
        else:
            dropped_cat.append(s)
    if dropped_cat:
        logger.info("[AiCoinUnified] drop not-in-catalog: %s", dropped_cat)

    th = _ai_quality_thresholds()
    liq = _liquidity_snapshot()
    ages = _kline_ages_sec(in_catalog) if in_catalog else {}

    out: List[str] = []
    dropped_q: List[str] = []
    for s in in_catalog:
        reasons: List[str] = []
        snap = liq.get(s) if liq else None
        if snap is not None:
            vol = float(snap.get("volume_24h") or 0.0)
            rank = int(snap.get("rank") or 10**9)
            exs = set(snap.get("exchanges") or [])
            if vol < float(th["min_vol"]):
                reasons.append(f"vol<{th['min_vol']:.0f}")
            if rank > int(th["max_rank"]):
                reasons.append(f"rank>{int(th['max_rank'])}")
            if th["require_hl"] >= 1.0 and "hyperliquid" not in exs:
                reasons.append("no_hyperliquid")
        # 无行情快照时不做流动性否决（fail-open）
        age = ages.get(s) if ages else None
        if age is not None and age > float(th["max_age_sec"]):
            reasons.append(f"kline_age>{th['max_age_sec']:.0f}s")
        if reasons:
            dropped_q.append(f"{s}({','.join(reasons)})")
            continue
        out.append(s)
    if dropped_q:
        logger.info("[AiCoinUnified] drop low-quality: %s", dropped_q)
    return out


def set_tier_symbols(
    session_id: str,
    tier: str,
    symbols: List[str],
    *,
    reason: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """写某一档的当前选中币（选择器/中线重采样完成后调用）。"""
    tier = (tier or "").strip().lower()
    if tier not in ("short", "mid"):
        return
    # [2026-09-07] 写入前过滤不可交易垃圾（TSLA/BZ 等），避免污染下游 universe
    cleaned = filter_tradeable_ai_symbols([str(s).upper() for s in symbols if s])
    dropped = [str(s).upper() for s in symbols if s and str(s).upper() not in set(cleaned)]
    if dropped:
        logger.info(
            "[AiCoinUnified] drop untradeable on write session=%s tier=%s dropped=%s",
            session_id, tier, dropped,
        )
    state = _read_state(session_id)
    state.setdefault("version", 1)
    state.setdefault("session_id", str(session_id))
    state[tier] = {
        "symbols": cleaned,
        "updated_at": time.time(),
        "reason": reason or "",
    }
    if extra:
        state[tier].update(extra)
    state["updated_at"] = time.time()
    _write_state(session_id, state)


def get_tier_state(session_id: str, tier: str) -> Dict[str, Any]:
    tier = (tier or "").strip().lower()
    state = _read_state(session_id)
    return state.get(tier) or {} if tier in ("short", "mid") else {}


def _migrate_legacy(session_id: str, db=None) -> Dict[str, Any]:
    """一次性迁移：旧 ai_mid_sticky 文件 + DB auto_coin_symbols 列 → 统一状态。"""
    state = _read_state(session_id)
    changed = False
    # mid：旧 sticky 文件
    if not state.get("mid"):
        try:
            from backend.services.auto_coin_selector import _ai_mid_sticky_path
            legacy = _ai_mid_sticky_path(session_id)
            if os.path.exists(legacy):
                with open(legacy, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                if isinstance(data, dict) and data.get("symbols"):
                    state["mid"] = {
                        "symbols": [str(s).upper() for s in data.get("symbols") or [] if s],
                        "updated_at": float(data.get("updated_at") or time.time()),
                        "reason": str(data.get("reason") or "migrated_from_sticky"),
                    }
                    changed = True
                    logger.info(
                        "[AiCoinUnified] migrate mid sticky → unified session=%s n=%d",
                        session_id, len(state["mid"]["symbols"]),
                    )
        except Exception as e:
            logger.debug("[AiCoinUnified] migrate mid skip %s: %s", session_id, e)
    # short：DB auto_coin_symbols 列
    if not state.get("short") and db is not None:
        try:
            from sqlalchemy import text as _sa_text
            row = db.execute(
                _sa_text(
                    "SELECT auto_coin_symbols FROM full_auto_sessions "
                    "WHERE session_id = :sid"
                ),
                {"sid": session_id},
            ).first()
            syms = _parse_list(row[0]) if row and row[0] else []
            if syms:
                state["short"] = {
                    "symbols": [str(s).upper() for s in syms if s],
                    "updated_at": time.time(),
                    "reason": "migrated_from_db_column",
                }
                changed = True
                logger.info(
                    "[AiCoinUnified] migrate short db column → unified session=%s n=%d",
                    session_id, len(syms),
                )
        except Exception as e:
            logger.debug("[AiCoinUnified] migrate short skip %s: %s", session_id, e)
    if changed:
        state.setdefault("version", 1)
        state.setdefault("session_id", str(session_id))
        state["updated_at"] = time.time()
        _write_state(session_id, state)
    return state


def _parse_list(raw) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(s) for s in raw if s]
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return _parse_list(data)
        except Exception:
            return [s.strip() for s in raw.split(",") if s.strip()]
    return []


def get_ai_coin_symbols(
    session_id: str,
    db=None,
    tier: Optional[str] = None,
) -> List[str]:
    """AI 选币统一读取入口（全部循环只认这里）。

    tier='short' → 短线 AI 选币；tier='mid' → 中线 AI 候选；
    tier=None → 两档合并去重。
    """
    _migrate_legacy(session_id, db=db)
    state = _read_state(session_id)
    out: List[str] = []
    seen = set()
    for _t in ("short", "mid") if tier is None else ((tier or "").lower(),):
        for s in (state.get(_t) or {}).get("symbols") or []:
            u = str(s).strip().upper()
            if u and u not in seen:
                seen.add(u)
                out.append(u)
    # [2026-09-07] 读路径也过滤，清历史脏状态（TSLA/BZ 等）
    return filter_tradeable_ai_symbols(out)
