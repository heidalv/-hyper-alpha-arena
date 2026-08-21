"""收尾批次回归（item11/item14/M8/S7 + GPU argmin 跨设备确定性，2026-08-21）。

- item11 GP 适应度减换手成本（CPU _fitness_core 与 GPU compute_fitness_from_values 同口径）
- item14 AST 进化仓桥接中线（_tradable_ast_bridge + _eval_ast_on_df）
- M8   FACTOR_ROUTE_MIN_ACTIVE_FACTORS 默认 2→3（item14 前置已落地）
- S7   单一决策分 _score_for_trade 贯穿 EV/校准/审计
- 算力刀 ts_argmin/ts_argmax 容差取首（CPU/GPU 跨设备确定性）+ 等价验收脚本存在
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


def _src(*parts):
    return open(os.path.join(os.path.dirname(__file__), "..", "..", *parts), encoding="utf-8").read()


# ── item11：GP 换手惩罚 ─────────────────────────────────
class TestItem11TurnoverPenalty:
    def test_flip_rate(self):
        from backend.services.evolution.gp_miner import _turnover_flip_rate
        assert _turnover_flip_rate(np.ones(50)) == pytest.approx(0.0)
        assert _turnover_flip_rate(np.where(np.arange(50) % 2 == 0, 1.0, -1.0)) == pytest.approx(2.0)
        assert _turnover_flip_rate(np.linspace(1, 2, 50)) == pytest.approx(0.0)  # 同号
        assert _turnover_flip_rate(np.array([1.0, np.nan, -1.0, 1.0])) == pytest.approx(2.0)

    def test_gpu_fitness_penalizes_flipper(self):
        from backend.services.evolution.gp_gpu_eval import compute_fitness_from_values
        rng = np.random.default_rng(3)
        t = rng.normal(0, 0.01, 300)
        smooth = np.sign(np.cumsum(rng.normal(0, 1, 300))) * 1.0
        smooth[:50] = 1.0  # 低换手段
        flipper = np.where(np.arange(300) % 2 == 0, 1.0, -1.0)
        asts = [{"op": "f", "args": []}, {"op": "f", "args": []}]
        vals = np.stack([smooth, flipper])
        fits, _ = compute_fitness_from_values(
            vals, asts, t, min_samples=50,
            lam_c=0.0, lam_corr=0.0, elite_fvs=[], lam_to=0.01, lens=[300],
        )
        assert fits[0] > fits[1], "高换手个体应被换手惩罚压低"

    def test_wiring(self):
        gp_src = _src("services", "evolution", "gp_miner.py")
        assert '"lambda_turnover": self.config.lambda_turnover' in gp_src
        assert "lam_to=self.config.lambda_turnover" in gp_src
        gpu_src = _src("services", "evolution", "gp_gpu_eval.py")
        assert "lam_to * _tfr(fv)" in gpu_src


# ── item14：AST 桥接 ────────────────────────────────────
class TestItem14AstBridge:
    def _patch_rows(self, monkeypatch, rows):
        import backend.services.factor_engine.active_set_policy as asp
        monkeypatch.setattr(
            asp, "load_factor_active_rows", lambda *a, **k: rows
        )
        # midlong_active_factor_set 里是函数内 import，patch 模块属性即可
        import backend.services.factor_engine.midlong_active_factor_set as mafs
        import backend.services.factor_engine.active_set_policy as asp2
        real_import = asp2.load_factor_active_rows
        monkeypatch.setattr(
            "backend.services.factor_engine.active_set_policy.load_factor_active_rows",
            lambda *a, **k: rows,
        )

    def test_bridge_filters_and_maps(self, monkeypatch):
        import backend.services.factor_engine.midlong_active_factor_set as mafs
        rows = [
            {"factor_id": "abc", "expr_ast": {"op": "mean", "args": [{"f": "close"}, {"c": 5}]},
             "icir": 0.42, "source": "gp"},
            {"factor_id": "s5m_xyz", "expr_ast": {"op": "mean", "args": [{"f": "close"}, {"c": 5}]},
             "icir": 0.5, "source": "gp|horizon=scalp|period=5m"},
            {"factor_id": "def", "expr_ast": None, "icir": 0.3, "source": "gp"},
            {"factor_id": "neg", "expr_ast": {"op": "std", "args": [{"f": "volume"}, {"c": 10}]},
             "icir": -0.35, "source": "mcts"},
        ]
        monkeypatch.setattr(
            "backend.services.factor_engine.active_set_policy.load_factor_active_rows",
            lambda *a, **k: rows,
        )
        out = mafs.MidLongActiveFactorSet._tradable_ast_bridge()
        fids = [r["factor_id"] for r in out]
        assert "evo_abc" in fids and "evo_neg" in fids
        assert "evo_s5m_xyz" not in fids and "evo_def" not in fids
        rec = next(r for r in out if r["factor_id"] == "evo_abc")
        assert rec["extra"]["kind"] == "ast"
        assert rec["extra"]["timeframe"] == "4h"
        assert rec["scores"]["expected_sign"] == 1
        rec_neg = next(r for r in out if r["factor_id"] == "evo_neg")
        assert rec_neg["scores"]["expected_sign"] == -1

    def test_eval_ast_on_df(self):
        from backend.services.factor_engine.midlong_factor_route import _eval_ast_on_df
        df = pd.DataFrame({"close": np.arange(1, 41, dtype=float)})
        vals = _eval_ast_on_df({"op": "mean", "args": [{"f": "close"}, {"c": 5}]}, df)
        assert vals is not None and len(vals) == 40
        assert np.isfinite(vals[4]) and np.isnan(vals[0])  # 窗口头部 NaN

    def test_ast_history_path_in_factor_history(self):
        src = _src("services", "factor_engine", "midlong_factor_route.py")
        assert '_eval_ast_on_df' in src and '"ast"' in src


# ── M8 / S7 ─────────────────────────────────────────────
class TestM8S7:
    def test_m8_default_three(self):
        import backend.config.settings as _s
        assert _s.FACTOR_ROUTE_MIN_ACTIVE_FACTORS == 3

    def test_m8_env_updated(self):
        env = open(os.path.join(
            os.path.dirname(__file__), "..", "..", "..", ".env"
        ), encoding="utf-8", errors="surrogateescape").read()
        assert "FACTOR_ROUTE_MIN_ACTIVE_FACTORS=3" in env

    def test_s7_single_score_flow(self):
        src = _src("services", "full_auto", "loops", "scalp_loop.py")
        assert "_score_for_trade = float(_pen_score or _sig.factor_score or 0)" in src
        assert "factor_score=_score_for_trade" in src          # EV 闸 + 校准
        assert "float(_score_for_trade or 50) / 100.0" in src  # AlphaBus
        assert "_scalp_prop.confidence = float(_score_for_trade)" in src


# ── 算力刀：argmin 跨设备确定性 ─────────────────────────
class TestArgminDeterminism:
    def test_cpu_tolerant_first_on_ties(self):
        from backend.services.factor_engine.formula_ops import ts_argmin, ts_argmax
        # [3,1,1,2]：并列最小值取首个（位置1），归一化 (len-1-idx)/(len-1)=2/3
        v = np.array([3.0, 1.0, 1.0, 2.0] * 5)
        r = ts_argmin(v, 4)
        assert r[3] == pytest.approx(2.0 / 3.0)
        v2 = np.array([1.0 + 1e-15, 1.0, 2.0, 3.0] * 5)
        # 1e-15 级并列在容差内 → 取首个（idx 0，跨设备确定性语义）
        assert ts_argmin(v2, 4)[3] == pytest.approx(1.0)
        v3 = np.array([3.0, 2.0, 2.0, 1.0] * 5)
        assert ts_argmax(v3, 4)[3] == pytest.approx(1.0)  # 并列最大取首个

    def test_gpu_mirror_matches_cpu(self):
        torch = pytest.importorskip("torch")
        if not torch.cuda.is_available():
            pytest.skip("CUDA 不可用")
        from backend.services.evolution.gpu_batch_eval import eval_panel_batch
        from backend.services.factor_engine.expr.parser import parse
        rng = np.random.default_rng(21)
        n = 300
        fd = {
            "close": np.cumsum(rng.normal(0, 1, n)) + 100,
            "volume": np.abs(rng.normal(1000, 100, n)),
        }
        # 量化输入 + 窗口并列的病态结构
        ast = {"op": "corr", "args": [
            {"op": "mul", "args": [{"f": "close"}, {"c": 20}]},
            {"op": "ts_argmin", "args": [
                {"op": "std", "args": [
                    {"op": "ts_rank", "args": [{"f": "volume"}, {"c": 5}]}, {"c": 30}]},
                {"c": 30}]},
            {"c": 5}],
        }
        vals, _ = eval_panel_batch([ast], [fd], device="cuda", chunk=8, mem_mb=2000)
        ref = np.asarray(parse(ast).evaluate(fd), dtype=float)
        gv = np.asarray(vals[0], dtype=float)[:len(ref)]
        r = ref[:len(gv)]
        m = np.isfinite(gv) & np.isfinite(r)
        c = float(np.corrcoef(gv[m], r[m])[0, 1])
        assert c >= 0.99999, f"GPU/CPU argmin 确定性失效: pearson={c:.6f}"

    def test_validation_script_exists(self):
        assert os.path.exists(os.path.join(
            os.path.dirname(__file__), "..", "..",
            "scripts", "validate_gpu_equivalence.py",
        ))
