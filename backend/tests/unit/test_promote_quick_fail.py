"""P0-3: 晋升门可审计拒绝 + quick 无晋升快失败。"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from backend.services.evolution.factor_evolution_loop import (
    _lookback_for_period,
    _promote_factors,
    _run_evolution_loop_impl,
)


def _make_df(n: int) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    return pd.DataFrame({
        "open": close * 0.999, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": rng.uniform(1e3, 2e3, n),
    })


def test_promote_logs_reject_when_dsr_fails():
    """DSR 不显著时 ORTHO→PAPER 失败，写出可审计拒绝原因。"""
    class _ER:
        icir = 0.5
        monotonicity_p = 0.01
        turnover = 0.2
        halflife_bars = 10

    survivors = [{
        "factor_id": "f1",
        "source": "test",
        "expr_ast": {},
        "incremental_corr": 0.1,
        "eval_result": _ER(),
    }]
    eval_results = {"f1": {"net_ic": 0.05, "expr": None, "source": "test"}}
    # 大量平庸 ICIR → DSR 难显著
    icirs = [0.5] + [0.4] * 40
    promoted = _promote_factors(
        survivors, eval_results, icirs, n_total=41,
        dfs={"BTC": _make_df(500)}, period="5m",
    )
    assert promoted == []
    rejects = survivors[0].get("_promote_rejects") or []
    assert rejects
    assert rejects[0].get("factor_id") == "f1"


def test_quick_promote_rejected_early_return():
    """quick + 晋升全拒 → 快失败，不进入后续长尾。"""
    need = _lookback_for_period("5m")
    dfs = {"BTC": _make_df(need)}

    rejected = [{
        "factor_id": "f1", "source": "test",
        "_promote_rejects": [{"factor_id": "f1", "reason": "池筛选未达标"}],
    }]

    with patch(
        "backend.services.evolution.factor_evolution_loop._load_data", return_value=dfs,
    ), patch(
        "backend.services.evolution.factor_evolution_loop._ensure_governance_columns",
    ), patch(
        "backend.services.evolution.factor_evolution_loop._mine_candidates",
        return_value=[(MagicMock(), "test")],
    ), patch(
        "backend.services.evolution.factor_evolution_loop._evaluate_candidates",
        return_value={"f1": {"avg_icir": 0.5, "net_ic": 0.05}},
    ), patch(
        "backend.services.evolution.factor_evolution_loop._load_active_factors",
        return_value=[],
    ), patch(
        "backend.services.evolution.factor_evolution_loop._purge_and_select",
        return_value=rejected,
    ), patch(
        "backend.services.evolution.factor_evolution_loop._promote_factors",
        return_value=[],
    ), patch(
        "backend.services.evolution.factor_evolution_loop._monitor_active",
    ) as mon:
        report = _run_evolution_loop_impl(
            symbols=["BTC"], period="5m", quick=True, t0=0.0,
        )
    assert report.get("error") == "promote_rejected"
    assert report.get("promoted") == 0
    assert report.get("promote_rejects")
    mon.assert_not_called()
