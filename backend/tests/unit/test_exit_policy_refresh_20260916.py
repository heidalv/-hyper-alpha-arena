# -*- coding: utf-8 -*-
"""[2026-09-16 调研轮7] 存量仓策略刷新契约测试。

背景：ExitPolicy 在开仓时快照进 exit_state_json 并终身沿用 ⇒ 车道重标定
（trail 1.0/0.5、min_roi、TP 0.8/1.6/3.0、time_limit 48h）对存量仓永不生效。
实测（10:0x）：XRP 4683 +4.42% ROI 仍用旧 trail 3.0/1.5；SOL 4681 持 20.3h/-12.26%
且 min_roi=[] 无兜底；同时刻新开 BNB 4685 已带新参数 —— 同账户新旧策略并存。

刷新语义：**只刷新利润保护字段**；sl_pct / structural_stop / enabled 保持快照值
（止损边界不盘后移动）。回滚：EXIT_POLICY_REFRESH_OPEN=false。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.exit import exit_policy as xp  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("EXIT_POLICY_"):
            monkeypatch.delenv(k, raising=False)
    yield


def _old_mid_snapshot() -> xp.ExitPolicy:
    """9/16 重标定之前开仓的 mid 仓快照（实测值）。"""
    return xp.ExitPolicy(
        lane="mid", sl_pct=4.67, tp_pct=None, time_limit_sec=604800,
        trailing_activation_pct=3.0, trailing_callback_pct=1.5,
        structural_stop="price", min_roi=(), tp_stages=(2.0, 3.5, 5.5),
        safety_net_cap=0.80, enabled=True,
    )


def test_refresh_swaps_protection_keeps_stop():
    snap = _old_mid_snapshot()
    merged, chg = xp.refreshed_policy(snap, "mid")
    # 利润保护被刷新到车道当前值（代码默认：验收轮6 重标定）
    assert merged.trailing_activation_pct == pytest.approx(1.0)
    assert merged.trailing_callback_pct == pytest.approx(0.5)
    assert merged.min_roi == ((43200, 0.5), (86400, 0.0))
    assert merged.tp_stages == (0.8, 1.6, 3.0)
    assert merged.time_limit_sec == 172800
    # 止损边界保持快照值（不盘后移动）
    assert merged.sl_pct == pytest.approx(4.67)
    assert merged.structural_stop == "price"
    assert merged.enabled is True
    # 变更明细可审计
    assert "trailing_activation_pct" in chg and chg["trailing_activation_pct"]["from"] == 3.0
    assert "min_roi" in chg and chg["min_roi"]["from"] == []


def test_no_change_when_already_current(monkeypatch):
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT", "1.0")
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_CALLBACK_PCT", "0.5")
    monkeypatch.setenv("EXIT_POLICY_MID_TP_STAGES", "0.8,1.6,3.0")
    monkeypatch.setenv("EXIT_POLICY_MID_MIN_ROI", "43200:0.5,86400:0.0")
    monkeypatch.setenv("EXIT_POLICY_MID_TIME_LIMIT_SEC", "172800")
    cur = xp.ExitPolicy.for_lane("mid")
    merged, chg = xp.refreshed_policy(cur, "mid")
    assert chg == {}, f"已是最新策略不应产生变更: {chg}"
    assert merged == cur


def test_refresh_enabled_default_and_rollback(monkeypatch):
    assert xp.refresh_enabled() is True          # 默认开
    monkeypatch.setenv("EXIT_POLICY_REFRESH_OPEN", "false")
    assert xp.refresh_enabled() is False


def test_env_override_drives_refresh(monkeypatch):
    """车道参数改了 env，存量仓刷新即跟随（这是本机制存在的意义）。"""
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT", "0.6")
    monkeypatch.setenv("EXIT_POLICY_MID_MIN_ROI", "3600:1.0")
    merged, chg = xp.refreshed_policy(_old_mid_snapshot(), "mid")
    assert merged.trailing_activation_pct == pytest.approx(0.6)
    assert merged.min_roi == ((3600, 1.0),)
    assert set(chg) >= {"trailing_activation_pct", "min_roi"}


def test_long_lane_snapshot_refresh_keeps_chandelier():
    snap = xp.ExitPolicy(
        lane="long", sl_pct=None, tp_pct=None, time_limit_sec=None,
        trailing_activation_pct=None, trailing_callback_pct=None,
        structural_stop="chandelier", min_roi=(), tp_stages=(8.0, 15.0, 25.0),
        safety_net_cap=0.80, enabled=True,
    )
    merged, chg = xp.refreshed_policy(snap, "long")
    # 长线车道保持「只认 L1/Chandelier」：不得被刷出 trailing/time_limit/min_roi
    assert merged.structural_stop == "chandelier"
    assert merged.trailing_activation_pct is None
    assert merged.min_roi == ()
    assert merged.time_limit_sec is None
    assert chg == {}
