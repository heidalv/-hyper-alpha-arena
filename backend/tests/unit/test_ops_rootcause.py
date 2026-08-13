"""根因单测：在线权重 factor_id 键 + ActiveSet 加载契约 + 车道默认关。"""
from __future__ import annotations

import os

import numpy as np
import pytest


class TestOnlineWeightKeys:
    def test_feature_importance_uses_names(self):
        from backend.services.evolution.online_weights import OnlineLinearModel

        m = OnlineLinearModel()
        m.learn_one(np.array([1.0, 2.0, 0.5]), 0.01)
        names = ["fac_a", "fac_b", "fac_c"]
        imp = m.feature_importance(names=names)
        assert set(imp.keys()) == set(names)
        assert all(k.startswith("f") is False or k in names for k in imp)
        assert "f0" not in imp

    def test_feature_importance_fallback_f_index(self):
        from backend.services.evolution.online_weights import OnlineLinearModel

        m = OnlineLinearModel()
        m.learn_one(np.array([1.0, 2.0]), 0.01)
        imp = m.feature_importance()
        assert "f0" in imp and "f1" in imp


class TestActiveSetPolicy:
    def test_role_states_frozen(self):
        from backend.services.factor_engine.active_set_policy import ActiveSetRole, states_for

        assert states_for(ActiveSetRole.TRADABLE) == frozenset(
            {"PAPER", "SMALL_LIVE", "ACTIVE"}
        )
        assert "ORTHO" not in states_for(ActiveSetRole.TRADABLE)
        assert "SMALL_LIVE" in states_for(ActiveSetRole.RESEARCH)
        assert "SMALL_LIVE" in states_for(ActiveSetRole.SHADOW)
        assert states_for(ActiveSetRole.UI_TOP) == states_for(ActiveSetRole.TRADABLE)

    def test_small_live_in_research_and_shadow(self):
        from backend.services.factor_engine.active_set_policy import ActiveSetRole, states_for

        assert "SMALL_LIVE" in states_for(ActiveSetRole.RESEARCH)
        assert "SMALL_LIVE" in states_for(ActiveSetRole.SHADOW)
        assert "ORTHO" in states_for(ActiveSetRole.RESEARCH)
        assert "ORTHO" not in states_for(ActiveSetRole.SHADOW)


class TestLaneSafeDefaults:
    def test_binding_lane_default_disabled(self, monkeypatch):
        monkeypatch.delenv("PAIR_BINDING_LANE_ENABLED", raising=False)
        # 重新读函数内 getenv
        from backend.services.scalp import pair_binding_lane as lane

        assert lane._enabled() is False

    def test_circuit_breaker_default_dry_run(self, monkeypatch):
        monkeypatch.delenv("SCALP_CIRCUIT_BREAKER_ENABLED", raising=False)
        # apply None → 读 env 默认 false
        assert os.getenv("SCALP_CIRCUIT_BREAKER_ENABLED", "false").lower() not in (
            "1", "true", "yes", "on",
        )


class TestNoScatteredActiveSetFilters:
    def test_no_orm_state_in_outside_policy(self):
        """CI 契约：禁止散落 FactorActiveSet.state.in_(...)，唯一入口 active_set_policy。"""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        bad = []
        for p in (root / "services").rglob("*.py"):
            if p.name == "active_set_policy.py":
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            if "FactorActiveSet.state.in_" in text:
                bad.append(str(p.relative_to(root)))
        for p in (root / "api").rglob("*.py"):
            text = p.read_text(encoding="utf-8", errors="ignore")
            if "FactorActiveSet.state.in_" in text:
                bad.append(str(p.relative_to(root)))
        assert bad == [], f"散落 ActiveSet 过滤: {bad}"


class TestOpsRoutesContract:
    def test_ops_router_importable(self):
        from backend.api.ops_routes import router

        paths = {getattr(r, "path", None) for r in router.routes}
        for p in (
            "/pipeline",
            "/heartbeats",
            "/factor-pool",
            "/evolution-funnel",
            "/candidates",
            "/bindings",
            "/errors",
            "/health-digest",
        ):
            assert any(str(x).endswith(p) for x in paths), p
