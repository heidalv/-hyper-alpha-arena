# -*- coding: utf-8 -*-
"""[2026-09-11] 图审立场建议「方向一致性」契约测试。

现场根因：图审信号 direction=long 却同时给 position_advice=no_new_long（自相矛盾），
且每 65~156min 重签一次 → ETH/UNI/XPL 多头通道几乎全时段被压死、纸面盘零成交。
48h 审计 1,419 条否决中 98.1% 来自单条 no_new_long。

契约（开关 MIDLONG_CHART_ADVICE_DIRECTION_CONSISTENT 默认 true）：
- 开多时 no_new_long：信号方向看空(-1) → 否决；方向看多(+1)/中性(0) → 放行（可见日志）。
- 开空时 no_new_short：信号方向看多(+1) → 否决；方向看空(-1)/中性(0) → 放行。
- 开关=false：TTL 内一律否决（旧口径，回滚档）。
- TTL 过期（>180min）仍放行（既有契约不回退）。
"""
import os
import time

import pytest

from backend.services.full_auto import midlong_chart_gate as g


def _signal(direction: int, advice: str, age_min: int = 30):
    return {
        "symbol": "ETH",
        "direction": direction,
        "strength": 4.0,
        "created_ms": int(time.time() * 1000) - age_min * 60 * 1000,
        "payload": {"position_advice": advice},
    }


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setenv("MIDLONG_CHART_GATE_ENABLED", "true")
    monkeypatch.setenv("MIDLONG_CHART_ADVICE_DIRECTION_CONSISTENT", "true")
    monkeypatch.setenv("MIDLONG_CHART_ADVICE_TTL_MIN", "180")
    monkeypatch.setenv("MIDLONG_CHART_MAX_SIGNAL_AGE_MIN", "240")
    monkeypatch.setenv("MIDLONG_CHART_REQUIRED", "false")


def test_long_advice_bearish_vetoes(monkeypatch):
    monkeypatch.setattr(g, "_latest_chart_signal", lambda sym: _signal(-1, "no_new_long"))
    allow, reason, _ = g.chart_gate_check("ETH", "buy", tier="mid")
    assert allow is False
    assert "no_new_long" in reason and "看空" in reason


def test_long_advice_bullish_ignored(monkeypatch):
    """direction=long + no_new_long 自相矛盾 → 不否决（修复核心）。"""
    monkeypatch.setattr(g, "_latest_chart_signal", lambda sym: _signal(1, "no_new_long"))
    allow, reason, _ = g.chart_gate_check("ETH", "buy", tier="mid")
    assert allow is True
    assert "不一致" in reason


def test_long_advice_neutral_ignored(monkeypatch):
    monkeypatch.setattr(g, "_latest_chart_signal", lambda sym: _signal(0, "no_new_long"))
    allow, reason, _ = g.chart_gate_check("ETH", "buy", tier="mid")
    assert allow is True
    assert "不一致" in reason


def test_short_advice_bullish_vetoes(monkeypatch):
    monkeypatch.setattr(g, "_latest_chart_signal", lambda sym: _signal(1, "no_new_short"))
    allow, reason, _ = g.chart_gate_check("ETH", "sell", tier="mid")
    assert allow is False
    assert "no_new_short" in reason and "看多" in reason


def test_short_advice_bearish_ignored(monkeypatch):
    monkeypatch.setattr(g, "_latest_chart_signal", lambda sym: _signal(-1, "no_new_short"))
    allow, reason, _ = g.chart_gate_check("ETH", "sell", tier="mid")
    assert allow is True
    assert "不一致" in reason


def test_rollback_flag_restores_old_veto(monkeypatch):
    monkeypatch.setenv("MIDLONG_CHART_ADVICE_DIRECTION_CONSISTENT", "false")
    monkeypatch.setattr(g, "_latest_chart_signal", lambda sym: _signal(1, "no_new_long"))
    allow, reason, _ = g.chart_gate_check("ETH", "buy", tier="mid")
    assert allow is False
    assert "position_advice=no_new_long" in reason


def test_ttl_expired_still_fail_open(monkeypatch):
    monkeypatch.setattr(g, "_latest_chart_signal", lambda sym: _signal(-1, "no_new_long", age_min=200))
    allow, reason, _ = g.chart_gate_check("ETH", "buy", tier="mid")
    assert allow is True
    assert "陈旧" in reason
