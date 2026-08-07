"""ScalpAdvisoryCache — 进程内短线参谋缓存（OrchBG + StructureScanner 写入）。"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ScalpAdvisory:
    symbol: str
    updated_at: float = 0.0
    orch_long_bias: str = "neutral"
    orch_short_bias: str = "neutral"
    orch_final_action: str = "wait"
    regime: str = "unknown"
    stop_clusters: List[str] = field(default_factory=list)
    swing_low_5m: float = 0.0
    swing_high_5m: float = 0.0
    range_position_5m: float = 0.5
    advisory_verdict: str = "neutral"  # allow_long / allow_short / avoid / neutral
    penalty: int = 0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScalpAdvisory":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


class ScalpAdvisoryCache:
    """线程安全进程内缓存；热路径只读。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: Dict[str, ScalpAdvisory] = {}

    def get(self, symbol: str) -> Optional[ScalpAdvisory]:
        sym = (symbol or "").upper()
        with self._lock:
            adv = self._data.get(sym)
            if adv is None:
                return None
            return ScalpAdvisory(**adv.to_dict())

    def upsert(self, advisory: ScalpAdvisory) -> None:
        sym = (advisory.symbol or "").upper()
        if not sym:
            return
        advisory.symbol = sym
        advisory.updated_at = advisory.updated_at or time.time()
        with self._lock:
            self._data[sym] = advisory
        logger.debug(
            "[ScalpAdvisory] %s verdict=%s penalty=%d",
            sym, advisory.advisory_verdict, advisory.penalty,
        )

    def merge_orchestrator(self, symbol: str, orch_data: Dict[str, Any]) -> None:
        """OrchBG 每币评估后合并编排器字段。"""
        sym = (symbol or "").upper()
        if not sym or not isinstance(orch_data, dict):
            return
        with self._lock:
            existing = self._data.get(sym) or ScalpAdvisory(symbol=sym)
        existing.orch_long_bias = str(orch_data.get("long_bias") or "neutral")
        existing.orch_short_bias = str(orch_data.get("short_bias") or "neutral")
        existing.orch_final_action = str(
            orch_data.get("final_action") or orch_data.get("action") or "wait"
        )
        existing.updated_at = time.time()
        self.upsert(existing)

    def all_symbols(self) -> List[str]:
        with self._lock:
            return list(self._data.keys())

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: v.to_dict() for k, v in self._data.items()}


scalp_advisory_cache = ScalpAdvisoryCache()
