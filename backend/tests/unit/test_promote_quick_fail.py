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


def test_promote_uses_per_factor_pbo_not_batch():
    """同批幸存者各自用自己的时序 PBO，不再共用「最佳因子」的 PBO。

    回归：2026-09-07 实证同分钟 ICIR 0.84/1.15/1.23 却共用 pbo=0.42 整批否决。
    """
    class _ER:
        def __init__(self, icir):
            self.icir = icir
            self.monotonicity_p = 0.01
            self.turnover = 0.2
            self.halflife_bars = 10

    # 稳定正 IC 序列 → 低 PBO；强翻转序列 → 高 PBO
    stable = [0.05] * 40
    flipped = [0.08] * 20 + [-0.08] * 20
    survivors = [
        {
            "factor_id": "good",
            "source": "test",
            "expr_ast": {},
            "incremental_corr": 0.1,
            "eval_result": _ER(1.2),
            "pooled_ic": np.asarray(stable, dtype=float),
        },
        {
            "factor_id": "bad",
            "source": "test",
            "expr_ast": {},
            "incremental_corr": 0.1,
            "eval_result": _ER(1.3),
            "pooled_ic": np.asarray(flipped, dtype=float),
        },
    ]
    eval_results = {
        "good": {"net_ic": 0.05, "expr": None, "source": "test"},
        "bad": {"net_ic": 0.05, "expr": None, "source": "test"},
    }

    calls = []

    def fake_dsr(*, icir_list, n_total_candidates, sample_len, ic_series=None):
        calls.append({"icir_list": list(icir_list), "n_series": 0 if ic_series is None else len(ic_series)})
        # 批次摘要（多 ICIR）随便过
        if len(icir_list) > 1:
            return {
                "dsr_result": {"significant": True},
                "pbo_result": {"pbo": 0.42, "indeterminate": False},
                "best_icir": max(icir_list), "n_factors": len(icir_list),
            }
        series = list(ic_series or [])
        # 用序列均值符号区分：稳定正 → 低 PBO；含负段 → 高 PBO
        mean = float(np.mean(series)) if series else 0.0
        pbo = 0.20 if mean > 0.01 else 0.80
        return {
            "dsr_result": {"significant": True},
            "pbo_result": {"pbo": pbo, "indeterminate": False},
            "best_icir": icir_list[0], "n_factors": 1,
        }

    with patch(
        "backend.services.factor_engine.dsr_pbo.compute_dsr_pbo_for_factors",
        side_effect=fake_dsr,
    ), patch(
        "backend.services.evolution.factor_evolution_loop._log_evolution",
    ), patch(
        "backend.services.evolution.factor_evolution_loop._estimate_capacity_usd_from_dfs",
        return_value=1e7,
    ), patch(
        "backend.services.factor_engine.active_set_policy.load_factor_active_rows",
        return_value=[1],  # 非空 TRADABLE，关掉冷启动 PBO 豁免
    ):
        promoted = _promote_factors(
            survivors, eval_results, [1.2, 1.3], n_total=10,
            dfs={"BTC": _make_df(500)}, period="4h",
        )

    ids = {p["factor_id"] for p in promoted}
    assert "good" in ids, "低 PBO 因子应过门（不再被批次 0.42 连带否决）"
    assert "bad" not in ids, "高 PBO 因子仍应被拒"
    # 至少：1 次批次 + 2 次逐因子
    assert len(calls) >= 3
    assert any(c["n_series"] == 40 and len(c["icir_list"]) == 1 for c in calls)


def test_promote_logs_reject_when_dsr_fails():
    """DSR 不显著时 ORTHO→PAPER 失败，写出可审计拒绝原因。

    [2026-09-02] 原用例靠"41 个 ICIR≈0.4-0.5"制造 DSR 不显著。M2b 之后 DSR 的
    sample_len 改为验证根数×币数（5m ≈ 45d×288 = 12960），ICIR 0.5 的 t≈57，
    41 次多重检验也稳过——前提失效而非闸门失效。改为直接钉死 DSR/PBO 结果
    （不显著 + PBO 0.9），只验证拒绝路径本身：不晋级 + 写出可审计原因 + 含 DSR 标记。
    也不再受 DB 中 TRADABLE 池是否为空（冷启动 n_trials 分母）的影响。
    """
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
    icirs = [0.5] + [0.4] * 40
    fake_dsr = {
        "dsr_result": {"significant": False, "dsr_prob": 0.3},
        "pbo_result": {"pbo": 0.9, "indeterminate": False},
        "best_icir": 0.5, "n_factors": 41,
    }
    with patch(
        "backend.services.factor_engine.dsr_pbo.compute_dsr_pbo_for_factors",
        return_value=fake_dsr,
    ), patch(
        "backend.services.evolution.factor_evolution_loop._log_evolution",
    ):
        promoted = _promote_factors(
            survivors, eval_results, icirs, n_total=41,
            dfs={"BTC": _make_df(500)}, period="5m",
        )
    assert promoted == []
    rejects = survivors[0].get("_promote_rejects") or []
    assert rejects
    assert rejects[0].get("factor_id") == "f1"
    assert rejects[0].get("dsr_significant") is False, "拒绝记录必须带 DSR 判定，供审计"


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
        import time
        report = _run_evolution_loop_impl(
            symbols=["BTC"], period="5m", quick=True, t0=time.time(),
        )
    assert report.get("error") == "promote_rejected"
    assert report.get("promoted") == 0
    assert report.get("promote_rejects")
    mon.assert_not_called()
