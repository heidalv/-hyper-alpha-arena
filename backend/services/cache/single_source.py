"""
共享 Cache —— 单一真相源（P2.5，方案 §1.5，红线 R6/R8）。

目标：
    - 所有状态（快照/因子/仓位/订单）进单一 Cache
    - 状态只通过事件更新（事件溯源）
    - 事件全量落审计日志（append-only）
    - replay(ts_start, ts_end) 复现任意窗口的决策（R8）

这是 nautilus_trader 式 Cache 单一真相源 + 事件溯源。
解决诊断'各 agent 各自缓存 3s TTL'的散点问题。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from backend.services.contracts.types import (
    ApprovedTarget,
    DataQualityFlag,
    FactorVector,
    Insight,
    MarketSnapshot,
    OrderEvent,
    RegimeLabel,
    Target,
)


@dataclass
class StateEvent:
    """状态变更事件（事件溯源落盘）。"""
    ts_ns: int
    event_type: str          # snapshot/factor/insight/target/approved/order/regime/quality
    instrument_symbol: str
    payload: Any
    seq: int = 0


class SingleSourceCache:
    """
    单一真相源 Cache。

    - 内存态：各类型最新状态（按 symbol 索引）
    - 事件日志：所有变更 append-only（可重放）
    - 线程安全（读写锁）
    """

    def __init__(self, max_journal: int = 100_000):
        self._lock = threading.RLock()
        # 最新状态（symbol -> 对象）
        self._snapshots: dict[str, MarketSnapshot] = {}
        self._factors: dict[str, FactorVector] = {}
        self._insights: dict[str, Insight] = {}
        self._targets: dict[str, Target] = {}
        self._approved: dict[str, ApprovedTarget] = {}
        self._orders: dict[str, list[OrderEvent]] = {}  # client_id -> events
        self._regime: RegimeLabel | None = None
        self._quality_flags: dict[str, DataQualityFlag] = {}
        # 事件日志（append-only，环形缓冲避免无界增长）
        self._journal: list[StateEvent] = []
        self._max_journal = max_journal
        self._seq_counter = 0

    # ==================== 写入（事件溯源） ====================

    def _log_event(self, event_type: str, symbol: str, payload: Any, ts_ns: int | None = None) -> StateEvent:
        ts = ts_ns if ts_ns is not None else time.time_ns()
        with self._lock:
            self._seq_counter += 1
            evt = StateEvent(ts_ns=ts, event_type=event_type,
                             instrument_symbol=symbol, payload=payload,
                             seq=self._seq_counter)
            self._journal.append(evt)
            if len(self._journal) > self._max_journal:
                self._journal = self._journal[-self._max_journal:]
            return evt

    def update_snapshot(self, snap: MarketSnapshot) -> None:
        with self._lock:
            self._snapshots[snap.instrument.symbol] = snap
        self._log_event("snapshot", snap.instrument.symbol, snap.ts_ns, snap.ts_ns)

    def update_factor(self, fv: FactorVector) -> None:
        with self._lock:
            self._factors[fv.instrument.symbol] = fv
        self._log_event("factor", fv.instrument.symbol, list(fv.values.keys()), fv.ts_ns)

    def update_insight(self, ins: Insight) -> None:
        with self._lock:
            self._insights[ins.instrument.symbol] = ins
        self._log_event("insight", ins.instrument.symbol, ins.direction.value, ins.ts_ns)

    def update_target(self, t: Target) -> None:
        with self._lock:
            self._targets[t.instrument.symbol] = t
        self._log_event("target", t.instrument.symbol, t.target_qty, t.ts_ns)

    def update_approved(self, at: ApprovedTarget) -> None:
        with self._lock:
            self._approved[at.instrument.symbol] = at
        self._log_event("approved", at.instrument.symbol, at.approved_qty, at.ts_ns)

    def append_order(self, oe: OrderEvent) -> None:
        with self._lock:
            self._orders.setdefault(oe.client_id, []).append(oe)
        self._log_event("order", oe.instrument.symbol, oe.status.value, oe.ts_ns)

    def update_regime(self, rl: RegimeLabel) -> None:
        with self._lock:
            self._regime = rl
        self._log_event("regime", "_global", rl.regime, rl.ts_ns)

    def update_quality(self, qf: DataQualityFlag) -> None:
        with self._lock:
            self._quality_flags[qf.instrument.symbol] = qf
        self._log_event("quality", qf.instrument.symbol, qf.quality.value, qf.ts_ns)

    # ==================== 读取 ====================

    def get_snapshot(self, symbol: str) -> MarketSnapshot | None:
        with self._lock:
            return self._snapshots.get(symbol)

    def get_factor(self, symbol: str) -> FactorVector | None:
        with self._lock:
            return self._factors.get(symbol)

    def get_insight(self, symbol: str) -> Insight | None:
        with self._lock:
            return self._insights.get(symbol)

    def get_regime(self) -> RegimeLabel | None:
        with self._lock:
            return self._regime

    def get_quality(self, symbol: str) -> DataQualityFlag | None:
        with self._lock:
            return self._quality_flags.get(symbol)

    def all_symbols(self) -> list[str]:
        with self._lock:
            return list(self._snapshots.keys())

    # ==================== 事件回放（R8） ====================

    def replay(self, ts_start_ns: int, ts_end_ns: int) -> list[StateEvent]:
        """复现时间窗 [ts_start, ts_end] 内的所有事件。"""
        with self._lock:
            return [e for e in self._journal if ts_start_ns <= e.ts_ns <= ts_end_ns]

    def replay_by_type(self, event_type: str, limit: int = 1000) -> list[StateEvent]:
        """按事件类型回放（最新 limit 条）。"""
        with self._lock:
            matches = [e for e in self._journal if e.event_type == event_type]
            return matches[-limit:]

    def journal_size(self) -> int:
        with self._lock:
            return len(self._journal)

    def stats(self) -> dict:
        with self._lock:
            return {
                "snapshots": len(self._snapshots),
                "factors": len(self._factors),
                "insights": len(self._insights),
                "orders": sum(len(v) for v in self._orders.values()),
                "journal": len(self._journal),
                "seq": self._seq_counter,
            }


# 模块级单例
_default_cache = SingleSourceCache()


def get_default_cache() -> SingleSourceCache:
    return _default_cache
