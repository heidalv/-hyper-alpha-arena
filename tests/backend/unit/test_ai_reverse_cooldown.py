"""Unit tests for P3 M2 — ai_reverse 冷却 (reentry_cooldown 扩展)."""

import time

import pytest

from backend.services.reentry_cooldown import (
    clear_ai_reverse,
    is_ai_reverse_blocked,
    record_ai_reverse,
)


@pytest.fixture(autouse=True)
def _reset():
    clear_ai_reverse(1, "ETH")
    clear_ai_reverse(1, "BTC")
    yield
    clear_ai_reverse(1, "ETH")
    clear_ai_reverse(1, "BTC")


def test_no_record_not_blocked():
    blocked, reason = is_ai_reverse_blocked(1, "ETH", cooldown_sec=3600)
    assert blocked is False
    assert reason == ""


def test_zero_cooldown_means_disabled():
    record_ai_reverse(1, "ETH")
    blocked, _ = is_ai_reverse_blocked(1, "ETH", cooldown_sec=0)
    assert blocked is False


def test_within_cooldown_blocks():
    record_ai_reverse(1, "ETH")
    blocked, reason = is_ai_reverse_blocked(1, "ETH", cooldown_sec=3600)
    assert blocked is True
    assert "cooldown" in reason.lower() or "冷却" in reason


def test_different_symbol_not_blocked():
    record_ai_reverse(1, "ETH")
    blocked, _ = is_ai_reverse_blocked(1, "BTC", cooldown_sec=3600)
    assert blocked is False


def test_different_account_not_blocked():
    record_ai_reverse(1, "ETH")
    blocked, _ = is_ai_reverse_blocked(2, "ETH", cooldown_sec=3600)
    assert blocked is False


def test_expired_record_is_cleared_and_allows(monkeypatch):
    # 手动把时间戳改到 2h 前，冷却 1h 应该已过期
    from backend.services import reentry_cooldown as rc
    rc._ai_reverse_last[f"1_ETH"] = time.time() - 7200
    blocked, _ = is_ai_reverse_blocked(1, "ETH", cooldown_sec=3600)
    assert blocked is False
    # 过期后应被清理
    assert "1_ETH" not in rc._ai_reverse_last


def test_empty_symbol_noop():
    record_ai_reverse(1, "")  # 不应崩
    blocked, _ = is_ai_reverse_blocked(1, "", cooldown_sec=3600)
    assert blocked is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
