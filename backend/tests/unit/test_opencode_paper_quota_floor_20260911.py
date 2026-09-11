# -*- coding: utf-8 -*-
"""[2026-09-11 F38t] paper 日配额地板契约测试。

现场：opencode #632（training_auto_apply_major 免审直通）把 max_daily_trades
4.0→3.2（-20% 恰在步长内），int→3 后日开 3 笔封顶，掐断 paper 样本管线。
用户定调「模拟盘=收集数据、不做亏损冻结」。

契约（两层防线）：
- 中央钳制：schema max_daily_trades.min = 10，apply_patches 把所有写入路径的
  低于 10 的值钳到 10（governor reconcile / 前端 / 直接 patch 同源）。
- 入口拒绝：validate_patches_hard 增加 schema [min,max] 边界硬校验——
  提案越界即 reject（含 training_auto_apply_major 免审路径，它同样先过硬校验）。
"""
import pytest

import backend.services.runtime_tuning_store as tuning_store
from backend.services import opencode_proposal_reviewer as reviewer


def _patch(key, value, ptype="tuning"):
    return [{"type": ptype, "key": key, "value": value}]


def test_schema_min_max_daily_trades_is_10():
    """中央钳制：schema 下限必须是 10（paper 样本管线地板）。"""
    sch = tuning_store._DEFAULT_SCHEMA["max_daily_trades"]
    assert sch["min"] == 10, sch
    assert sch["max"] == 20, sch


def test_apply_patches_clamps_below_floor_to_10(monkeypatch, tmp_path):
    """任何写入路径（含 governor reconcile 的 apply_patches）都不能把
    max_daily_trades 写到 10 以下——3.2 必须被钳到 10.0。"""
    monkeypatch.setattr(tuning_store, "TUNING_FILE", str(tmp_path / "runtime_tuning.json"))
    applied = tuning_store.apply_patches({"max_daily_trades": 3.2})
    assert float(applied["max_daily_trades"]) == 10.0, applied


def test_reviewer_rejects_below_schema_min_when_delta_would_pass(monkeypatch):
    """入口拒绝：复现 #632 场景——当前值 4.0、提案 3.2（-20% 恰在步长内）。
    旧校验会放行（正是历史事故）；新校验必须因 schema min=10 拒绝。"""
    monkeypatch.setattr(reviewer, "_current_tuning_value", lambda key: 4.0)
    ok, errors = reviewer.validate_patches_hard(_patch("max_daily_trades", 3.2))
    assert ok is False, errors
    assert any("below schema min" in e for e in errors), errors


def test_reviewer_accepts_value_at_floor_within_delta(monkeypatch):
    """当前 12、提案 10：-16.7% 在步长内、≥ 地板 → 通过。"""
    monkeypatch.setattr(reviewer, "_current_tuning_value", lambda key: 12.0)
    ok, errors = reviewer.validate_patches_hard(_patch("max_daily_trades", 10))
    assert ok is True, errors


def test_reviewer_rejects_above_schema_max(monkeypatch):
    """schema 上限同样硬校验（当前值未知时也不能越界）。"""
    monkeypatch.setattr(reviewer, "_current_tuning_value", lambda key: None)
    ok, errors = reviewer.validate_patches_hard(_patch("max_daily_trades", 25))
    assert ok is False, errors
    assert any("above schema max" in e for e in errors), errors


def test_reviewer_floor_covers_other_bounded_keys(monkeypatch):
    """通用边界校验同样保护 maturity 系列（warmup relief min=5）。"""
    monkeypatch.setattr(reviewer, "_current_tuning_value", lambda key: None)
    ok, errors = reviewer.validate_patches_hard(_patch("maturity_max_warmup_relief", 1))
    assert ok is False, errors
    assert any("below schema min" in e for e in errors), errors
