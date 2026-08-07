"""选币周期观测指标。"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last: Dict[str, Dict[str, Any]] = {}  # key = track:session_or_platform
_history: List[Dict[str, Any]] = []
_MAX_HIST = 50


@dataclass
class CycleMetrics:
    track: str  # platform | session
    session_id: Optional[str] = None
    scanned: int = 0
    ai_reviewed: int = 0
    injected: int = 0
    replaced: int = 0
    renewed_no_change: int = 0
    soft_reject: int = 0
    hard_reject: int = 0
    degraded: Optional[str] = None  # score_only | no_llm | None
    rank_source: str = "coin_rank"  # coin_rank | legacy
    lane: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def log(self) -> None:
        logger.info(
            "[CoinRank.metrics] track=%s session=%s scanned=%d ai=%d injected=%d "
            "replaced=%d renewed=%d soft=%d hard=%d degraded=%s source=%s lane=%s",
            self.track,
            self.session_id or "-",
            self.scanned,
            self.ai_reviewed,
            self.injected,
            self.replaced,
            self.renewed_no_change,
            self.soft_reject,
            self.hard_reject,
            self.degraded or "none",
            self.rank_source,
            self.lane or "-",
        )


def record_cycle_metrics(m: CycleMetrics) -> None:
    m.log()
    key = f"{m.track}:{m.session_id or 'platform'}"
    payload = asdict(m)
    with _lock:
        _last[key] = payload
        _history.append(payload)
        if len(_history) > _MAX_HIST:
            del _history[: len(_history) - _MAX_HIST]


def get_last_cycle_metrics(track: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
    with _lock:
        if session_id:
            return dict(_last.get(f"session:{session_id}") or {})
        if track == "platform":
            return dict(_last.get("platform:platform") or {})
        return dict(_last)


def get_metrics_history(limit: int = 20) -> List[Dict[str, Any]]:
    with _lock:
        return list(_history[-limit:])
