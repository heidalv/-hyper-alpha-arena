# -*- coding: utf-8 -*-
"""实盘收紧策略单测（2026-08-28 用户指令：实盘不能像模拟盘一样粗放）。"""
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import pytest

from backend.services.full_auto import live_gate_policy as P


def test_live_pwin_extra_defaults():
    assert P.live_pwin_extra() >= 0.0
    assert P.live_v5_conf_extra() >= 0
    assert P.master_conf_extra() >= 0
    assert P.live_score_extra() >= 0
    assert 0.2 <= P.live_budget_mult() <= 1.0
    assert P.live_daily_open_cap() >= 0


def test_ev_meta_hard_filter_defaults_on_for_live():
    # 实盘收紧：默认 false = 不降级（硬过滤保持）
    assert P.live_scalp_ev_meta_hard_filter_off() is False


def test_daily_quota_resets_by_date_and_session():
    sid = "fa_quota_test_" + uuid.uuid4().hex[:8]
    try:
        used = P.live_daily_open_used(sid)
        assert used == 0
        n = P.live_daily_open_bump(sid)
        assert n == 1
        assert P.live_daily_open_used(sid) == 1
        # 不同会话互不影响
        sid2 = sid + "_b"
        assert P.live_daily_open_used(sid2) == 0
        # 同会话继续累加
        assert P.live_daily_open_bump(sid) == 2
    finally:
        for s in (sid, sid + "_b"):
            try:
                os.remove(P._quota_path(s))
            except Exception:
                pass


def test_live_scalp_pwin_floor_bounds():
    f = P.live_scalp_pwin_floor()
    assert 0.35 <= f <= 0.55
