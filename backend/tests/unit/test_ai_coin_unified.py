# -*- coding: utf-8 -*-
"""AI 选币统一状态层单测（2026-08-28 归一：单存储 + 单入口）。"""
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import pytest

from backend.services import ai_coin_unified as U
from backend.services.auto_coin_selector import (
    _ai_mid_sticky_path,
    _load_ai_mid_sticky,
    _save_ai_mid_sticky,
)


@pytest.fixture()
def sid():
    s = "fa_unified_test_" + uuid.uuid4().hex[:10]
    yield s
    # 清理统一状态 + 遗留 sticky
    try:
        os.remove(U._state_path(s))
    except Exception:
        pass
    try:
        os.remove(_ai_mid_sticky_path(s))
    except Exception:
        pass


def test_short_mid_roundtrip_and_merge(sid):
    U.set_tier_symbols(sid, "short", ["HYPE", "LINK"], reason="selector_inject")
    U.set_tier_symbols(sid, "mid", ["ONDO", "HYPE"], reason="resample")
    assert sorted(U.get_ai_coin_symbols(sid, tier="short")) == ["HYPE", "LINK"]
    assert sorted(U.get_ai_coin_symbols(sid, tier="mid")) == ["HYPE", "ONDO"]
    # 合并去重、顺序稳定（short 先 mid 后）
    assert U.get_ai_coin_symbols(sid, tier=None) == ["HYPE", "LINK", "ONDO"]


def test_mid_sticky_io_routes_through_unified(sid):
    # 旧 API 写入 → 统一状态可读（归一：选择器不用改调用方式）
    _save_ai_mid_sticky(sid, ["ONDO", "INJ"], reason="resample from midlong_board")
    assert sorted(U.get_ai_coin_symbols(sid, tier="mid")) == ["INJ", "ONDO"]
    # 旧 API 读取 → 结构契约保持 {symbols, updated_at, reason}
    sticky = _load_ai_mid_sticky(sid)
    assert sorted(sticky.get("symbols") or []) == ["INJ", "ONDO"]
    assert "updated_at" in sticky and "reason" in sticky
    assert "midlong_board" in sticky["reason"]


def test_legacy_sticky_migration(sid):
    # 先写旧文件（模拟归一前存量），再读统一入口 → 自动迁移
    legacy = _ai_mid_sticky_path(sid)
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    with open(legacy, "w", encoding="utf-8") as f:
        json.dump({
            "session_id": sid,
            "symbols": ["ADA", "FIL"],
            "updated_at": time.time(),
            "updated_iso": "2026-08-28T00:00:00Z",
            "reason": "resample from midlong_board",
        }, f)
    assert sorted(U.get_ai_coin_symbols(sid, tier="mid")) == ["ADA", "FIL"]
    # 迁移后统一状态文件存在
    assert os.path.exists(U._state_path(sid))
    state = json.load(open(U._state_path(sid), encoding="utf-8"))
    assert state["mid"]["reason"] == "resample from midlong_board"


def test_missing_session_returns_empty(sid):
    assert U.get_ai_coin_symbols(sid, tier=None) == []
    assert U.get_tier_state(sid, "mid") == {}
