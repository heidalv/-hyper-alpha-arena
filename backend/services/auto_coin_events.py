# -*- coding: utf-8 -*-
"""AutoCoin 轻量事件总线（M3 Fast Lane）。

进程内队列 + 去重；不依赖外部消息中间件。
默认仅在 MULTI_LANE_ENABLED 时被调度器轮询。
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AutoCoinEvent:
    symbol: str
    event_type: str  # volume|oi|whale|news|sector|social
    ts: float = field(default_factory=time.time)
    meta: Dict = field(default_factory=dict)


class AutoCoinEventBus:
    def __init__(self, maxsize: int = 200):
        self._q: Deque[AutoCoinEvent] = deque(maxlen=maxsize)
        self._dedup: Dict[str, float] = {}  # key -> last_ts
        self._forced_ai_ts: Deque[float] = deque(maxlen=64)

    def _dedup_sec(self) -> float:
        try:
            from backend.config.settings import AUTO_COIN_EVENT_DEDUP_SEC
            return float(AUTO_COIN_EVENT_DEDUP_SEC)
        except Exception:
            return 300.0

    def push(self, symbol: str, event_type: str, **meta) -> bool:
        sym = (symbol or "").upper()
        if not sym:
            return False
        key = f"{sym}:{event_type}"
        now = time.time()
        last = self._dedup.get(key, 0.0)
        if now - last < self._dedup_sec():
            return False
        self._dedup[key] = now
        self._q.append(AutoCoinEvent(symbol=sym, event_type=event_type, ts=now, meta=meta))
        logger.info(f"[AutoCoinEvent] enqueue {event_type} {sym} meta={meta}")
        return True

    def drain(self, limit: int = 20) -> List[AutoCoinEvent]:
        out: List[AutoCoinEvent] = []
        while self._q and len(out) < limit:
            out.append(self._q.popleft())
        return out

    def pending_symbols(self, limit: int = 20) -> List[str]:
        evs = self.drain(limit=limit)
        # drain 后若还要保留给调用方，这里直接返回 symbols；
        # 调用方负责处理。若需要 peek 可另加；M3 简化为 drain。
        seen = []
        for e in evs:
            if e.symbol not in seen:
                seen.append(e.symbol)
        return seen

    def can_force_ai(self) -> bool:
        try:
            from backend.config.settings import AUTO_COIN_FAST_AI_MAX_PER_HOUR
            max_n = int(AUTO_COIN_FAST_AI_MAX_PER_HOUR)
        except Exception:
            max_n = 6
        now = time.time()
        # 清掉 1h 外
        while self._forced_ai_ts and now - self._forced_ai_ts[0] > 3600:
            self._forced_ai_ts.popleft()
        return len(self._forced_ai_ts) < max_n

    def mark_force_ai(self) -> None:
        self._forced_ai_ts.append(time.time())

    def size(self) -> int:
        return len(self._q)


auto_coin_event_bus = AutoCoinEventBus()
