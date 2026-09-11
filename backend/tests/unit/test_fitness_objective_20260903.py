# -*- coding: utf-8 -*-
"""[P3.2 2026-09-03] GP/MCTS 统一含成本净收益目标（fitness_objective）。

覆盖：
1. net_edge_after_cost 的口径：sign 仓位、非重叠 h 根、cost·turn/2、方向取优、分段不跨币；
2. segment_icir 与 GPU 路径 FIX-1 语义一致（<2 段/std 退化 → 回退）；
3. blend_objective 五种 objective 的行为与可回滚性；
4. 三条挖矿路径（GP-CPU / GP-GPU 组装 / MCTS）对同一因子给出同一目标；
5. factor_evolution_loop 的面板携带真实前瞻收益，miner 参数组装正确。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pandas as pd
import pytest

from backend.services.evolution import fitness_objective as fo


# ─────────────────────────── 工具 ───────────────────────────

def _returns(n: int, seed: int, sigma: float = 0.004) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0, sigma, n)


# ─────────────────────────── 1. net_edge_after_cost ───────────────────────────

class TestNetEdge:
    def test_perfect_foresight_positive_gross_and_orient(self):
        r = _returns(600, 1)
        edge = fo.net_edge_after_cost(r, r, horizon=1, cost=0.0009)
        assert edge["n"] == 600
        assert edge["orient"] == 1
        # 完美预知：每步毛收益 = mean|r|（sign(r−med)·r ≈ |r|）
        assert edge["gross"] == pytest.approx(float(np.mean(np.abs(r - np.median(r)) * np.sign(r - np.median(r)) * np.sign(r))), rel=0.2)
        assert edge["gross"] > 0.002
        assert edge["net"] == pytest.approx(edge["gross"] - 0.0009 * edge["turnover"])

    def test_reversed_factor_gets_same_gross_via_orientation(self):
        r = _returns(600, 2)
        a = fo.net_edge_after_cost(r, r, horizon=1, cost=0.0009)
        b = fo.net_edge_after_cost(-r, r, horizon=1, cost=0.0009)
        assert a["gross"] == pytest.approx(b["gross"])
        assert a["turnover"] == pytest.approx(b["turnover"])
        assert b["orient"] == -1

    def test_alternating_signal_pays_full_cost(self):
        """逐步翻转、毫无预测力的因子：换手≈1/步，净收益≈−cost。"""
        n = 800
        r = _returns(n, 3)
        fv = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
        edge = fo.net_edge_after_cost(fv, r, horizon=1, cost=0.0009)
        assert edge["turnover"] == pytest.approx(1.0, abs=0.01)
        assert edge["gross"] < 0.0005
        assert edge["net"] < 0

    def test_persistent_signal_has_near_zero_turnover(self):
        n = 400
        r = _returns(n, 4)
        fv = np.linspace(0, 1, n)  # 单调：前半段 −1、后半段 +1，只翻转一次
        edge = fo.net_edge_after_cost(fv, r, horizon=1, cost=0.0009)
        # 首步 0→−1 记 0.5，中点 −1→+1 记 1.0 → 共 1.5/400
        assert edge["turnover"] == pytest.approx(1.5 / n, abs=1e-9)

    def test_horizon_phases_use_every_sample_once(self):
        n = 601
        r = _returns(n, 5)
        fv = _returns(n, 6)
        for h in (1, 3, 12):
            edge = fo.net_edge_after_cost(fv, r, horizon=h, cost=0.0009)
            assert edge["n"] == n, f"h={h} 应用满全部有效样本"

    def test_nan_samples_excluded(self):
        n = 500
        r = _returns(n, 7)
        fv = _returns(n, 8)
        fv[:40] = np.nan   # 预热期
        r[-12:] = np.nan   # 尾部无未来
        edge = fo.net_edge_after_cost(fv, r, horizon=4, cost=0.0009)
        assert edge["n"] == n - 40 - 12

    def test_segments_do_not_carry_position_across_symbols(self):
        """两币拼接：第一币末尾 +1、第二币开头 −1。分段后边界不算一次翻转（各段从空仓起）。"""
        n = 300
        r = np.concatenate([_returns(n, 9), _returns(n, 10)])
        fv = np.concatenate([np.ones(n), -np.ones(n)])
        # 不分段：中点 +1→−1 一次翻转(1.0) + 首步 0.5 = 1.5
        flat = fo.net_edge_after_cost(fv, r, horizon=1, cost=0.0009, lens=None)
        # 分段：两段各首步 0.5 = 1.0
        seg = fo.net_edge_after_cost(fv, r, horizon=1, cost=0.0009, lens=[n, n])
        assert flat["turnover"] == pytest.approx(1.5 / (2 * n), abs=1e-12)
        assert seg["turnover"] == pytest.approx(1.0 / (2 * n), abs=1e-12)
        assert seg["n"] == flat["n"] == 2 * n

    def test_bad_lens_falls_back_to_single_segment(self):
        assert fo.segment_bounds([100, 50], 300) == [(0, 300)]
        assert fo.segment_bounds([100, 200], 300) == [(0, 100), (100, 300)]
        assert fo.segment_bounds(None, 10) == [(0, 10)]
        assert fo.segment_bounds([5, 5], 0) == []

    def test_insufficient_samples_returns_nan(self):
        r = _returns(30, 11)
        edge = fo.net_edge_after_cost(r, r, horizon=1, cost=0.0009, min_samples=50)
        assert edge["n"] == 0 and np.isnan(edge["net"])

    def test_shape_mismatch_returns_nan(self):
        edge = fo.net_edge_after_cost(np.ones(10), np.ones(11), horizon=1, cost=0.0009)
        assert np.isnan(edge["net"])

    def test_matches_scorer_accounting_convention(self):
        """与 FactorBacktestScorer._walk_forward_backtest 同款：非重叠 h 根 + cost·turn/2。

        手工构造 h=2：相位 0 取偶数位、相位 1 取奇数位，各自独立计仓位与换手。
        """
        fv = np.array([1, -1, 1, -1, 1, -1, 1, -1], dtype=float) * 10 + 0.5
        r = np.array([0.01, 0.02, 0.01, 0.02, 0.01, 0.02, 0.01, 0.02])
        # 相位 0（idx 0,2,4,6）：因子全 +，仓位恒 +1 → 毛 4×0.01，换手 0.5
        # 相位 1（idx 1,3,5,7）：因子全 −，仓位恒 −1 → 毛 −4×0.02，换手 0.5
        edge = fo.net_edge_after_cost(fv, r, horizon=2, cost=0.01, min_samples=1)
        gross_signed = (4 * 0.01 - 4 * 0.02) / 8
        assert edge["orient"] == -1
        assert edge["gross"] == pytest.approx(abs(gross_signed))
        assert edge["turnover"] == pytest.approx(1.0 / 8)
        assert edge["net"] == pytest.approx(abs(gross_signed) - 0.01 * (1.0 / 8))


# ─────────────────────────── 2. segment_icir ───────────────────────────

class TestSegmentIcir:
    def _panel(self):
        B = 300
        t0, t1, t2 = _returns(B, 20), _returns(B, 21), _returns(B, 22)
        rng = np.random.default_rng(23)
        fv = np.concatenate([t0 + rng.normal(0, 0.006, B),
                             t1 + rng.normal(0, 0.006, B),
                             t2 + rng.normal(0, 0.006, B)])
        target = np.concatenate([t0, t1, t2])
        return fv, target, [B, B, B]

    def test_multi_segment_gives_icir(self):
        fv, target, lens = self._panel()
        mask = np.isfinite(fv) & np.isfinite(target)
        ic = abs(float(np.corrcoef(fv[mask], target[mask])[0, 1]))
        icir = fo.segment_icir(fv, target, mask, lens, fallback=ic)
        assert np.isfinite(icir) and icir != ic and icir > 0

    def test_single_valid_segment_falls_back(self):
        fv, target, lens = self._panel()
        fv[300:] = np.nan
        mask = np.isfinite(fv) & np.isfinite(target)
        assert fo.segment_icir(fv, target, mask, lens, fallback=0.123) == pytest.approx(0.123)

    def test_no_lens_falls_back(self):
        fv, target, _ = self._panel()
        mask = np.isfinite(fv)
        assert fo.segment_icir(fv, target, mask, None, fallback=0.5) == 0.5

    def test_degenerate_std_falls_back(self):
        """三段 IC 完全相同 → std=0 → 回退（防 mean/1e-10 爆炸）。"""
        B = 300
        t = _returns(B, 24)
        fv = np.concatenate([t, t, t])
        target = np.concatenate([t, t, t])
        mask = np.isfinite(fv)
        assert fo.segment_icir(fv, target, mask, [B, B, B], fallback=1.0) == 1.0


# ─────────────────────────── 3. blend_objective ───────────────────────────

class TestBlendObjective:
    def _ctx(self, objective, r, **kw):
        return fo.net_context(objective=objective, fwd_ret=r, horizon=1, lens=None,
                              cost=0.0009, weight=0.5, prescreen=0.005, **kw)

    def test_legacy_objectives_untouched(self):
        r = _returns(400, 30)
        for obj in ("ic", "icir"):
            ctx = self._ctx(obj, r)
            assert set(ctx.keys()) == {"objective"}
            assert fo.blend_objective(0.7, 0.05, r, ctx) == 0.7

    def test_default_objective_and_env_override(self, monkeypatch):
        monkeypatch.delenv("FACTOR_GP_OBJECTIVE", raising=False)
        assert fo.default_objective() == "icir_net"
        monkeypatch.setenv("FACTOR_GP_OBJECTIVE", "icir")
        assert fo.default_objective() == "icir"
        monkeypatch.setenv("FACTOR_GP_OBJECTIVE", "garbage")
        assert fo.default_objective() == "icir_net"

    def test_icir_net_adds_weighted_ratio(self):
        r = _returns(400, 31)
        ctx = self._ctx("icir_net", r)
        edge = fo.net_edge_after_cost(r, r, horizon=1, cost=0.0009)
        expected = 0.7 + 0.5 * fo.net_ratio(edge["net"], 0.0009)
        assert fo.blend_objective(0.7, 0.05, r, ctx) == pytest.approx(expected)
        assert fo.blend_objective(0.7, 0.05, r, ctx) > 0.7  # 完美预知：净收益为正 → 目标升高

    def test_ic_net_uses_ic_as_base(self):
        r = _returns(400, 32)
        ctx = self._ctx("ic_net", r)
        edge = fo.net_edge_after_cost(r, r, horizon=1, cost=0.0009)
        assert fo.blend_objective(0.05, 0.05, r, ctx) == pytest.approx(
            0.05 + 0.5 * fo.net_ratio(edge["net"], 0.0009))

    def test_costly_flipper_is_penalised(self):
        """逐步翻转因子：净收益≈−cost → ratio≈−1 → 目标被扣 w。"""
        n = 800
        r = _returns(n, 33)
        fv = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
        ctx = self._ctx("icir_net", r)
        out = fo.blend_objective(1.0, 0.03, fv, ctx)
        assert out < 1.0 - 0.5 * 0.8

    def test_net_objective_prescreens_on_ic(self):
        r = _returns(400, 34)
        ctx = self._ctx("net", r)
        assert fo.blend_objective(0.7, 0.001, r, ctx) == float("-inf")
        out = fo.blend_objective(0.7, 0.05, r, ctx)
        edge = fo.net_edge_after_cost(r, r, horizon=1, cost=0.0009)
        assert out == pytest.approx(fo.net_ratio(edge["net"], 0.0009))

    def test_ratio_is_clipped(self):
        assert fo.net_ratio(1.0, 0.0009) == fo.NET_RATIO_CLIP
        assert fo.net_ratio(-1.0, 0.0009) == -fo.NET_RATIO_CLIP
        assert np.isnan(fo.net_ratio(float("nan"), 0.0009))

    def test_missing_fwd_ret_disables_net_term(self):
        ctx = fo.net_context(objective="icir_net", fwd_ret=None, horizon=1)
        assert "fwd_ret" not in ctx
        assert fo.blend_objective(0.4, 0.05, np.ones(10), ctx) == 0.4

    def test_insufficient_samples_keeps_base(self):
        r = _returns(20, 35)
        ctx = self._ctx("icir_net", r)
        assert fo.blend_objective(0.4, 0.05, r, ctx, min_samples=50) == 0.4

    def test_mining_cost_follows_scorer_setting(self, monkeypatch):
        monkeypatch.delenv("FACTOR_MINE_COST", raising=False)
        from backend.config import settings as _s
        monkeypatch.setattr(_s, "FACTOR_SCORER_COST", 0.0021, raising=False)
        assert fo.mining_cost() == pytest.approx(0.0021)
        monkeypatch.setenv("FACTOR_MINE_COST", "0.0005")
        assert fo.mining_cost() == pytest.approx(0.0005)


# ─────────────────────────── 4. 三条挖矿路径同口径 ───────────────────────────

def _three_symbol_panel(B=400):
    """三币面板：因子 = 前瞻收益 + 噪声（有真实边际）。"""
    rng = np.random.default_rng(40)
    rets = [_returns(B, 41 + i) for i in range(3)]
    fv = np.concatenate([r + rng.normal(0, 0.008, B) for r in rets])
    target = np.concatenate(rets)
    return fv, target, [B, B, B]


class TestThreePathsAgree:
    def test_gp_cpu_and_mcts_and_gpu_assembly_agree(self):
        from backend.services.evolution.gp_miner import _fitness_core
        from backend.services.evolution.mcts_miner import _mcts_fitness_core
        from backend.services.evolution.gp_gpu_eval import compute_fitness_from_values

        fv, target, lens = _three_symbol_panel()
        ctx = fo.net_context(objective="icir_net", fwd_ret=target, horizon=3, lens=lens,
                             cost=0.0009, weight=0.5)
        base_state = {
            "factor_value_fn": lambda c: fv,
            "target": target,
            "min_samples": 50,
            "lambda_complexity": 0.0,
            "lambda_corr": 0.0,
            "lambda_turnover": 0.0,
            "objective": "icir_net",
            "lens": lens,
            "net_ctx": ctx,
            "elite_ast": [],
            "elite_fvs": [],
            "root_asts": [],
        }
        ast = {"f": "close"}
        f_cpu = _fitness_core(ast, base_state)
        ic_m, f_mcts = _mcts_fitness_core(ast, base_state)
        fits, _ = compute_fitness_from_values(
            fv[None, :], [ast], target, min_samples=50, lam_c=0.0, lam_corr=0.0,
            elite_fvs=[], node_counts=[1], lens=lens, objective="icir_net", net_ctx=ctx,
        )
        assert np.isfinite(f_cpu)
        assert f_cpu == pytest.approx(f_mcts, rel=1e-9)
        assert f_cpu == pytest.approx(fits[0], rel=1e-9)
        # 手工复算：ICIR + w·ratio
        mask = np.isfinite(fv) & np.isfinite(target)
        ic = abs(float(np.corrcoef(fv[mask], target[mask])[0, 1]))
        icir = fo.segment_icir(fv, target, mask, lens, fallback=ic)
        edge = fo.net_edge_after_cost(fv, target, horizon=3, cost=0.0009, lens=lens)
        assert f_cpu == pytest.approx(icir + 0.5 * fo.net_ratio(edge["net"], 0.0009), rel=1e-9)
        assert abs(ic_m) == pytest.approx(ic)

    def test_cpu_icir_no_longer_needs_gpu_ctx(self):
        """GPMiner 直接携带 lens → 无 GPU 上下文时 icir 也按币段计算（此前静默退化为 |IC|）。"""
        from backend.services.evolution.alpha_miner import AlphaPool
        from backend.services.evolution.gp_miner import GPConfig, GPMiner, _fitness_core

        fv, target, lens = _three_symbol_panel()
        cfg = GPConfig(objective="icir")
        miner = GPMiner(["close"], lambda c: fv, target, AlphaPool(capacity=5), cfg,
                        lens=lens, fwd_ret=target, horizon=3)
        state = miner._fitness_state()
        assert state["lens"] == lens
        assert state["net_ctx"] == {"objective": "icir"}  # icir 不带净收益项
        f_seg = _fitness_core({"f": "close"}, state)
        state_nolens = dict(state, lens=None)
        f_flat = _fitness_core({"f": "close"}, state_nolens)
        assert f_seg != pytest.approx(f_flat), "有 lens 时应按币段 ICIR，而不是全面板 |IC|"

    def test_gpminer_default_state_carries_net_ctx(self):
        from backend.services.evolution.alpha_miner import AlphaPool
        from backend.services.evolution.gp_miner import GPConfig, GPMiner

        fv, target, lens = _three_symbol_panel()
        cfg = GPConfig()
        assert cfg.objective == "icir_net"
        miner = GPMiner(["close"], lambda c: fv, target, AlphaPool(capacity=5), cfg,
                        lens=lens, fwd_ret=target, horizon=3)
        ctx = miner._fitness_state()["net_ctx"]
        assert ctx["objective"] == "icir_net"
        assert ctx["horizon"] == 3 and ctx["lens"] == lens
        assert ctx["fwd_ret"].shape == target.shape
        assert ctx["cost"] == pytest.approx(fo.mining_cost())

    def test_gpminer_shape_mismatch_disables_net_term(self):
        from backend.services.evolution.alpha_miner import AlphaPool
        from backend.services.evolution.gp_miner import GPConfig, GPMiner

        fv, target, lens = _three_symbol_panel()
        miner = GPMiner(["close"], lambda c: fv, target, AlphaPool(capacity=5), GPConfig(),
                        lens=lens, fwd_ret=target[:-1], horizon=3)
        assert miner.fwd_ret is None
        assert "fwd_ret" not in miner._fitness_state()["net_ctx"]

    def test_mcts_state_carries_objective_and_net_ctx(self):
        from backend.services.evolution.alpha_miner import AlphaPool
        from backend.services.evolution.mcts_miner import MCTSConfig, MctsMiner

        fv, target, lens = _three_symbol_panel()
        cfg = MCTSConfig(scale="mid")
        assert cfg.objective == "icir_net"
        miner = MctsMiner(["close"], lambda c: fv, target, AlphaPool(capacity=5), cfg,
                          lens=lens, fwd_ret=target, horizon=3)
        st = miner._fitness_state()
        assert st["objective"] == "icir_net" and st["lens"] == lens
        assert st["net_ctx"]["fwd_ret"].shape == target.shape

    def test_net_term_reorders_high_ic_flipper_below_steady_factor(self):
        """核心动机：IC 略高但逐步翻转的因子，扣费后应排在 IC 略低但换手极低的因子之后。"""
        from backend.services.evolution.gp_miner import _fitness_core

        n = 2000
        r = _returns(n, 51, sigma=0.003)
        t = np.arange(n)
        # 稠密翻转因子：sign 逐步交替（换手≈1/步），叠加一点真实信息（|IC|≈0.1，现实量级）
        flip = np.where(t % 2 == 0, 1.0, -1.0) * 0.002 + r * 0.08
        # 平稳因子：慢方波（每 200 根翻一次，换手≈0.005/步），信息更弱（|IC|≈0.03）
        slow = np.sign(np.sin(2 * np.pi * t / 400.0)) * 0.002 + r * 0.02
        target = r

        def _state(objective, fv):
            return {
                "factor_value_fn": lambda c: fv, "target": target, "min_samples": 50,
                "lambda_complexity": 0.0, "lambda_corr": 0.0, "lambda_turnover": 0.0,
                "objective": objective, "lens": None,
                "net_ctx": fo.net_context(objective=objective, fwd_ret=target, horizon=1,
                                          cost=0.0009, weight=0.5),
                "elite_ast": [], "elite_fvs": [],
            }

        e_flip = fo.net_edge_after_cost(flip, target, horizon=1, cost=0.0009)
        e_slow = fo.net_edge_after_cost(slow, target, horizon=1, cost=0.0009)
        assert e_flip["turnover"] > 0.6 > e_slow["turnover"]
        f_flip_net = _fitness_core({"f": "close"}, _state("ic_net", flip))
        f_slow_net = _fitness_core({"f": "close"}, _state("ic_net", slow))
        f_flip_ic = _fitness_core({"f": "close"}, _state("ic", flip))
        f_slow_ic = _fitness_core({"f": "close"}, _state("ic", slow))
        assert f_flip_ic > f_slow_ic, "构造前提：翻转因子的原始 |IC| 更高"
        assert f_flip_net < f_slow_net, "含成本目标下，逐步翻转的高 IC 因子应被压到低换手因子之下"


# ─────────────────────────── 5. 进化环面板接线 ───────────────────────────

class TestEvolutionLoopWiring:
    def _dfs(self, n=300):
        out = {}
        for i, s in enumerate(("BTC", "ETH")):
            rng = np.random.default_rng(60 + i)
            close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
            idx = pd.date_range("2026-01-01", periods=n, freq="5min")
            out[s] = pd.DataFrame({
                "open": close, "high": close * 1.001, "low": close * 0.999,
                "close": close, "volume": rng.uniform(1, 2, n),
            }, index=idx)
        return out

    def test_forward_returns_raw_bypasses_labels(self, monkeypatch):
        import backend.services.evolution.factor_evolution_loop as evo
        df = self._dfs()["BTC"]
        raw = evo._forward_returns(df, horizon=5, raw=True)
        close = df["close"].to_numpy()
        assert np.isnan(raw[-5:]).all()
        assert raw[0] == pytest.approx(close[5] / close[0] - 1.0)

    def test_panel_carries_fwd_raw_and_kwargs_align(self, monkeypatch):
        import backend.services.evolution.factor_evolution_loop as evo
        dfs = self._dfs()
        _eval_fn, target, field_names, panel = evo._stack_mine_panel(dfs, ["BTC", "ETH"])
        assert len(panel) == 3
        field_dicts, lens, fwd_raw = panel
        assert lens == [300, 300]
        assert fwd_raw.shape == target.shape
        kw = evo._miner_panel_kwargs(panel, "5m")
        assert kw["lens"] == [300, 300]
        assert kw["fwd_ret"].shape == target.shape
        assert kw["horizon"] == evo._fwd_bars_for_period("5m")

    def test_kwargs_degrade_gracefully(self):
        import backend.services.evolution.factor_evolution_loop as evo
        assert evo._miner_panel_kwargs(None) == {}
        kw = evo._miner_panel_kwargs(([], [100, 100], np.zeros(150)), "4h")
        assert kw["lens"] == [100, 100] and "fwd_ret" not in kw  # 长度不齐 → 不带净收益

    def test_mine_candidates_wires_objective_and_panel(self):
        """源码级：GP 与 MCTS 都从 fitness_objective 取 objective，且都拿到面板 kwargs。"""
        import inspect
        import backend.services.evolution.factor_evolution_loop as evo
        src = inspect.getsource(evo._mine_candidates)
        assert "gp_config.objective = _default_objective()" in src
        assert "mcts_config.objective = _mcts_default_objective()" in src
        assert src.count("_miner_panel_kwargs(_panel, period)") >= 2
