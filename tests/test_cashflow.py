# -*- coding: utf-8 -*-
"""cashflow / carry sim 纯逻辑单测（不连库）。"""
from __future__ import annotations

from backend.services.cashflow.e2b_carry_sim import run_carry_sim
from backend.services.cashflow.rebate_snapshot import snapshot_rebate_config


def test_carry_sim_single_venue(monkeypatch):
    monkeypatch.setenv("CASHFLOW_CARRY_SIM_ENABLED", "true")

    def _fake_rates(**kw):
        return {"hyperliquid": {"BTC/USDT": 0.0001}}

    monkeypatch.setattr(
        "backend.services.rebate_arb.funding_rate_provider.latest_funding_by_venue", _fake_rates,
    )
    out = run_carry_sim(execute_paper=False)
    assert out["multi_venue_coverage"] is False
    assert out.get("reason")


def test_rebate_snapshot_writes_file(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.services.cashflow.rebate_snapshot.DATA_DIR", tmp_path)
    out = snapshot_rebate_config()
    assert out.get("ts_ms")
    assert (tmp_path / "rebate_config_snapshot.json").exists()
