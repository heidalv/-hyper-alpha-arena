# -*- coding: utf-8 -*-
"""[§95 契约 2026-09-11 / 目标③「不产生悬挂仓位」] 出场抑制的**可选上限**（P24-C）。

背景：熔断抑制让"该通道的离场"被压住，若仓位始终不触发保护性通道，就会被**无限期压住**
（§93.3 残留风险 2）。本开关给同一 `tier|通道` 的抑制设次数上限（窗口内计数），
超过即放行；默认 `EXIT_SUPPRESS_MAX_COUNT=0` = 关闭 ⇒ 与旧行为完全一致（零行为变化）。

本文件锁：默认关闭、上限语义、窗口过期、保护通道不计数、键归一化、闸门接线。
"""
from __future__ import annotations

import logging

import pytest

import backend.services.source_attribution as sa
from backend.services.exit import channel_breaker_gate as g


@pytest.fixture()
def fresh_attr(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("EXIT_CHANNEL_REBUILD_ON_LOAD", "false")
    a = sa.SourceAttribution()
    monkeypatch.setattr(sa, "attribution", a)
    monkeypatch.setattr(a, "exit_channel_shadow", lambda r, t: True, raising=False)
    monkeypatch.setattr(a, "exit_channel_evidence_fresh",
                        lambda r, t: (True, 0.0, 7.0), raising=False)
    return a


def test_cap_off_by_default(fresh_attr, monkeypatch):
    """默认关闭 ⇒ 连续抑制不设限（与旧行为一致）。"""
    monkeypatch.delenv("EXIT_SUPPRESS_MAX_COUNT", raising=False)
    for _ in range(3):
        sup, why = g.should_suppress("trend_broken: x", "mid")
        assert sup is True and why == "mid|trend_broken"


def test_cap_enforced_then_release(fresh_attr, monkeypatch, caplog):
    """上限=2：第 1、2 次抑制，第 3 次放行且带 WARNING（不再悬挂）。"""
    monkeypatch.setenv("EXIT_SUPPRESS_MAX_COUNT", "2")
    monkeypatch.setenv("EXIT_SUPPRESS_WINDOW_H", "24")
    for i in range(2):
        sup, why = g.should_suppress("midlong: x", "mid")
        assert sup is True, (i, why)
    with caplog.at_level(logging.WARNING):
        sup, why = g.should_suppress("midlong: x", "mid")
    assert sup is False and why.startswith("suppress_cap_reached:mid|midlong"), why
    assert "抑制已达上限" in caplog.text


def test_window_expiry_clears_count(fresh_attr, monkeypatch):
    """窗口过期 ⇒ 计数清零，重新可抑制（cap 是"窗口内"计数）。"""
    monkeypatch.setenv("EXIT_SUPPRESS_MAX_COUNT", "1")
    monkeypatch.setenv("EXIT_SUPPRESS_WINDOW_H", "1")
    fake = {"t": 1_700_000_000.0}
    monkeypatch.setattr(sa.time, "time", lambda: fake["t"])
    sup, _ = g.should_suppress("trend_broken: x", "mid")
    assert sup is True
    sup2, why2 = g.should_suppress("trend_broken: x", "mid")
    assert sup2 is False and "suppress_cap_reached" in why2
    fake["t"] += 2 * 3600.0          # 2 小时后窗口过期
    sup3, why3 = g.should_suppress("trend_broken: x", "mid")
    assert sup3 is True, why3


def test_protected_channels_do_not_count(fresh_attr, monkeypatch):
    """保护性通道不参与抑制 ⇒ 不计数（计数只属于真实抑制）。"""
    monkeypatch.setenv("EXIT_SUPPRESS_MAX_COUNT", "1")
    g.should_suppress("sl: x", "mid")
    assert fresh_attr.suppression_count("sl", "mid") == 0


def test_note_normalizes_keys(fresh_attr):
    """`note_suppression` 的键 = `tier|归一化通道`（与 shadow 查询同口径）。"""
    fresh_attr.note_suppression("trend_broken: 方向破坏", "mid")
    assert fresh_attr.suppression_count("trend_broken", "mid") == 1
    assert fresh_attr.suppression_count("trend_broken: 方向破坏", "mid") == 1


def test_gate_wiring_source_guard():
    """接线护栏：闸门必须真的调用 `suppression_count`/`note_suppression`（只定义没用）。"""
    src = g.should_suppress.__code__.co_consts
    import inspect
    body = inspect.getsource(g.should_suppress)
    assert "suppression_count" in body and "note_suppression" in body
    assert "EXIT_SUPPRESS_MAX_COUNT" in body and "EXIT_SUPPRESS_WINDOW_H" not in body  # 窗口在 attribution 侧
