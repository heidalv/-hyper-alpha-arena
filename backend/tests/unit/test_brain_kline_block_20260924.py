# -*- coding: utf-8 -*-
"""[续作 R18] 真 K 线块注入活主脑 prompt 的单测。

背景（§52/§54.1 三条证据）：活主脑 prompt 里没有 K 线本体，只有派生标量；
9,037 字符的真 OHLCV 块只存在于另一条（agent/图表复核）路径。
现以紧凑形式注入（默认 true；周期/根数/上限可配；失败只 debug）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.mlto import brain as B

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MIDLONG_BRAIN_KLINE_BLOCK", raising=False)
    monkeypatch.delenv("MIDLONG_BRAIN_KLINE_BLOCK_PERIODS", raising=False)
    monkeypatch.delenv("MIDLONG_BRAIN_KLINE_BLOCK_COUNT", raising=False)
    monkeypatch.delenv("MIDLONG_BRAIN_KLINE_BLOCK_MAX_CHARS", raising=False)
    yield


def test_switch_and_defaults(monkeypatch):
    assert B._kline_block_enabled() is True
    assert B._kline_block_periods() == ["1h", "4h", "1d"]
    assert B._kline_block_count() == 12
    assert B._kline_block_max_chars() == 3000
    monkeypatch.setenv("MIDLONG_BRAIN_KLINE_BLOCK", "false")
    assert B._kline_block_enabled() is False
    monkeypatch.setenv("MIDLONG_BRAIN_KLINE_BLOCK", "garbage")
    assert B._kline_block_enabled() is False  # 非法值 fail-closed：不注入


def test_periods_and_limits_configurable(monkeypatch):
    monkeypatch.setenv("MIDLONG_BRAIN_KLINE_BLOCK_PERIODS", "1h,1d,1w")
    assert B._kline_block_periods() == ["1h", "1d", "1w"]
    monkeypatch.setenv("MIDLONG_BRAIN_KLINE_BLOCK_PERIODS", " ,  ,")
    assert B._kline_block_periods() == ["1h", "4h", "1d"]  # 空输入回落默认
    monkeypatch.setenv("MIDLONG_BRAIN_KLINE_BLOCK_COUNT", "8")
    assert B._kline_block_count() == 8
    monkeypatch.setenv("MIDLONG_BRAIN_KLINE_BLOCK_COUNT", "garbage")
    assert B._kline_block_count() == 12
    monkeypatch.setenv("MIDLONG_BRAIN_KLINE_BLOCK_MAX_CHARS", "1500")
    assert B._kline_block_max_chars() == 1500
    monkeypatch.setenv("MIDLONG_BRAIN_KLINE_BLOCK_MAX_CHARS", "garbage")
    assert B._kline_block_max_chars() == 3000


def test_injection_site_has_guard_marker_and_cap():
    """源码守卫：注入点必须带开关、标题、截断上限与 fail-safe。"""
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8")
    i = src.find("_kline_block = ")
    assert i >= 0, "未找到 K 线块注入点"
    seg = src[i:i + 1400]
    assert "if _kline_block_enabled():" in seg
    assert "【K线块】" in seg
    assert "build_kline_block" in seg
    assert "_kline_block_max_chars()" in seg, "必须有截断上限，防止把 prompt 撑爆"
    assert "except Exception" in seg, "注入失败必须 fail-safe"
    # 注入成功后与体检同节流打一条可见确认
    assert "kline_block 注入 chars=" in seg
