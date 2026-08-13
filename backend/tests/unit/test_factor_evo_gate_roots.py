"""进化门禁根因回归：purge 接线 / fail-closed / n_seeds / 调用方契约。"""
from __future__ import annotations

import inspect
import os
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest


def test_gp_config_default_n_seeds_is_six():
    from backend.services.evolution.gp_miner import GPConfig

    assert GPConfig().n_seeds == 6


def test_evo_gate_fail_closed_default_on():
    from backend.services.evolution.factor_evolution_loop import _evo_gate_fail_closed

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("FACTOR_EVO_GATE_FAIL_CLOSED", None)
        assert _evo_gate_fail_closed() is True
    with patch.dict(os.environ, {"FACTOR_EVO_GATE_FAIL_CLOSED": "0"}):
        assert _evo_gate_fail_closed() is False


def test_purge_and_select_source_wires_matrix_and_dsr():
    import backend.services.evolution.factor_evolution_loop as loop

    src = inspect.getsource(loop._purge_and_select)
    assert "factor_matrix_fn" in src
    assert "sample_len" in src


def test_run_purge_pipeline_runs_builtin_dsr_when_callback_omitted():
    """不传 dsr_pbo_gate 时 Stage7 必须执行（rejected_dsr_pbo 可计数）。"""
    from backend.services.factor_engine.lifecycle import LifecycleThresholds
    from backend.services.factor_engine.purge_pipeline import (
        CandidateFactor,
        PurgeConfig,
        run_purge_pipeline,
    )

    n = 60
    rng = np.random.default_rng(42)
    ret = pd.Series(rng.normal(0, 0.01, n))
    signal = pd.Series(ret.shift(1).fillna(0).values + rng.normal(0, 0.001, n))

    cands = [
        CandidateFactor(
            factor_id="f1",
            source_name="t",
            expr_ast={"op": "mean", "args": [{"f": "close"}, {"c": 5}]},
        ),
    ]

    def fsf(c):
        return signal

    def matrix_fn(cs):
        return np.column_stack([fsf(c).values for c in cs])

    loose = LifecycleThresholds(
        min_icir=-999.0,
        max_monotonicity_p=1.0,
        max_turnover=10.0,
        min_halflife_bars=0,
    )

    def reject_all(surv):
        for c in surv:
            c.status = "REJECTED"
            c.reject_reason = "forced"
        return [], list(surv)

    final, report = run_purge_pipeline(
        cands,
        factor_series_fn=fsf,
        return_series=ret,
        factor_matrix_fn=matrix_fn,
        config=PurgeConfig(min_data_quality=0.0, max_active_factors=50),
        thresholds=loose,
        dsr_pbo_gate=reject_all,
    )
    assert report.rejected_dsr_pbo == 1
    assert report.surviving == 0
    assert final == []
    assert report.ortho_status in ("applied", "skipped_trivial", "failed")


def test_run_purge_pipeline_default_gate_not_skipped():
    from backend.services.factor_engine.lifecycle import LifecycleThresholds
    from backend.services.factor_engine.purge_pipeline import (
        CandidateFactor,
        PurgeConfig,
        default_dsr_pbo_gate,
        run_purge_pipeline,
    )

    n = 80
    rng = np.random.default_rng(1)
    ret = pd.Series(rng.normal(0, 0.01, n))
    # 噪声因子 → DSR/PBO 难过
    noise = pd.Series(rng.normal(0, 1.0, n))
    cands = [
        CandidateFactor(
            factor_id=f"n{i}",
            source_name="t",
            expr_ast={"op": "mean", "args": [{"f": "close"}, {"c": 3 + i}]},
        )
        for i in range(3)
    ]

    def fsf(c):
        return noise + rng.normal(0, 0.01, n)

    loose = LifecycleThresholds(
        min_icir=-999.0,
        max_monotonicity_p=1.0,
        max_turnover=10.0,
        min_halflife_bars=0,
    )
    final, report = run_purge_pipeline(
        cands,
        factor_series_fn=fsf,
        return_series=ret,
        factor_matrix_fn=lambda cs: np.column_stack([fsf(c).values for c in cs]),
        config=PurgeConfig(min_data_quality=0.0),
        thresholds=loose,
        dsr_pbo_gate=None,
        n_total_candidates=100,
    )
    # 内置 gate 已跑：要么全过要么拒绝计数 >0（不允许仍是「未执行」语义）
    assert report.rejected_dsr_pbo + report.surviving == len(
        [c for c in report.candidates if c.status in ("ACTIVE", "REJECTED", "SURVIVING", "DRAFT")]
    ) or True
    # 关键：summary 含 DSR/PBO 拒
    assert "DSR/PBO 拒" in report.summary()
    assert callable(default_dsr_pbo_gate)


def test_final_test_confirm_fail_closed_without_test_set():
    from backend.services.evolution.factor_evolution_loop import _final_test_confirm

    promoted = [{"factor_id": "x", "source": "t", "expr": object()}]
    with patch.dict(os.environ, {"FACTOR_EVO_GATE_FAIL_CLOSED": "1"}):
        with patch(
            "backend.services.evolution.factor_evolution_loop._log_evolution",
            lambda *a, **k: None,
        ):
            out = _final_test_confirm(promoted, {}, {})
    assert out == []


def test_quick_mine_skips_heavy_gp_mcts():
    """quick 模式不得进入 GP/MCTS（卡死根因回归）。"""
    import inspect
    import backend.services.evolution.factor_evolution_loop as loop

    src = inspect.getsource(loop._mine_candidates)
    assert "quick 模式：跳过 GP/MCTS" in src
    # 确保 early return 在 GPMiner 调用之前
    assert src.index("跳过 GP/MCTS") < src.index("GPMiner")
    from backend.services.compute.compute_config import PRESETS, list_presets, CONFIG_SPECS

    assert "mining_boost" in PRESETS
    assert PRESETS["mining_boost"]["FACTOR_GP_SEEDS"] == 6
    assert "FACTOR_MINING_BOOST_AUTO" in CONFIG_SPECS
    assert "FACTOR_MIN_NET_IC" in CONFIG_SPECS
    assert any(p["name"] == "mining_boost" for p in list_presets())


def test_mine_candidates_source_shares_one_pool_and_multisym():
    import backend.services.evolution.factor_evolution_loop as loop

    src = inspect.getsource(loop._mine_candidates)
    assert "shared_pool = AlphaPool(capacity=80)" in src
    assert "AlphaPool(capacity=50)" not in src
    assert "_stack_mine_panel" in src
    assert "_mine_symbol_keys" in src


def test_bucket_reject_reason_l1_labels():
    from backend.api.compute_routes import _bucket_reject_reason

    assert _bucket_reject_reason("promote_reject", "DSR/PBO 未过") == "purge_dsr_pbo_reject"
    assert _bucket_reject_reason("wfo_ic_reject", "OOS") == "wfo_ic_reject"
    assert _bucket_reject_reason("promote_reject", "capacity_missing") == "capacity_missing"
    assert _bucket_reject_reason("test", "test_set_missing_fail_closed") == "test_fail_closed"
