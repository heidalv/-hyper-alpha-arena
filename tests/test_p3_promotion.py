# -*- coding: utf-8 -*-
"""p3-promotion 单测：分配器可信度缩放、阶梯、freeze、付费决策、F4、基差门、尾部 APR。"""
from __future__ import annotations

from typing import List


def test_blend_buckets_half_scale():
    from backend.services.allocation.capital_allocator import _blend_buckets, DEFAULT_BUCKETS
    suggested = {"trend": 0.20, "cashflow": 0.60, "research": 0.20}
    out = _blend_buckets(suggested, 0.5)
    assert abs(out["trend"] - (DEFAULT_BUCKETS["trend"] * 0.5 + 0.20 * 0.5)) < 1e-6
    assert abs(sum(out.values()) - 1.0) < 1e-6


def test_credibility_scale_low_n():
    from backend.services.allocation.capital_allocator import _credibility_scale
    assert _credibility_scale({"n_scored": 5, "avg_score": 0.9}) == 0.5


def test_credibility_scale_high():
    from backend.services.allocation.capital_allocator import _credibility_scale
    s = _credibility_scale({"n_scored": 40, "avg_score": 0.8})
    assert s == 0.8


def test_promote_blocked_by_freeze(monkeypatch, tmp_path):
    from backend.services.allocation import capital_allocator as CA

    monkeypatch.setattr(CA, "DATA_DIR", tmp_path)
    monkeypatch.setattr(CA, "LADDER_PATH", tmp_path / "research_ladder.json")
    monkeypatch.setattr(CA, "promotion_frozen", lambda: {"frozen": True, "seconds_left": 100})
    res = CA.promote_strategy("e5_2_funding_shock")
    assert res["ok"] is False
    assert res["reason"] == "promotion_freeze"


def test_promote_and_advance_ladder(monkeypatch, tmp_path):
    from backend.services.allocation import capital_allocator as CA

    monkeypatch.setattr(CA, "DATA_DIR", tmp_path)
    monkeypatch.setattr(CA, "LADDER_PATH", tmp_path / "research_ladder.json")
    monkeypatch.setattr(CA, "promotion_frozen", lambda: {"frozen": False})

    r = CA.promote_strategy("e5_test")
    assert r["ok"] and r["strategy"]["stage"] == "small"
    assert r["strategy"]["bucket_pct"] == 0.05

    r2 = CA.advance_ladder("e5_test", positive_4w=True)
    assert r2["ok"] and r2["strategy"]["bucket_pct"] == 0.10

    r3 = CA.advance_ladder("e5_test", positive_4w=False)
    assert r3["ok"] and r3["strategy"]["bucket_pct"] == 0.05


def test_paid_data_skip_when_insignificant():
    from backend.research.paid_data_decision import decide_for_vendor, PAID_CANDIDATES
    vendor = PAID_CANDIDATES[0]
    reports = [{
        "event_type": "liquidation.cascade",
        "n": 50,
        "promotion_ready": False,
        "excess_ci_bp": [-20.0, 10.0],
    }]
    d = decide_for_vendor(vendor, reports)
    assert d["decision"] == "SKIP"


def test_paid_data_buy_when_insufficient():
    from backend.research.paid_data_decision import decide_for_vendor, PAID_CANDIDATES
    vendor = PAID_CANDIDATES[1]
    reports = [{"event_type": "token.unlock", "n": 5, "promotion_ready": False}]
    d = decide_for_vendor(vendor, reports)
    assert d["decision"] == "BUY"


def test_f4_gate_fails_without_artifacts(monkeypatch, tmp_path):
    from backend.services import trend_e1_f4_gate as F4

    monkeypatch.setattr(F4, "DATA_DIR", tmp_path)
    monkeypatch.setattr(F4, "GATE_PATH", tmp_path / "f4.json")
    monkeypatch.setenv("TREND_E1_LIVE_ASTER", "false")
    monkeypatch.setenv("TREND_E1_F4_SKIP_FEE", "true")
    monkeypatch.setenv("TREND_E1_F4_HALT_TESTS_OK", "true")
    monkeypatch.setattr(F4, "_check_freeze_and_kill", lambda: [
        {"name": "promotion_freeze", "ok": True},
        {"name": "kill_switch", "ok": True},
    ])
    out = F4.evaluate_f4_gate(persist=True)
    assert out["passed"] is False  # drift/days 缺文件
    assert out["live_allowed"] is False


