# -*- coding: utf-8 -*-
"""[M1 2026-09-14] 校准器 MIN_TS 修复切点契约测试。

背景：trend 校准器 32 笔旧样本（修复前负期望结构，胜率 0.375）→ p_win=0.393 →
EV 恒负 → long 车道零成交 → 校准器无新样本（死亡螺旋）。
MIN_TS 把样本窗口锚到"修复上线时刻"之后；MIN_TS=0 保持旧行为逐字节一致。

契约：
- MIN_TS=0 → 窗口下界 = now − lookback_days（旧行为）。
- MIN_TS 在窗口内 → 下界 = MIN_TS 对应时刻。
- MIN_TS 早于窗口下界 → 仍取 lookback 窗口（不放大窗口）。
- MIN_TS 非法值 → 忽略（回退旧行为）。
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

import backend.config.settings as settings
from backend.services.calibration.confidence_calibrator import ConfidenceCalibrator


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setattr(settings, "TREND_CALIBRATOR_MIN_TS", 0.0, raising=False)


def _cal():
    return ConfidenceCalibrator(
        signal_type="trend_agent_score", config_prefix="TREND_CALIBRATOR", pivot_default=56.0,
    )


def test_min_ts_zero_keeps_lookback_window(monkeypatch):
    monkeypatch.setattr(settings, "TREND_CALIBRATOR_MIN_TS", 0.0, raising=False)
    now = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    cut = _cal()._effective_cutoff(60, now=now)
    assert cut == now - timedelta(days=60)


def test_min_ts_inside_window_raises_lower_bound(monkeypatch):
    now = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    min_ts = (now - timedelta(days=5)).timestamp()  # 修复切点=5 天前
    monkeypatch.setattr(settings, "TREND_CALIBRATOR_MIN_TS", float(min_ts), raising=False)
    cut = _cal()._effective_cutoff(60, now=now)
    assert abs((cut - (now - timedelta(days=5))).total_seconds()) < 1.0


def test_min_ts_older_than_window_does_not_widen(monkeypatch):
    now = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    old_ts = (now - timedelta(days=90)).timestamp()
    monkeypatch.setattr(settings, "TREND_CALIBRATOR_MIN_TS", float(old_ts), raising=False)
    cut = _cal()._effective_cutoff(60, now=now)
    assert cut == now - timedelta(days=60)


def test_min_ts_invalid_value_ignored(monkeypatch):
    monkeypatch.setattr(settings, "TREND_CALIBRATOR_MIN_TS", float(2**62), raising=False)
    now = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    cut = _cal()._effective_cutoff(60, now=now)
    assert cut == now - timedelta(days=60)
