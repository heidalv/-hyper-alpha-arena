# -*- coding: utf-8 -*-
"""p2-arb-infra 单测：SDN runtime、scorecard 门、FundTransfer dry-run、carry 小资金帽。"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest


# ─────────────────────────── SDN runtime ───────────────────────────
def test_sdn_in_strategy_runtime():
    from backend.services.rebate_arb.strategy_runtime_registry import (
        get_runtime_spec, is_paper_auto_executable,
    )

    spec = get_runtime_spec("SDN")
    assert spec is not None
    assert spec.strategy_id == "SDN"
    assert spec.execution_mode == "hedge"
    assert spec.requires_funding_signal is True
    assert is_paper_auto_executable("SDN") is True


# ─────────────────────────── scorecard ───────────────────────────
def test_max_drawdown_none_on_short_series():
    from backend.services.arbitrage.scorecard import _max_drawdown
    assert _max_drawdown([]) is None
    assert _max_drawdown([1.0]) is None


def test_max_drawdown_detects_peak_to_trough():
    from backend.services.arbitrage.scorecard import _max_drawdown
    # cum: 10 → 15 → 5 → 12  → mdd = 10
    assert _max_drawdown([10.0, 5.0, -10.0, 7.0]) == 10.0


def test_promotion_gate_fails_without_samples(monkeypatch):
    from backend.services.arbitrage import scorecard as SC

    monkeypatch.setenv("ARB_SCORECARD_MIN_N", "10")
    monkeypatch.setenv("ARB_SCORECARD_MIN_ANN", "0.05")
    gate = SC._promotion_gate(
        {"n_closed": 2, "annualized": 0.2, "max_drawdown": 1.0, "pnl": 10.0},
        {"n": 0},
        {"multi_venue_coverage": True},
    )
    assert gate["passed"] is False
    assert any("样本" in r for r in gate["reasons"])


def test_promotion_gate_passes_with_enough(monkeypatch):
    from backend.services.arbitrage import scorecard as SC

    monkeypatch.setenv("ARB_SCORECARD_MIN_N", "5")
    monkeypatch.setenv("ARB_SCORECARD_MIN_ANN", "0.05")
    gate = SC._promotion_gate(
        {"n_closed": 5, "annualized": 0.12, "max_drawdown": 1.0, "pnl": 20.0},
        {"n": 0},
        {"multi_venue_coverage": True},
    )
    assert gate["passed"] is True
    assert gate["reasons"] == []


def test_compute_scorecard_survives_db_failure(monkeypatch, tmp_path):
    from backend.services.arbitrage import scorecard as SC

    monkeypatch.setattr(SC, "DATA_DIR", tmp_path)
    monkeypatch.setattr(SC, "_load_v3_positions", lambda days: {
        "n": 0, "n_active": 0, "n_closed": 0, "pnl": 0.0, "funding_captured": 0.0,
        "occupied_usd": 0.0, "avg_notional_usd": None, "annualized": None,
        "max_drawdown": None, "by_strategy": {}, "paper_n": 0, "live_n": 0,
        "notes": ["mock"], "rows": [],
    })
    monkeypatch.setattr(SC, "_load_rebate_logs", lambda days: {"n": 0, "notes": []})
    monkeypatch.setattr(SC, "_load_carry_sim", lambda: {"available": False, "notes": []})
    monkeypatch.setattr(SC, "_load_maker_ratio", lambda: {"available": False, "notes": []})

    out = SC.compute_scorecard(days=7)
    assert "kpi" in out
    assert out["kpi"]["pnl"] is None  # 无平仓 → 不用 0 冒充
    assert (tmp_path / "scorecard_latest.json").exists()


# ─────────────────────────── FundTransfer ───────────────────────────
def test_fund_transfer_disabled_by_default(monkeypatch, tmp_path):
    import asyncio
    from backend.services.arbitrage import fund_transfer as FT

    monkeypatch.setattr(FT, "DATA_DIR", tmp_path)
    monkeypatch.delenv("FUND_TRANSFER_ENABLED", raising=False)
    monkeypatch.delenv("FUND_TRANSFER_LIVE", raising=False)

    req = FT.TransferRequest(account_id=1, exchange="binance", amount=100.0)
    res = asyncio.run(FT.transfer_async(None, req))
    assert res.ok is False
    assert res.status == "rejected"
    assert "ENABLED" in res.message


def test_fund_transfer_dry_run_records_only(monkeypatch, tmp_path):
    import asyncio
    from backend.services.arbitrage import fund_transfer as FT

    monkeypatch.setattr(FT, "DATA_DIR", tmp_path)
    monkeypatch.setenv("FUND_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("FUND_TRANSFER_LIVE", "false")
    monkeypatch.setattr(FT, "_kill_switch_blocked", lambda: None)

    class BoomClient:
        def transfer(self, *a, **kw):
            raise AssertionError("dry-run 不得调交易所")

    req = FT.TransferRequest(account_id=14, exchange="binance", amount=50.0)
    res = asyncio.run(FT.transfer_async(BoomClient(), req))
    assert res.ok is True
    assert res.status == "dry_run"
    assert list(tmp_path.glob("ft_*.json"))
    ledger = FT.list_transfers(limit=5)
    assert ledger and ledger[0]["status"] == "dry_run"


def test_fund_transfer_live_unsupported_adapter(monkeypatch, tmp_path):
    import asyncio
    from backend.services.arbitrage import fund_transfer as FT

    monkeypatch.setattr(FT, "DATA_DIR", tmp_path)
    monkeypatch.setenv("FUND_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("FUND_TRANSFER_LIVE", "true")
    monkeypatch.setattr(FT, "_kill_switch_blocked", lambda: None)

    req = FT.TransferRequest(account_id=1, exchange="binance", amount=10.0, dry_run=False)
    res = asyncio.run(FT.transfer_async(object(), req))  # 无 transfer 方法
    assert res.ok is False
    assert res.status == "unsupported"


# ─────────────────────────── carry 小资金 ───────────────────────────
def test_carry_small_notional_is_5pct(monkeypatch):
    from backend.services.cashflow import carry_small as CS

    monkeypatch.setenv("CASHFLOW_CARRY_RESEARCH_EQUITY_USD", "5000")
    monkeypatch.setenv("CASHFLOW_CARRY_RESEARCH_BUCKET_PCT", "0.05")
    assert CS.capped_notional_usd() == 250.0


def test_carry_small_bucket_hard_cap(monkeypatch):
    from backend.services.cashflow import carry_small as CS

    monkeypatch.setenv("CASHFLOW_CARRY_RESEARCH_BUCKET_PCT", "0.99")  # 误配
    assert CS.research_bucket_pct() == 0.20


def test_carry_small_blocked_without_gate(monkeypatch, tmp_path):
    from backend.services.cashflow import carry_small as CS

    monkeypatch.setattr(CS, "DATA_DIR", tmp_path)
    monkeypatch.setenv("CASHFLOW_CARRY_SMALL_ENABLED", "true")
    monkeypatch.setenv("CASHFLOW_CARRY_SMALL_LIVE", "false")
    monkeypatch.setattr(CS, "_scorecard_gate", lambda: {
        "passed": False, "gate": {"passed": False, "reasons": ["样本不足"]}, "kpi": None,
    })

    out = CS.run_carry_small()
    assert out["ok"] is False
    assert "晋升门" in (out.get("reason") or "")


def test_carry_small_live_hard_off_falls_to_paper(monkeypatch, tmp_path):
    from backend.services.cashflow import carry_small as CS

    monkeypatch.setattr(CS, "DATA_DIR", tmp_path)
    monkeypatch.setenv("CASHFLOW_CARRY_SMALL_ENABLED", "true")
    monkeypatch.setenv("CASHFLOW_CARRY_SMALL_LIVE", "true")
    monkeypatch.setattr(CS, "_scorecard_gate", lambda: {
        "passed": True, "gate": {"passed": True, "reasons": []}, "kpi": {},
    })
    monkeypatch.setattr(CS, "_live_trading_hard_off", lambda: True)

    called = {}

    def fake_sim(**kw):
        called["notional"] = kw.get("notional_usd")
        return {"ok": True, "venues": ["binance"], "multi_venue_coverage": True,
                "combos": [{}], "executions": [{}], "notes": []}

    monkeypatch.setattr(
        "backend.services.cashflow.e2b_carry_sim.run_carry_sim", fake_sim,
    )

    out = CS.run_carry_small()
    assert out.get("live_blocked") is True
    assert out["mode"] == "paper"
    assert out["ok"] is True
    assert called["notional"] == CS.capped_notional_usd()


def test_arb_infra_jobs_register(monkeypatch):
    from backend.services.arbitrage.jobs import register_arb_infra_jobs

    registered_ids: List[str] = []

    class FakeSched:
        def add_interval_task(self, **kw):
            registered_ids.append(kw.get("task_id"))

        def add_cron_task(self, **kw):
            registered_ids.append(kw.get("task_id"))

    def wrap(name, fn):
        return fn

    def job(name, *a, **kw):
        pass

    monkeypatch.setattr(
        "backend.services.analysis.scheduling.off_peak_cron",
        lambda h, m: {"hour": h, "minute": m},
    )
    out = register_arb_infra_jobs(FakeSched(), wrap, job)
    assert "arb_scorecard" in out
    assert "carry_small_capital" in out
    assert "v3_arb_scorecard" in registered_ids
