# -*- coding: utf-8 -*-
"""跨轮假设登记簿（DSR 分母，2026-09-03 审查修正 C）。

锁定三条口径：不棘轮（重复评估不加计）、不漏算（跨轮不同假设累加）、
会过期（验证窗滑过去以后不再计入）；以及进化主链取 max(本轮, 滚动) 的接线。
"""
import os

import pytest

from backend.services.factor_engine import trials_registry as tr


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTOR_TRIALS_REGISTRY_PATH", str(tmp_path / "reg.json"))
    tr.reset()
    yield tr
    tr.reset()


class TestRegistrySemantics:
    def test_repeat_evaluation_does_not_ratchet(self, isolated_registry):
        t0 = 1_000_000.0
        tr.register_evaluated("5m", ["a", "b", "c"], now=t0)
        tr.register_evaluated("5m", ["a", "b", "c"], now=t0 + 3600)
        tr.register_evaluated("5m", ["a"], now=t0 + 7200)
        assert tr.distinct_recent("5m", window_days=10, now=t0 + 7200) == 3

    def test_cross_round_accumulates(self, isolated_registry):
        t0 = 1_000_000.0
        for day in range(5):  # 连续 5 轮、每轮 20 个新假设
            tr.register_evaluated("5m", [f"r{day}_{i}" for i in range(20)], now=t0 + day * 86400)
        assert tr.distinct_recent("5m", window_days=10, now=t0 + 4 * 86400) == 100

    def test_entries_expire_with_window(self, isolated_registry):
        t0 = 1_000_000.0
        tr.register_evaluated("5m", ["old1", "old2"], now=t0)
        tr.register_evaluated("5m", ["new1"], now=t0 + 12 * 86400)
        # 10 天窗：老的两条已滑出
        assert tr.distinct_recent("5m", window_days=10, now=t0 + 12 * 86400) == 1
        # 20 天窗：都还在
        assert tr.distinct_recent("5m", window_days=20, now=t0 + 12 * 86400) == 3

    def test_periods_are_isolated(self, isolated_registry):
        t0 = 1_000_000.0
        tr.register_evaluated("5m", ["x", "y"], now=t0)
        tr.register_evaluated("4h", ["x"], now=t0)
        assert tr.distinct_recent("5m", 10, now=t0) == 2
        assert tr.distinct_recent("4h", 10, now=t0) == 1

    def test_persists_across_reload(self, isolated_registry, tmp_path):
        t0 = 1_000_000.0
        tr.register_evaluated("5m", ["p", "q"], now=t0)
        assert os.path.exists(str(tmp_path / "reg.json"))
        tr._state = None  # 模拟进程重启
        assert tr.distinct_recent("5m", 10, now=t0) == 2

    def test_register_and_count(self, isolated_registry):
        t0 = 1_000_000.0
        assert tr.register_and_count("15m", ["a", "b"], window_days=15, now=t0) == 2
        assert tr.register_and_count("15m", ["b", "c"], window_days=15, now=t0 + 60) == 3

    def test_prune_caps_size(self, isolated_registry, monkeypatch):
        monkeypatch.setattr(tr, "_MAX_IDS_PER_PERIOD", 50)
        t0 = 1_000_000.0
        tr.register_evaluated("5m", [f"h{i}" for i in range(120)], now=t0)
        assert len(tr._load()["5m"]) == 50


class TestEvolutionLoopWiring:
    def test_promote_uses_rolling_when_larger(self, isolated_registry, monkeypatch):
        """_promote_factors：非冷启动时 n_trials = max(本轮, 近验证窗天数内不同假设数)。"""
        from backend.services.evolution import factor_evolution_loop as fel

        t0 = 1_000_000.0
        # 前几轮已登记 300 个不同假设
        tr.register_evaluated("4h", [f"prev{i}" for i in range(300)], now=t0)

        captured = {}

        def fake_dsr(icir_list, n_total_candidates, sample_len, ic_series=None):
            captured["n"] = n_total_candidates
            return {"dsr_result": {"significant": False}, "pbo_result": {"pbo": 1.0}}

        monkeypatch.setattr(
            "backend.services.factor_engine.dsr_pbo.compute_dsr_pbo_for_factors", fake_dsr)
        # 非冷启动：TRADABLE 非空
        monkeypatch.setattr(
            "backend.services.factor_engine.active_set_policy.load_factor_active_rows",
            lambda *a, **k: [{"factor_id": "x"}])
        monkeypatch.setattr(tr.time, "time", lambda: t0 + 60)

        eval_results = {f"cur{i}": {"avg_icir": 0.1} for i in range(10)}
        out = fel._promote_factors(
            survivors=[], eval_results=eval_results, all_icir_values=[0.1] * 10,
            n_total=10, dfs={"BTC": None}, period="4h",
        )
        assert out == []
        assert captured["n"] == 310, f"本轮 10 + 前几轮 300 = 310，实际 {captured.get('n')}"

    def test_cold_start_exception_preserved(self, isolated_registry, monkeypatch):
        """TRADABLE 为空的冷启动仍按 survivors 数做分母（08-27 防自杀口径不变）。"""
        from backend.services.evolution import factor_evolution_loop as fel

        t0 = 1_000_000.0
        tr.register_evaluated("4h", [f"prev{i}" for i in range(300)], now=t0)
        captured = {}

        def fake_dsr(icir_list, n_total_candidates, sample_len, ic_series=None):
            captured["n"] = n_total_candidates
            return {"dsr_result": {"significant": False}, "pbo_result": {"pbo": 1.0}}

        monkeypatch.setattr(
            "backend.services.factor_engine.dsr_pbo.compute_dsr_pbo_for_factors", fake_dsr)
        monkeypatch.setattr(
            "backend.services.factor_engine.active_set_policy.load_factor_active_rows",
            lambda *a, **k: [])
        monkeypatch.setattr(tr.time, "time", lambda: t0 + 60)
        # survivors 需要走到 net_ic 判定前就返回 → 给 eval_results 缺 net_ic 的 survivor，
        # net_ic 默认 1.0 通过，后续 ShadowJudge 等会因缺数据抛异常/拒绝；这里只关心 captured
        survivors = [{"factor_id": "s1"}, {"factor_id": "s2"}]
        try:
            fel._promote_factors(
                survivors=survivors, eval_results={"s1": {}, "s2": {}},
                all_icir_values=[0.5, 0.6], n_total=2, dfs={"BTC": None}, period="4h",
            )
        except Exception:
            pass
        assert captured["n"] == 2, captured
