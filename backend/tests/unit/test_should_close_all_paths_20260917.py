# -*- coding: utf-8 -*-
"""[调研轮20 2026-09-17] `should_close` 确认闸**全路径覆盖**的契约测试。

## 缺陷

F39 反转确认闸（2026-09-12，基于 163 笔亏损平仓反事实）此前**只装在
`manage_position` 一条路径**；"每 tick 全仓哨兵"（`full_auto_trading_service`
直接调 `resolve_thesis_hard_exit` 后 `close_position`）**没有确认**。
生产佐证：近 24h 确认闸相关日志 0 次；14 天 `thesis_should_close` 平仓 16 笔、
均亏 **−8.04**（合计 −128.59）。

## 锁定语义

1. `thesis_should_close_allowed` 是**唯一入口**（两条路径共用），判据沿用 F39 不改；
2. 开关关闭 / 异常 ⇒ fail-open（放行，保持旧行为）；
3. 哨兵必须：未确认 ⇒ **不平仓**且不 `continue`（继续走硬止损/追踪等其它保护）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.full_auto import midlong_position_manager as mpm  # noqa: E402


def _pos():
    return {"symbol": "UNI", "side": "long", "mark_price": 100.0,
            "entry_price": 100.0, "timeframe_tier": "mid"}


def test_delegates_to_f39(monkeypatch):
    """确认闸的判据沿用 F39：直接透传其结果。"""
    monkeypatch.setattr(mpm, "thesis_should_close_confirmed",
                        lambda *a, **k: (False, "wait_price_confirmation:x"))
    ok, why = mpm.thesis_should_close_allowed(
        None, position=_pos(), thesis=SimpleNamespace(), tier="mid")
    assert ok is False and "wait_price_confirmation" in why


def test_switch_off_is_old_behavior(monkeypatch):
    from backend.config import settings as s

    monkeypatch.setattr(s, "MIDLONG_THESIS_CLOSE_CONFIRM_ENABLED", False, raising=False)
    ok, why = mpm.thesis_should_close_allowed(
        None, position=_pos(), thesis=SimpleNamespace(), tier="mid")
    assert ok is True and why == "f39_off"


def test_exception_is_fail_open(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(mpm, "thesis_should_close_confirmed", _boom)
    ok, why = mpm.thesis_should_close_allowed(
        None, position=_pos(), thesis=SimpleNamespace(), tier="mid")
    assert ok is True and why == "f39_error_fail_open"


def test_sentinel_path_applies_confirmation():
    """接线护栏：哨兵必须调用确认入口，且未确认时不平仓（`_th = None` 守卫）。"""
    from backend.services import full_auto_trading_service as svc

    src = inspect.getsource(svc)
    assert "thesis_should_close_allowed" in src, "哨兵未接确认闸（等于没装）"
    assert "待确认" in src, "未确认路径不可观测"
    i = src.index("thesis_should_close_allowed")
    window = src[i - 600:i + 1400]
    assert "_th = None" in window, "未确认时必须阻止平仓"
    assert "if _th is not None:" in window, "平仓必须在确认后才执行"


def test_manage_position_still_uses_f39():
    """原有路径不得被动坏。"""
    src = inspect.getsource(mpm)
    assert "thesis_should_close_confirmed(" in src
    assert "MIDLONG_THESIS_CLOSE_CONFIRM_ENABLED" in src
