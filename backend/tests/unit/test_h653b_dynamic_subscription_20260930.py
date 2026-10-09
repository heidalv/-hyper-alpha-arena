# -*- coding: utf-8 -*-
"""[h653b] market_flow 动态订阅:diff_new_symbols 并集语义。"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "asterdex_collector",
    ROOT / "backend" / "services" / "market_flow" / "asterdex_collector.py")
m = importlib.util.module_from_spec(_spec)
saved = sys.stdout, sys.stderr
try:
    sys.stdout = sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    _spec.loader.exec_module(m)
finally:
    sys.stdout, sys.stderr = saved

diff_new_symbols = m.diff_new_symbols


def test_new_symbols_only():
    assert diff_new_symbols(["BTC", "ETH"], ["BTC", "ETH", "SUI", "ENA"]) == ["SUI", "ENA"]


def test_union_semantics_never_remove():
    # 解析结果变少也不产出"取消"信号(F332:替换式刷新制造人为缺口)
    assert diff_new_symbols(["BTC", "ETH"], ["BTC"]) == []


def test_dedup_and_case_insensitive():
    out = diff_new_symbols(["btc"], ["BTC", "btc", "SUI"])
    assert out == ["SUI"]


def test_empty_resolved_is_noop():
    assert diff_new_symbols(["BTC"], []) == []
    assert diff_new_symbols([], []) == []
