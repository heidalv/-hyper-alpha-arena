"""P1-1: Codegen LLM 接入 _mine_candidates（quick 跳过 / 完整调用）。"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from backend.services.evolution.factor_evolution_loop import _mine_candidates


def _dfs(n=200):
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    df = pd.DataFrame({
        "open": close, "high": close, "low": close, "close": close,
        "volume": rng.uniform(10, 20, n),
    })
    return {"BTC": df}


def test_codegen_skipped_in_quick():
    with patch.dict("os.environ", {"FACTOR_MCTS_ENABLED": "0", "FACTOR_CODEGEN_ENABLED": "1"}):
        with patch(
            "backend.services.evolution.gp_miner.GPMiner.mine", return_value=[],
        ), patch(
            "backend.services.evolution.alpha_miner.AlphaMiner",
        ) as am:
            cands = _mine_candidates(_dfs(), period="5m", quick=True)
    am.assert_not_called()
    assert not any(str(s).startswith("llm_") for _, s in cands)


def test_codegen_called_when_not_quick():
    fake_expr = MagicMock()
    fake_expr.expr_id = "llmdeadbeef"
    fake_expr.ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}

    class _Miner:
        def __init__(self, *a, **k):
            pass

        def mine_llm_candidates(self, *a, **k):
            return [(fake_expr, 0.1)]

    with patch.dict("os.environ", {"FACTOR_MCTS_ENABLED": "0", "FACTOR_CODEGEN_ENABLED": "1"}):
        with patch(
            "backend.services.evolution.gp_miner.GPMiner.mine", return_value=[],
        ), patch(
            "backend.services.evolution.alpha_miner.AlphaMiner", _Miner,
        ), patch(
            "backend.services.evolution.alpha_miner.CodegenCritic", MagicMock,
        ), patch(
            "backend.services.evolution.factor_evolution_loop._load_active_factors",
            return_value=[],
        ), patch(
            "backend.services.evolution.factor_evolution_loop._log_evolution",
        ):
            cands = _mine_candidates(_dfs(), period="5m", quick=False)

    sources = [s for _, s in cands]
    assert any(str(s).startswith("llm_") for s in sources)
