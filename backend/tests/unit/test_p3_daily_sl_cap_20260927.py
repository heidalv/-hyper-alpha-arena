# -*- coding: utf-8 -*-
"""[P3 大轮回 2026-09-27] §6.1 重入规则：同币当日 ≥3 次止损后当日禁开。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services import reentry_cooldown as rc  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in list(os.environ):
        if k.startswith("REENTRY_DAILY_SL_LIMIT"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REENTRY_DAILY_SL_LIMIT", "3")
    rc._daily_sl.clear()
    # 内存不足时 DB 兜底查询 → 本测试钉成 0（纯内存语义）
    monkeypatch.setattr(rc, "_daily_sl_db_count", lambda *a, **k: 0)
    # 隔离本测试目标（当日止损上限）：同向/亏损/SL/TP 冷却全部归零，避免干扰断言
    monkeypatch.setattr(rc, "_get_cooldown_sec", lambda tier: 0)
    for k in ("REENTRY_SL_COOLDOWN_SEC_MID", "REENTRY_SL_COOLDOWN_SEC_SHORT",
              "REENTRY_SL_COOLDOWN_SEC_LONG", "REENTRY_LOSS_COOLDOWN_SEC_MID",
              "REENTRY_LOSS_COOLDOWN_SEC_SHORT", "REENTRY_LOSS_COOLDOWN_SEC_LONG",
              "REENTRY_MIN_COOLDOWN_AFTER_TP_SEC"):
        monkeypatch.setenv(k, "0")


@pytest.fixture
def _patch_db_count(monkeypatch):
    """把 _daily_sl_blocked 内部 DB 查询换成可脚本化的假实现。"""
    def _fake_db_count(account_id, symbol, tier):
        return int(os.environ.get("FAKE_DB_SL_COUNT", "0"))
    monkeypatch.setattr(rc, "_daily_sl_db_count", _fake_db_count)


def _sl_close(account=14, symbol="UNI", tier="mid"):
    rc.record_full_close(account, symbol, "long", tier=tier,
                         close_pnl=-5.0, close_reason="sl")


def test_two_sl_same_day_not_blocked():
    _sl_close()
    _sl_close()
    blocked, reason = rc.reopen_blocked(14, "UNI", "buy", new_tier="mid")
    assert not blocked, reason


def test_three_sl_same_day_blocks():
    for _ in range(3):
        _sl_close()
    blocked, reason = rc.reopen_blocked(14, "UNI", "buy", new_tier="mid")
    assert blocked and "3 次止损" in reason


def test_non_sl_close_does_not_count():
    rc.record_full_close(14, "UNI", "long", tier="mid", close_pnl=-5.0,
                         close_reason="breakeven_tp")
    _sl_close()
    _sl_close()
    blocked, _ = rc.reopen_blocked(14, "UNI", "buy", new_tier="mid")
    assert not blocked


def test_tier_isolation():
    for _ in range(3):
        _sl_close(tier="mid")
    blocked_mid, _ = rc.reopen_blocked(14, "UNI", "buy", new_tier="mid")
    assert blocked_mid
    blocked_long, _ = rc.reopen_blocked(14, "UNI", "buy", new_tier="long")
    assert not blocked_long, "long 车道不受 mid 当日止损上限影响"


def test_symbol_isolation():
    for _ in range(3):
        _sl_close(symbol="UNI")
    blocked, _ = rc.reopen_blocked(14, "SOL", "buy", new_tier="mid")
    assert not blocked


def test_direction_symmetry():
    """空头当日 3 次止损同样禁开（方向对称，§1.2）。"""
    for _ in range(3):
        rc.record_full_close(14, "UNI", "short", tier="mid",
                             close_pnl=-5.0, close_reason="sl")
    blocked, reason = rc.reopen_blocked(14, "UNI", "sell", new_tier="mid")
    assert blocked and "3 次止损" in reason


def test_db_fallback_counts_after_restart(monkeypatch, _patch_db_count):
    """内存为空时用 DB 口径（进程重启后不蒸发）。"""
    monkeypatch.setenv("FAKE_DB_SL_COUNT", "3")
    blocked, reason = rc.reopen_blocked(14, "UNI", "buy", new_tier="mid")
    assert blocked and "DB 口径" in reason


def test_limit_zero_disables(monkeypatch):
    monkeypatch.setenv("REENTRY_DAILY_SL_LIMIT_MID", "0")
    for _ in range(4):
        _sl_close()
    blocked, _ = rc.reopen_blocked(14, "UNI", "buy", new_tier="mid")
    assert not blocked
