"""
事件溯源（整改#9）—— Phase 1 shadow + Phase 2 双写/投影读/对拍。

默认关（EVENT_SOURCING_ENABLED=false），开启后写路径双写事件并维护内存投影。
"""
from backend.services.event_sourcing.event_store import (
    DomainEvent,
    EventSourcedPositionRepository,
    EventStore,
    PositionProjection,
    get_event_store,
    is_enabled,
    EVT_ORDER_SUBMITTED,
    EVT_ORDER_FILLED,
    EVT_ORDER_CANCELLED,
    EVT_POSITION_OPENED,
    EVT_POSITION_CHANGED,
    EVT_POSITION_CLOSED,
    EVT_ACCOUNT_UPDATED,
)
from backend.services.event_sourcing.phase2 import (
    get_live_repository,
    get_reconcile_stats,
    is_phase2_read_enabled,
    is_phase2_reconcile_enabled,
    record_position_event,
    reconcile_db_vs_projection,
    projection_positions_for_account,
    reset_live_repository_for_tests,
)
from backend.services.event_sourcing.phase3 import (
    bootstrap_db_position_row,
    get_phase3_stats,
    is_phase3_enabled,
    is_projection_read_active,
    resolve_position_list_for_read,
    warm_startup_projection,
)
from backend.services.event_sourcing.phase4 import (
    get_phase4_stats,
    is_write_retirement_enabled,
    run_retirement_sync,
    should_use_event_first_write,
)

__all__ = [
    "DomainEvent",
    "EventStore",
    "PositionProjection",
    "EventSourcedPositionRepository",
    "get_event_store",
    "get_live_repository",
    "get_reconcile_stats",
    "is_enabled",
    "is_phase2_read_enabled",
    "is_phase2_reconcile_enabled",
    "is_phase3_enabled",
    "is_projection_read_active",
    "get_phase3_stats",
    "bootstrap_db_position_row",
    "warm_startup_projection",
    "resolve_position_list_for_read",
    "get_phase4_stats",
    "is_write_retirement_enabled",
    "should_use_event_first_write",
    "run_retirement_sync",
    "record_position_event",
    "reconcile_db_vs_projection",
    "projection_positions_for_account",
    "reset_live_repository_for_tests",
    "EVT_ORDER_SUBMITTED",
    "EVT_ORDER_FILLED",
    "EVT_ORDER_CANCELLED",
    "EVT_POSITION_OPENED",
    "EVT_POSITION_CHANGED",
    "EVT_POSITION_CLOSED",
    "EVT_ACCOUNT_UPDATED",
]
