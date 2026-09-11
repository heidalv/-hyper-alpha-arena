# -*- coding: utf-8 -*-
"""dashboard 资本与边际聚合冒烟。"""
from __future__ import annotations


def test_capital_margin_endpoint_shape(monkeypatch):
    from backend.api import dashboard_routes as D

    monkeypatch.setattr(D, "_freshness", lambda: [
        {"name": "allocation", "exists": True, "age_h": 1.0, "stale": False, "path": "x"},
    ])

    # 避免真连 DB：桩掉重依赖
    import backend.services.allocation.capital_allocator as CA
    monkeypatch.setattr(CA, "latest_allocation", lambda: {
        "bucket_weights": {"trend": 0.6, "cashflow": 0.3, "research": 0.1},
        "credibility_scale": 0.5,
    })
    monkeypatch.setattr(CA, "allocate", lambda persist=False: CA.latest_allocation())
    monkeypatch.setattr(CA, "promotion_frozen", lambda: {"frozen": False})

    monkeypatch.setattr(
        "backend.services.arbitrage.scorecard.latest_scorecard",
        lambda: {"kpi": {"pnl": 1.2, "annualized": 0.1, "max_drawdown": 0.5, "occupied_usd": 100}},
    )

    class FakeLedgers:
        @staticmethod
        def agent_credibility(days):
            return [{"agent": "timing", "n_scored": 10, "avg_score": 0.6}]

    import backend.services.analysis.ledgers as L
    monkeypatch.setattr(L, "agent_credibility", FakeLedgers.agent_credibility)

    monkeypatch.setattr(
        "backend.services.strategies.event.base.registered_strategies",
        lambda: [],
    )

    out = D.capital_margin(days=14)
    assert "bucket_weights" in out
    assert out["bucket_weights"]["trend"] == 0.6
    assert out["kpi"]["arb_pnl"] == 1.2
    assert out["agents"][0]["agent"] == "timing"
    assert out["freshness"]
