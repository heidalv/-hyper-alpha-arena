# -*- coding: utf-8 -*-
"""AutoCoin 板块联动信号（M2）。

默认关闭：AUTO_COIN_SECTOR_SIGNAL_ENABLED=false。
提供：
  - sector_rs_score(symbol): 相对板块强度 0~1（无数据返回 None）
  - detect_leader_peers(...): 龙头触发 → 同板块观察池 peers
  - enforce_max_per_sector(symbols): 池内同板块上限过滤
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 简单进程内缓存：sector_ret / watchlist
_SECTOR_RET_CACHE: Dict[str, Tuple[float, float]] = {}  # sector -> (ts, ret_4h)
_WATCHLIST: Dict[str, float] = {}  # symbol -> expire_ts


def _settings():
    from backend.config import settings as s
    return s


def sector_rs_score(symbol: str, ret_4h: Optional[float] = None) -> Optional[float]:
    """个币相对板块 4h 收益的 RS 分；无板块收益缓存时返回 None。"""
    try:
        s = _settings()
        if not getattr(s, "AUTO_COIN_SECTOR_SIGNAL_ENABLED", False):
            return None
    except Exception:
        return None

    from backend.services.auto_coin_sectors import get_sector
    sector = get_sector(symbol)
    if sector == "other":
        return 0.5

    cached = _SECTOR_RET_CACHE.get(sector)
    if not cached:
        return None
    _, sector_ret = cached
    if ret_4h is None:
        ret_4h = _estimate_ret_4h(symbol)
    if ret_4h is None:
        return None
    rs = float(ret_4h) - float(sector_ret)
    # ±10% 映射满幅
    return max(0.0, min(1.0, 0.5 + rs / 0.10))


def update_sector_return(sector: str, ret_4h: float) -> None:
    """供扫描循环写入板块收益（成交额加权由调用方算好）。"""
    _SECTOR_RET_CACHE[sector] = (time.time(), float(ret_4h))


def _estimate_ret_4h(symbol: str) -> Optional[float]:
    try:
        from backend.services.kline_data_service import kline_service
        raw = kline_service.get_klines_from_db(symbol, "4h", count=6)
        if not raw or len(raw) < 2:
            return None
        first = raw[-2]
        last = raw[-1]
        o = float(first.get("open") or first.get("close") or 0)
        c = float(last.get("close") or 0)
        if o <= 0 or c <= 0:
            return None
        return (c - o) / o
    except Exception:
        return None


def detect_leader_peers(
    members: List[Dict[str, Any]],
) -> List[str]:
    """members: [{symbol, ret_1h, volume_z_1h, volume_24h}, ...]
    若龙头满足阈值，返回同板块 peers（不含龙头）。
    """
    try:
        s = _settings()
        if not getattr(s, "AUTO_COIN_SECTOR_SIGNAL_ENABLED", False):
            return []
        ret_thr = float(getattr(s, "AUTO_COIN_SECTOR_LEADER_RET_1H", 0.04))
        vol_z_thr = float(getattr(s, "AUTO_COIN_SECTOR_LEADER_VOL_Z", 2.0))
        top_k = int(getattr(s, "AUTO_COIN_SECTOR_PEER_TOP_K", 4))
        ttl_min = int(getattr(s, "AUTO_COIN_WATCH_TTL_MIN", 45))
    except Exception:
        return []

    if not members:
        return []

    ranked = sorted(
        members,
        key=lambda m: float(m.get("volume_z_1h") or 0) * float(m.get("ret_1h") or 0),
        reverse=True,
    )
    leader = ranked[0]
    if float(leader.get("ret_1h") or 0) < ret_thr:
        return []
    if float(leader.get("volume_z_1h") or 0) < vol_z_thr:
        return []

    peers = [
        str(m["symbol"]).upper()
        for m in ranked[1:]
        if m.get("symbol")
    ][:top_k]
    expire = time.time() + ttl_min * 60
    for p in peers:
        _WATCHLIST[p] = expire
    if peers:
        logger.info(
            f"[SectorSignal] leader={leader.get('symbol')} peers={peers} "
            f"ret_1h={leader.get('ret_1h')} vol_z={leader.get('volume_z_1h')}"
        )
    return peers


def get_watchlist() -> List[str]:
    now = time.time()
    dead = [k for k, exp in _WATCHLIST.items() if exp <= now]
    for k in dead:
        _WATCHLIST.pop(k, None)
    return list(_WATCHLIST.keys())


def enqueue_watch(symbols: List[str], ttl_min: Optional[int] = None) -> None:
    try:
        s = _settings()
        ttl = int(ttl_min or getattr(s, "AUTO_COIN_WATCH_TTL_MIN", 45))
    except Exception:
        ttl = 45
    exp = time.time() + ttl * 60
    for sym in symbols:
        _WATCHLIST[str(sym).upper()] = exp


def enforce_max_per_sector(symbols: List[str], max_per: Optional[int] = None) -> List[str]:
    """按出现顺序保留，同板块超出上限的丢弃。"""
    from backend.services.auto_coin_sectors import get_sector
    try:
        s = _settings()
        limit = int(max_per or getattr(s, "AUTO_COIN_MAX_PER_SECTOR", 2))
    except Exception:
        limit = 2
    counts: Dict[str, int] = {}
    out: List[str] = []
    for sym in symbols:
        sec = get_sector(sym)
        if counts.get(sec, 0) >= limit:
            continue
        counts[sec] = counts.get(sec, 0) + 1
        out.append(sym)
    return out
