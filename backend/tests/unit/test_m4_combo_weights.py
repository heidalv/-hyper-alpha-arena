# -*- coding: utf-8 -*-
"""升级 v3.0 S3/M4 单测：ICIR 加权组合权重解析。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine.combo_weights import resolve_combo_weights


def test_icir_mode_normalized():
    """[2026-08-26 语义契约同步] 无 expected_sign 时按 sign(icir) 锁定方向：
    正 ICIR 因子按幅度加权；负 ICIR 因子（无符号锁定）= 反向因子，获得
    |icir| 权重（路由按锁定符号反手使用），不再是旧语义的 0 权重。
    显式 expected_sign 与 icir 符号冲突 → 0 权重（不信任）。"""
    records = [
        {"factor_id": "a", "scores": {"icir": 0.8}},
        {"factor_id": "b", "scores": {"icir": 0.2}},
        {"factor_id": "c", "scores": {"icir": -0.5}},          # 反向因子，合法获权
        {"factor_id": "d", "scores": {"icir": -0.5, "expected_sign": 1}},  # 符号冲突 → 0
    ]
    w = resolve_combo_weights(records, {})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["a"] > w["b"] > 0.0
    assert abs(w["a"] - 0.8 / 1.5) < 1e-9          # base: 0.8+0.2+0.5
    assert abs(w["b"] - 0.2 / 1.5) < 1e-9
    assert abs(w["c"] - 0.5 / 1.5) < 1e-9          # 反向因子权重=|icir|占比
    assert w["d"] == 0.0                            # 符号冲突不信任


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


def test_all_zero_icir_returns_all_zero_not_uniform():
    """[2026-09-02 契约变更] 全零权重不再回退均权，而是如实返回全零。

    旧契约（回退 1/n 均权）是一条 fail-open：上一步刚被负 IC / 符号冲突判定为
    不可信而归零的因子，会在这里原样拿回等权，与归零的设计意图正好相反——
    因子体系整体失效时系统反而按等权继续出信号。现在全零如实传给下游，
    midlong_factor_route 的 weight_sum<=0 分支给出 no_valid_votes 跳过本轮。
    """
    records = [{"factor_id": "a", "scores": {"icir": 0.0}}, {"factor_id": "b", "scores": {}}]
    w = resolve_combo_weights(records, {})
    assert w == {"a": 0.0, "b": 0.0}
    assert sum(w.values()) == 0.0
