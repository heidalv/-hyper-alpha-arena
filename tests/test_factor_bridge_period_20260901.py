"""F1/F8 断层与三周期修复回归测试（2026-09-01）。

- F1：ScalpActiveFactorSet._tradable_ast_bridge 只收短线档（s5m_/horizon=scalp），
  排除 seed_bootstrap 与中线档；mid tier 策略注入走 MidLongActiveFactorSet。
- F8：scalp 白名单按周期过滤（4h 中线因子不再进短线 allowlist）。
"""
import pytest

import backend.services.factor_engine.scalp_active_factor_set as sas
from backend.services.scalp.scalp_factor_exclude import get_scalp_factor_allowlist


def _rows(monkeypatch, rows):
    """monkeypatch active_set_policy.load_factor_active_rows 返回假 TRADABLE 行。"""
    import backend.services.factor_engine.active_set_policy as _asp
    monkeypatch.setattr(
        _asp, "load_factor_active_rows",
        lambda role, parse_expr=False, limit=50: rows,
    )


class TestScalpAstBridge:
    def test_only_short_horizon_rows_admitted(self, monkeypatch):
        rows = [
            {"factor_id": "s5m_abc", "expr_ast": {"op": "x"}, "source": "gp|horizon=scalp|period=5m",
             "state": "PAPER", "period": "5m", "icir": 0.45},
            {"factor_id": "mid4h_def", "expr_ast": {"op": "y"}, "source": "gp|horizon=midlong",
             "state": "PAPER", "period": "4h", "icir": 0.50},
            {"factor_id": "s5m_xyz", "expr_ast": {"op": "z"}, "source": "mcts|horizon=scalp",
             "state": "ACTIVE", "period": "15m", "icir": -0.30},
        ]
        _rows(monkeypatch, rows)
        out = sas.ScalpActiveFactorSet._tradable_ast_bridge()
        ids = {r["factor_id"] for r in out}
        assert ids == {"evo_s5m_abc", "evo_s5m_xyz"}  # 中线档被排除

    def test_seed_bootstrap_excluded(self, monkeypatch):
        rows = [
            {"factor_id": "s5m_seed", "expr_ast": {"op": "x"}, "source": "seed_bootstrap|horizon=scalp",
             "state": "PAPER", "period": "5m", "icir": 0.05},
            {"factor_id": "s5m_real", "expr_ast": {"op": "y"}, "source": "gp|horizon=scalp",
             "state": "PAPER", "period": "5m", "icir": 0.40},
        ]
        _rows(monkeypatch, rows)
        out = sas.ScalpActiveFactorSet._tradable_ast_bridge()
        assert {r["factor_id"] for r in out} == {"evo_s5m_real"}

    def test_expected_sign_from_icir(self, monkeypatch):
        rows = [
            {"factor_id": "s5m_pos", "expr_ast": {"op": "x"}, "source": "gp|horizon=scalp",
             "state": "PAPER", "period": "5m", "icir": 0.42},
            {"factor_id": "s5m_neg", "expr_ast": {"op": "y"}, "source": "gp|horizon=scalp",
             "state": "PAPER", "period": "5m", "icir": -0.42},
        ]
        _rows(monkeypatch, rows)
        out = {r["factor_id"]: r for r in sas.ScalpActiveFactorSet._tradable_ast_bridge()}
        assert out["evo_s5m_pos"]["scores"]["expected_sign"] == 1
        assert out["evo_s5m_neg"]["scores"]["expected_sign"] == -1

    def test_paper_role_cap_marker(self, monkeypatch):
        rows = [
            {"factor_id": "s5m_p", "expr_ast": {"op": "x"}, "source": "gp|horizon=scalp",
             "state": "PAPER", "period": "5m", "icir": 0.4},
        ]
        _rows(monkeypatch, rows)
        out = sas.ScalpActiveFactorSet._tradable_ast_bridge()
        assert out[0]["extra"]["role"] == "paper"


class TestScalpAllowlistPeriodFilter:
    def test_short_only_in_allowlist(self, monkeypatch):
        import backend.services.factor_engine.active_set_policy as _asp
        monkeypatch.setattr(
            "backend.services.scalp.scalp_factor_exclude.scalp_use_vetted_factors_only",
            lambda: True,
        )
        monkeypatch.setattr(
            _asp, "load_factor_active_rows",
            lambda role: [
                {"factor_id": "s5m_aaa", "source": "gp|horizon=scalp"},
                {"factor_id": "mid_bbb", "source": "gp|horizon=midlong"},
                {"factor_id": "seed_mom10", "source": "seed_bootstrap"},
            ],
        )
        monkeypatch.setattr(
            sas, "scalp_active_factor_set",
            type("S", (), {"get_active_factors": lambda self: []})(),
        )
        allow = get_scalp_factor_allowlist()
        assert "s5m_aaa" in allow
        assert "mid_bbb" not in allow  # 4h 中线档不进短线白名单
        assert "seed_mom10" not in allow

    def test_vetted_off_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.scalp.scalp_factor_exclude.scalp_use_vetted_factors_only",
            lambda: False,
        )
        assert get_scalp_factor_allowlist() is None
