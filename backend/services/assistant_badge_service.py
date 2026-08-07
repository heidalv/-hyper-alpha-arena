"""Alpha 助手浮动角标 — P0 优先，否则 distinct 错误类型数。"""

from __future__ import annotations

from typing import Any, Dict


def build_assistant_badge(*, window_hours: int = 24) -> Dict[str, Any]:
    from backend.services.log_digest_service import build_digest

    digest = build_digest(window_hours=window_hours)
    total = int(digest.get("total_errors") or 0)
    distinct = int(digest.get("distinct_groups") or 0)
    p0 = int(digest.get("p0_count") or 0)

    if not digest.get("has_log_errors") or total <= 0:
        return {
            "count": 0,
            "kind": "none",
            "label": "错误",
            "hint": "24h 后台日志无 ERROR",
            "p0_count": 0,
            "distinct_groups": 0,
            "total_errors": 0,
            "top_entries": [],
        }

    if p0 > 0:
        count = p0
        kind = "p0"
        hint = f"24h 严重错误 {p0} 类（共 {total} 条 ERROR，{distinct} 类错误）"
    else:
        count = distinct if distinct > 0 else min(total, 99)
        kind = "error_types"
        hint = f"24h 后台错误 {count} 类（共 {total} 条）"

    return {
        "count": count,
        "kind": kind,
        "label": "错误",
        "hint": hint,
        "p0_count": p0,
        "distinct_groups": distinct,
        "total_errors": total,
        "top_entries": (digest.get("entries") or [])[:5],
    }
