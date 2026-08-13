"""阶段7补挖必须落库；晋升日志仅在落库后记 promote。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.services.evolution import factor_evolution_loop as fel


def test_replace_degraded_persists_and_logs_promote():
    degraded = [{"factor_id": "old1", "state": "ACTIVE", "source": "mom"}]
    survivor = {
        "factor_id": "new1",
        "source": "rev",
        "expr_ast": {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]},
        "eval_result": MagicMock(icir=1.2),
        "_to_state": "PAPER",
        "incremental_corr": 0.1,
    }

    with patch.object(fel, "_deactivate_factor") as deact, \
         patch.object(fel, "_log_evolution") as log_evo, \
         patch.object(fel, "_mine_candidates", return_value=[]), \
         patch.object(fel, "_evaluate_candidates", return_value={}), \
         patch.object(fel, "_purge_and_select", return_value=[survivor]), \
         patch.object(fel, "_promote_factors", return_value=[survivor]), \
         patch.object(fel, "_save_active_factors") as save, \
         patch.object(fel, "_log_promote_committed") as log_commit, \
         patch.object(fel, "_trigger_meta_retrain_after_promote") as meta:
        out = fel._replace_degraded(degraded, dfs={"BTC": MagicMock()}, period="4h")

    assert len(out) == 1
    assert out[0]["factor_id"] == "new1"
    deact.assert_called_once_with("old1")
    save.assert_called_once()
    saved_rows = save.call_args[0][0]
    assert saved_rows[0]["factor_id"] == "new1"
    assert saved_rows[0]["state"] == "PAPER"
    assert saved_rows[0]["expr_ast"]
    log_commit.assert_called_once()
    meta.assert_called_once_with(1)
    # quarantine 仍写 degrade 日志
    quarantine_calls = [
        c for c in log_evo.call_args_list
        if (c.kwargs.get("action") == "quarantine")
        or (len(c.args) >= 2 and "degrade" in str(c.args[1]))
    ]
    assert quarantine_calls


def test_promoted_rows_skip_empty_ast():
    rows = fel._promoted_rows_for_save(
        [
            {"factor_id": "a", "expr_ast": {}, "_to_state": "PAPER"},
            {
                "factor_id": "b",
                "expr_ast": {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]},
                "_to_state": "PAPER",
                "eval_result": MagicMock(icir=0.5),
            },
        ],
        period="4h",
    )
    assert [r["factor_id"] for r in rows] == ["b"]
