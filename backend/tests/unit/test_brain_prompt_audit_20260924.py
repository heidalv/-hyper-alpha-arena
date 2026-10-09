# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R30] 活主脑 prompt 体检的开关与节流单测。

背景：prompt 归档自 2026-08-17 停跑（台账 §45），此后"提示词里到底注入了什么"
只能靠离线重建。`_audit_context_prompt` 让它在生产里持续可核（只读、零行为变化）。
"""
from __future__ import annotations

import pytest

from backend.services.mlto import brain as B


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(B, "_CONTEXT_AUDIT_TS", {}, raising=False)
    monkeypatch.delenv("MIDLONG_BRAIN_PROMPT_AUDIT", raising=False)
    monkeypatch.delenv("MIDLONG_BRAIN_PROMPT_AUDIT_SEC", raising=False)
    # [R41] **必须关掉归档**：本文件不设归档目录，否则会写进仓库真实的
    # `data/prompt_archives/brain/`（实测污染过 2 个文件：测试 fixture 文本被当成生产归档）。
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_ARCHIVE", "false")
    yield


@pytest.mark.parametrize("val,expected", [("true", True), ("1", True), ("", True), ("false", False), ("0", False), ("off", False)])
def test_switch(monkeypatch, val, expected):
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_AUDIT", val)
    assert B._context_audit_enabled() is expected


def test_throttle_per_symbol():
    assert B._context_audit_should("BTC", 1000.0) is True     # 首次
    assert B._context_audit_should("BTC", 1001.0) is False    # 同币 1800s 内不再打
    assert B._context_audit_should("ETH", 1001.0) is True     # 另一币独立
    assert B._context_audit_should("BTC", 2800.0) is True     # 到点再打


def test_gap_configurable_and_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_AUDIT_SEC", "10")
    assert B._context_audit_gap_sec() == 10.0
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_AUDIT_SEC", "garbage")
    assert B._context_audit_gap_sec() == 1800.0


def test_audit_never_raises_on_weird_input(monkeypatch):
    """只读体检不得因奇怪输入影响主流程。"""
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_AUDIT", "true")
    for bad in (None, "", "无指标无K线", "\n\n\n"):
        B._audit_context_prompt("BTC", "mid", bad)


def test_audit_disabled_writes_nothing(monkeypatch, caplog):
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_AUDIT", "false")
    with caplog.at_level("INFO"):
        B._audit_context_prompt("BTC", "mid", "RSI EMA MACD K线" * 10)
    assert "PromptAudit" not in caplog.text
    assert B._CONTEXT_AUDIT_TS == {}


def test_audit_logs_indicators(monkeypatch, caplog):
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_AUDIT", "true")
    with caplog.at_level("INFO"):
        B._audit_context_prompt("BTC", "long", "## K线\nRSI=55 EMA20=1 MACD=0.1 ATR=2%")
    assert "PromptAudit" in caplog.text
    assert "RSI" in caplog.text and "MACD" in caplog.text
