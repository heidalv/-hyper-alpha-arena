# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R17] 归零因子前向影子单测。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.services import factor_zero_shadow as Z


@pytest.fixture()
def shadow_file(tmp_path, monkeypatch):
    p = tmp_path / "factor_zero_shadow.jsonl"
    monkeypatch.setattr(Z, "_PATH", p)
    monkeypatch.setenv("FACTOR_ZERO_SHADOW_ENABLED", "true")
    return p


def _sigs(mapping):
    return {k: SimpleNamespace(direction=v) for k, v in mapping.items()}


def test_records_only_zero_weight_with_direction(shadow_file):
    Z.record("BTC", {"a": 1.0, "b": 0.0, "c": 0.0, "d": 0.5},
             _sigs({"a": 0.8, "b": -0.6, "c": 0.0, "d": 0.3}), regime="up")
    row = json.loads(shadow_file.read_text(encoding="utf-8").strip())
    assert row["sym"] == "BTC" and row["regime"] == "up"
    assert row["zeros"] == {"b": -0.6}, "只记 w=0 且方向非零的因子"
    assert row["n_zero"] == 1


def test_disabled_by_default(shadow_file, monkeypatch):
    monkeypatch.setenv("FACTOR_ZERO_SHADOW_ENABLED", "false")
    Z.record("BTC", {"b": 0.0}, _sigs({"b": -0.6}))
    assert not shadow_file.exists()


def test_no_zero_weights_no_write(shadow_file):
    Z.record("BTC", {"a": 1.0}, _sigs({"a": 0.8}))
    assert not shadow_file.exists()


def test_does_not_raise_on_bad_input(shadow_file):
    Z.record("BTC", {"b": "oops"}, _sigs({"b": -0.6}))
    Z.record(None, None, None)
    Z.record("BTC", {"b": 0.0}, {"b": SimpleNamespace(direction=None)})
    # 不应抛异常；至多没写行


def test_stats_reads_back(shadow_file):
    Z.record("BTC", {"b": 0.0}, _sigs({"b": -0.6}))
    Z.record("ETH", {"b": 0.0, "c": 0.0}, _sigs({"b": 0.4, "c": -0.2}))
    s = Z.stats()
    assert s["enabled"] is True and s["lines"] == 2
    assert s["distinct_factors"] == 2