def _write_drift(tmp_path, **over):
    """按 compute_trend_drift 的真实落盘形状造 latest.json。"""
    import json

    payload = {
        "account_id": 14,
        "as_of_bar": "2026-09-01",
        "trend_drift": 0,
        "weight_drift": 0,
        "leverage_violations": 0,
        "matched": [{"symbol": "BTC", "deviation": -0.01}],
    }
    payload.update(over)
    (tmp_path / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def test_f4_drift_reads_real_field_names(monkeypatch, tmp_path):
    """回归：trend_drift/weight_drift/leverage_violations 是计数字段，
    早先版本找顶层 'drift' 键，导致永远报『字段缺失』。"""
    from backend.services import trend_e1_f4_gate as F4

    monkeypatch.setattr(F4, "DATA_DIR", tmp_path)
    monkeypatch.setenv("TREND_E1_F4_MAX_DRIFT", "0.02")
    _write_drift(tmp_path, matched=[{"symbol": "BTC", "deviation": -0.015}])

    d = F4._check_drift()
    assert d["ok"] is True
    assert d["trend_drift"] == 0
    assert d["max_weight_deviation"] == 0.015
    assert "raw_keys" not in d  # 不得再走「字段缺失」分支


def test_f4_drift_blocks_leverage_violation(monkeypatch, tmp_path):
    """线上真实场景：方向零漂移但有 10x 杠杆仓位，小额实盘必须挡住。"""
    from backend.services import trend_e1_f4_gate as F4

    monkeypatch.setattr(F4, "DATA_DIR", tmp_path)
    monkeypatch.setenv("TREND_E1_F4_MAX_DRIFT", "0.02")
    _write_drift(tmp_path, weight_drift=1, leverage_violations=2)

    d = F4._check_drift()
    assert d["ok"] is False
    assert d["trend_drift"] == 0  # 方向本身是干净的
    assert d["leverage_violations"] == 2
    assert "leverage_violations" in d["reason"]


def test_f4_drift_blocks_direction_and_weight(monkeypatch, tmp_path):
    from backend.services import trend_e1_f4_gate as F4

    monkeypatch.setattr(F4, "DATA_DIR", tmp_path)
    monkeypatch.setenv("TREND_E1_F4_MAX_DRIFT", "0.02")
    _write_drift(tmp_path, trend_drift=3,
                 matched=[{"symbol": "BTC", "deviation": -0.141}])

    d = F4._check_drift()
    assert d["ok"] is False
    assert d["trend_drift"] == 3
    assert d["max_weight_deviation"] == 0.141
    assert "trend_drift=3" in d["reason"]
    assert "0.1410" in d["reason"]


def test_f4_drift_aggregates_worst_account(monkeypatch, tmp_path):
    """latest.json 只留最后一个账户，多账户须从 e1_last_run 取最差。"""
    import json

    from backend.services import trend_e1_f4_gate as F4

    monkeypatch.setattr(F4, "DATA_DIR", tmp_path)
    monkeypatch.setenv("TREND_E1_F4_MAX_DRIFT", "0.02")
    _write_drift(tmp_path)  # 账户 14 干净
    (tmp_path / "e1_last_run.json").write_text(json.dumps({
        "accounts": {
            "14": {"drift": {"trend_drift": 0, "weight_drift": 0,
                             "leverage_violations": 0}},
            "15": {"drift": {"trend_drift": 2, "weight_drift": 1,
                             "leverage_violations": 0}},
        }
    }, ensure_ascii=False), encoding="utf-8")

    d = F4._check_drift()
    assert d["ok"] is False
    assert d["accounts_checked"] == 2
    assert d["trend_drift"] == 2  # 取最差账户


def test_basis_entry_rejects_small_spread(monkeypatch):
    from backend.services.arbitrage import basis_entry_gate as BG

    monkeypatch.setattr(
        "backend.services.allocation.capital_allocator.promotion_frozen",
        lambda: {"frozen": False},
    )
    monkeypatch.setattr(
        "backend.services.allocation.capital_allocator.research_notional_for",
        lambda sid: {"notional_usd": 100.0, "stage": "small"},
    )
    monkeypatch.setenv("BASIS_ENTRY_MIN_ABS_PCT", "0.15")
    r = BG.basis_entry_allowed(basis_pct=0.05)
    assert r["allowed"] is False


def test_basis_entry_allows(monkeypatch):
    from backend.services.arbitrage import basis_entry_gate as BG

    monkeypatch.setattr(
        "backend.services.allocation.capital_allocator.promotion_frozen",
        lambda: {"frozen": False},
    )
    monkeypatch.setattr(
        "backend.services.allocation.capital_allocator.research_notional_for",
        lambda sid: {"notional_usd": 100.0, "stage": "small"},
    )
    r = BG.basis_entry_allowed(basis_pct=0.5)
    assert r["allowed"] is True
    assert r["notional_usd"] == 100.0


def test_apr_from_8h():
    from backend.services.cashflow.funding_tail_harvest import _apr_from_8h_rate
    # 0.05% per 8h → ~54.75% APR
    assert abs(_apr_from_8h_rate(0.0005) - 0.0005 * 3 * 365) < 1e-9


def test_apply_promotion_stage_respects_freeze(monkeypatch):
    from backend.services import promotion_scan_service as PS

    monkeypatch.setattr(
        "backend.services.allocation.capital_allocator.promotion_frozen",
        lambda: {"frozen": True},
    )
    assert PS.apply_promotion_stage("x", "canary") is False


def test_promotion_jobs_register(monkeypatch):
    from backend.services.allocation.jobs import register_promotion_jobs

    ids: List[str] = []

    class FakeSched:
        def add_interval_task(self, **kw):
            ids.append(kw.get("task_id"))

        def add_cron_task(self, **kw):
            ids.append(kw.get("task_id"))

    monkeypatch.setattr(
        "backend.services.analysis.scheduling.off_peak_cron",
        lambda h, m: {"hour": h, "minute": m},
    )
    out = register_promotion_jobs(FakeSched(), lambda n, f: f, lambda *a, **k: None)
    assert "capital_allocate" in out
    assert "funding_tail_scan" in out
    assert "v3_capital_allocate" in ids
