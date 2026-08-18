# -*- coding: utf-8 -*-
"""升级 v3.0 S3/M4 单测：ICIR 加权组合权重解析。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine.combo_weights import resolve_combo_weights


def test_icir_mode_normalized():
    records = [
        {"factor_id": "a", "scores": {"icir": 0.8}},
        {"factor_id": "b", "scores": {"icir": 0.2}},
        {"factor_id": "c", "scores": {"icir": -0.5}},  # 负 ICIR → 0 权重
    ]
    w = resolve_combo_weights(records, {})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["a"] > w["b"] > w["c"] == 0.0
    assert abs(w["a"] - 0.8) < 1e-9 and abs(w["b"] - 0.2) < 1e-9


def test_manual_override():
    records = [
        {"factor_id": "a", "scores": {"icir": 0.8}},
        {"factor_id": "b", "scores": {"icir": 0.2}},
    ]
    w = resolve_combo_weights(records, {"a": 0.1})
    assert abs(w["a"] - (0.1 / 0.3)) < 1e-9, "手工覆盖项按覆盖值参与归一"


def test_equal_mode(monkeypatch):
    monkeypatch.setenv("FACTOR_COMBO_MODE", "equal")
    records = [{"factor_id": "a", "scores": {"icir": 0.8}}, {"factor_id": "b", "scores": {"icir": 0.2}}]
    w = resolve_combo_weights(records, {})
    assert w == {"a": 1.0, "b": 1.0}


def test_all_zero_icir_fallback_uniform():
    records = [{"factor_id": "a", "scores": {"icir": 0.0}}, {"factor_id": "b", "scores": {}}]
    w = resolve_combo_weights(records, {})
    assert abs(w["a"] - 0.5) < 1e-9 and abs(w["b"] - 0.5) < 1e-9
