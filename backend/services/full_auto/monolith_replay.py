"""
Monolith 仓位轨迹 ↔ 事件溯源对拍（C7 特征化测试网）。

将 monolith 内存仓位操作序列转为 DomainEvent 流，重放后应与 monolith 视图一致。
零风险：纯离线转换，不接入实盘写路径。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.services.event_sourcing import (
    DomainEvent,
    EVT_POSITION_CHANGED,
    EVT_POSITION_CLOSED,
    EVT_POSITION_OPENED,
)


def apply_monolith_op(state: Dict[str, dict], op: dict) -> None:
    """在 monolith 内存视图上应用单步操作（特征化参考实现）。"""
    kind = op.get("op")
    pid = str(op["id"])
    if kind == "open":
        state[pid] = {
            "position_id": pid,
            "symbol": op.get("symbol", ""),
            "side": op.get("side", ""),
            "size": float(op.get("size", 0)),
            "entry_price": float(op.get("entry_price", 0)),
            "status": "open",
            "realized_pnl": 0.0,
        }
    elif kind == "change":
        pos = state.get(pid)
        if not pos:
            return
        if "size" in op:
            pos["size"] = float(op["size"])
        if "entry_price" in op:
            pos["entry_price"] = float(op["entry_price"])
    elif kind == "close":
        pos = state.get(pid)
        if not pos:
            return
        pos["status"] = "closed"
        pos["size"] = 0.0
        pos["exit_price"] = float(op.get("exit_price", pos.get("entry_price", 0)))
        pos["realized_pnl"] = float(op.get("realized_pnl", 0))


def build_monolith_view(ops: List[dict]) -> Dict[str, dict]:
    """从操作序列构建 monolith 内存仓位视图。"""
    state: Dict[str, dict] = {}
    for op in ops:
        apply_monolith_op(state, op)
    return state


def ops_to_events(ops: List[dict]) -> List[DomainEvent]:
    """将 monolith 操作序列转为 DomainEvent 流。"""
    events: List[DomainEvent] = []
    for op in ops:
        kind = op.get("op")
        pid = str(op["id"])
        if kind == "open":
            events.append(DomainEvent(
                EVT_POSITION_OPENED, pid,
                {
                    "symbol": op.get("symbol", ""),
                    "side": op.get("side", ""),
                    "size": float(op.get("size", 0)),
                    "entry_price": float(op.get("entry_price", 0)),
                },
            ))
        elif kind == "change":
            payload: Dict[str, Any] = {}
            if "size" in op:
                payload["size"] = float(op["size"])
            if "entry_price" in op:
                payload["entry_price"] = float(op["entry_price"])
            if payload:
                events.append(DomainEvent(EVT_POSITION_CHANGED, pid, payload))
        elif kind == "close":
            events.append(DomainEvent(
                EVT_POSITION_CLOSED, pid,
                {
                    "exit_price": float(op.get("exit_price", 0)),
                    "realized_pnl": float(op.get("realized_pnl", 0)),
                },
            ))
    return events


def replay_matches_monolith(
    ops: List[dict],
    *,
    store_path: Optional[str] = None,
) -> bool:
    """事件重放结果是否与 monolith 视图逐字段一致（size/status/realized_pnl）。"""
    from backend.services.event_sourcing import EventStore, EventSourcedPositionRepository

    monolith = build_monolith_view(ops)
    store = EventStore(log_path=store_path) if store_path else EventStore()
    # 清空：用临时路径时天然为空；否则靠 force 写入新文件
    for ev in ops_to_events(ops):
        store.append(ev, force=True)
    replayed = EventSourcedPositionRepository(store).rebuild_from_events()

    if set(monolith.keys()) != set(replayed.keys()):
        return False
    for pid, mpos in monolith.items():
        rpos = replayed.get(pid, {})
        for key in ("status", "size", "realized_pnl", "symbol", "side"):
            if key in mpos and mpos.get(key) != rpos.get(key):
                return False
    return True
