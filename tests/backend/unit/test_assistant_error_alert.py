"""assistant_error_alert_service 单元测试。"""
from backend.services.assistant_error_alert_service import (
    ERROR_ALERT_MARKER,
    error_alert_fingerprint,
    format_entry_alert,
)


def test_format_entry_alert_contains_marker_and_severity():
    text = format_entry_alert(
        {"logger": "backend.main", "count": 12, "severity_hint": "P0", "sample": "boom"},
        badge_hint="24h 严重错误",
    )
    assert ERROR_ALERT_MARKER in text
    assert "P0" in text
    assert "backend.main" in text
    assert "boom" in text


def test_error_alert_fingerprint_stable():
    badge = {
        "kind": "p0",
        "count": 2,
        "total_errors": 10,
        "top_entries": [{"logger": "a", "count": 3, "severity_hint": "P1"}],
    }
    assert error_alert_fingerprint(badge) == error_alert_fingerprint(badge)
