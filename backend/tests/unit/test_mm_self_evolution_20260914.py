# -*- coding: utf-8 -*-
"""[2026-09-14 F88] 自进化闭环契约测试（护栏 fail-closed + 有界候选 + 日志）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import evolution as ev  # noqa: E402


def test_grid_is_bounded_and_contains_incumbent():
    """候选必须包含在位配置，且每维只做有限档位变化（防跳变）。"""
    # 与线上一致的全量当前配置（含 F88b 新增的冻结档/波动缩放维度）
    cur = {"w_base_bp": 6.0, "k_inv": 1.0, "max_one_side_seconds": 900.0,
           "frozen_max_move_bp": 8.0, "ofi_block_threshold": 0.5,
           "frozen_width_bp": 3.0, "frozen_lookback": 240, "k_vol": 0.0}
    grid = ev.candidate_grid(cur)
    assert any(all(c.get(k) == cur[k] for k in cur) for c in grid), "缺在位配置"
    assert 1 < len(grid) <= 40, f"候选数异常: {len(grid)}"
    for c in grid:
        for k in ev.GRID:
            assert c.get(k) in ev.GRID[k], f"{k}={c.get(k)} 不在有界档位内"


def test_guardrail_rejects_insufficient_sample():
    ok, why = ev.should_deploy({"fills": 10, "net_bp": 5.0, "net_usd": 50.0},
                               {"fills": 300, "net_bp": 0.2, "net_usd": 10.0})
    assert not ok and "样本不足" in why


def test_guardrail_rejects_drawdown_breach():
    ok, why = ev.should_deploy(
        {"fills": 300, "net_bp": 1.0, "net_usd": 30.0, "max_dd_pct": 9.9},
        {"fills": 300, "net_bp": 0.2, "net_usd": 10.0, "max_dd_pct": 1.0})
    assert not ok and "回撤超限" in why


def test_guardrail_accepts_improvement():
    ok, why = ev.should_deploy(
        {"fills": 300, "net_bp": 0.30, "net_usd": 20.0, "max_dd_pct": 1.0},
        {"fills": 300, "net_bp": 0.25, "net_usd": 17.0, "max_dd_pct": 1.5})
    assert ok and "通过" in why


def test_guardrail_rejects_marginal_or_negative():
    ok, why = ev.should_deploy(
        {"fills": 300, "net_bp": 0.251, "net_usd": 17.01, "max_dd_pct": 1.0},
        {"fills": 300, "net_bp": 0.250, "net_usd": 17.00, "max_dd_pct": 1.5})
    assert not ok and "改进不足" in why
    ok2, _ = ev.should_deploy(
        {"fills": 300, "net_bp": -0.1, "net_usd": 20.0, "max_dd_pct": 1.0},
        {"fills": 300, "net_bp": 0.1, "net_usd": 5.0, "max_dd_pct": 1.5})
    assert not ok2


def test_auto_evolve_off_by_default(monkeypatch):
    """默认只出提案，不允许自动改注册表（安全默认）。"""
    monkeypatch.delenv("MM_AUTO_EVOLVE", raising=False)
    assert ev.evolve_enabled() is False
    monkeypatch.setenv("MM_AUTO_EVOLVE", "1")
    assert ev.evolve_enabled() is True


def test_journal_is_append_only_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "JOURNAL_PATH", str(tmp_path / "j.jsonl"))
    ev._journal({"a": 1})
    ev._journal({"a": 2})
    rows = ev.read_journal()
    assert [r["a"] for r in rows] == [1, 2]


def test_rollback_no_history_is_noop(tmp_path, monkeypatch):
    """无历史变更时回滚检查是 no-op（fail-safe）。"""
    import types
    fake = types.SimpleNamespace(get_lane=lambda lid: {"meta": {}})
    monkeypatch.setitem(sys.modules, "backend.services.lane_registry", fake)
    res = ev.check_and_rollback("mm_asterdex")
    assert res.get("action") == "none"
