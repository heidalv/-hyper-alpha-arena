"""
事件溯源 - 事件存储（整改#9 Phase 1）—— 对标 NautilusTrader 事件溯源。

目标：引入不可变事件日志，使订单/仓位/账户状态可重放、可审计、可崩溃恢复，
从根本上消除"回测引擎与实盘引擎两套代码"的不一致风险。

Phase 1（本模块，零风险）：
  - EventStore 仅 **shadow 记录**（JSONL 追加），不接入实盘写路径、不改任何现有逻辑。
  - 默认关（EVENT_SOURCING_ENABLED=false）→ append 直接 no-op 返回。
  - PositionProjection / EventSourcedPositionRepository 提供"从事件流重建仓位"的能力，
    供离线重放、审计、崩溃恢复验证；不作为实盘读路径（Phase 2 再灰度）。

线程安全：JSONL 追加用全局锁串行化。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()

# 事件类型常量
EVT_ORDER_SUBMITTED = "OrderSubmitted"
EVT_ORDER_FILLED = "OrderFilled"
EVT_ORDER_CANCELLED = "OrderCancelled"
EVT_POSITION_OPENED = "PositionOpened"
EVT_POSITION_CHANGED = "PositionChanged"
EVT_POSITION_CLOSED = "PositionClosed"
EVT_ACCOUNT_UPDATED = "AccountUpdated"


def is_enabled() -> bool:
    return os.environ.get("EVENT_SOURCING_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


def _default_log_path() -> str:
    return os.environ.get(
        "EVENT_SOURCING_LOG_PATH",
        os.path.join(_repo_root(), "data", "event_log.jsonl"),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DomainEvent:
    """不可变领域事件。"""
    event_type: str
    aggregate_id: str                    # order_id / position_id / account_id
    payload: dict
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:16]}")
    timestamp: str = field(default_factory=lambda: _now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DomainEvent":
        return cls(
            event_type=d["event_type"],
            aggregate_id=d["aggregate_id"],
            payload=d.get("payload") or {},
            event_id=d.get("event_id") or f"evt_{uuid.uuid4().hex[:16]}",
            timestamp=d.get("timestamp") or _now().isoformat(),
        )


class EventStore:
    """事件日志持久化（JSONL 追加，不可变）。Phase 1 仅 shadow。"""

    def __init__(self, log_path: Optional[str] = None):
        self.log_path = log_path or _default_log_path()

    def append(self, event: DomainEvent, *, force: bool = False) -> bool:
        """追加一条事件。EVENT_SOURCING_ENABLED=false 且非 force → no-op。

        返回是否实际写入。
        """
        if not force and not is_enabled():
            return False
        try:
            with _write_lock:
                os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")
            return True
        except Exception as e:  # noqa: BLE001 — shadow 记录失败不得影响主流程
            logger.debug("[EventStore#9] 追加失败: %s", e)
            return False

    def append_many(self, events: Iterable[DomainEvent], *, force: bool = False) -> int:
        n = 0
        for e in events:
            if self.append(e, force=force):
                n += 1
        return n

    def _iter_all(self) -> Iterable[DomainEvent]:
        if not os.path.exists(self.log_path):
            return
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield DomainEvent.from_dict(json.loads(line))
                    except Exception:  # noqa: BLE001
                        continue
        except Exception as e:  # noqa: BLE001
            logger.debug("[EventStore#9] 读取失败: %s", e)

    def replay(self, aggregate_id: str) -> List[DomainEvent]:
        """重放某聚合（订单/仓位）的所有事件，按时间升序。"""
        events = [e for e in self._iter_all() if e.aggregate_id == aggregate_id]
        events.sort(key=lambda e: e.timestamp)
        return events

    def replay_all(self, since: Optional[str] = None) -> List[DomainEvent]:
        """重放全部事件（可选起始 ISO 时间），用于状态重建。"""
        events = list(self._iter_all())
        if since:
            events = [e for e in events if e.timestamp >= since]
        events.sort(key=lambda e: e.timestamp)
        return events

    def count(self) -> int:
        return sum(1 for _ in self._iter_all())


class PositionProjection:
    """仓位物化视图 —— 从事件流投影出当前仓位状态。"""

    def __init__(self):
        self._positions: Dict[str, dict] = {}

    def apply(self, event: DomainEvent) -> None:
        aid = event.aggregate_id
        p = event.payload or {}
        if event.event_type == EVT_POSITION_OPENED:
            self._positions[aid] = {
                "position_id": aid,
                "account_id": int(p.get("account_id") or 0),
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "size": float(p.get("size", 0.0)),
                "entry_price": float(p.get("entry_price", 0.0)),
                "status": "open",
                "trade_nature": p.get("trade_nature"),
                "strategy_id": p.get("strategy_id"),
                "leverage": float(p.get("leverage") or 1),
            }
        elif event.event_type == EVT_POSITION_CHANGED:
            pos = self._positions.get(aid)
            if pos:
                if "size" in p:
                    pos["size"] = float(p["size"])
                if "entry_price" in p:
                    pos["entry_price"] = float(p["entry_price"])
                if "account_id" in p:
                    pos["account_id"] = int(p.get("account_id") or 0)
        elif event.event_type == EVT_POSITION_CLOSED:
            pos = self._positions.get(aid)
            if pos:
                pos["status"] = "closed"
                pos["size"] = 0.0
                pos["exit_price"] = float(p.get("exit_price", pos.get("entry_price", 0.0)))
                pos["realized_pnl"] = float(p.get("realized_pnl", 0.0))

    @property
    def current_state(self) -> Dict[str, dict]:
        return self._positions

    def open_positions(self) -> Dict[str, dict]:
        return {k: v for k, v in self._positions.items() if v.get("status") == "open"}


class EventSourcedPositionRepository:
    """事件溯源仓位仓库 —— 从事件日志重建当前状态（崩溃恢复）。"""

    def __init__(self, store: Optional[EventStore] = None):
        self.store = store or EventStore()
        self._projection = PositionProjection()

    def rebuild_from_events(self, since: Optional[str] = None) -> Dict[str, dict]:
        """从事件日志重建当前仓位状态，返回物化视图。"""
        self._projection = PositionProjection()
        for event in self.store.replay_all(since=since):
            self._projection.apply(event)
        return self._projection.current_state

    def record_and_apply(self, event: DomainEvent, *, force: bool = False) -> bool:
        """追加事件并同步更新内存投影（Phase 2 读路径预备）。"""
        if not force and not is_enabled():
            return False
        written = self.store.append(event, force=force)
        self._projection.apply(event)
        return written

    @property
    def projection(self) -> PositionProjection:
        return self._projection


_store_singleton: Optional[EventStore] = None


def get_event_store() -> EventStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = EventStore()
    return _store_singleton
