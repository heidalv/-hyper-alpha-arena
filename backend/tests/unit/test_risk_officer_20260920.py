# -*- coding: utf-8 -*-
"""[轮131 2026-09-20] 风控官（有否决权）：宪法三项真的生效 / 名义硬顶 / 辩论联动 / 落库可审计。

## 背景（实测缺陷）
`constitutional_veto` 设计为"最后红线"，但调用点 `mlto/brain.py:1864` 只传了
`account_id/symbol/side/sl_pct` —— **equity_usd/margin_usd 从未传过**：
  · 单笔保证金占比硬顶（>20%）   → `if equity > 0 and margin > 0` 永不成立 ⇒ 空转
  · 日亏损硬停（≥6%）            → `if account_id is not None and equity > 0` ⇒ 空转
  · 单币/总敞口上限              → 同上 ⇒ 空转
只剩止损幅度校验在跑。本文件把这些检查**钉成可执行断言**，防止再次退化。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import risk_officer as RO  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("RISK_OFFICER_ENABLED", "RISK_OFFICER_MAX_NOTIONAL_PCT",
              "RISK_OFFICER_VETO_ON_DEBATE_REJECT", "RISK_OFFICER_DEBATE_RISK_FLOOR",
              "RISK_OFFICER_PAPER_DAILY_LOSS", "RISK_OFFICER_DEBATE_MAX_AGE_H"):
        monkeypatch.delenv(k, raising=False)
    yield


def _eval(**kw):
    args = dict(account_id=14, symbol="BTC", side="long", tier="mid", mode="paper",
                equity_usd=1000.0, planned_notional_usd=600.0, leverage=3.0, sl_pct=0.02,
                persist=False)
    args.update(kw)
    return RO.evaluate_open(**args)


# ───────────────── 1. 宪法三项（过去空转的那三项）─────────────────

def test_single_margin_cap_now_actually_binds():
    """单笔保证金 > 净值 20% 必须否决 —— 这正是过去因没传 equity/margin 而空转的一项。"""
    # 名义 1200 / 杠杆 3 = 保证金 400 = 净值 1000 的 40% > 20%
    r = _eval(planned_notional_usd=1200.0, leverage=3.0)
    assert r["allow"] is False
    assert "single_margin" in r["reason"], r["reason"]
    con = [c for c in r["checks"] if c["name"] == "constitution"][0]
    assert con["ok"] is False and "single_margin" in str(con["value"])


def test_single_margin_within_cap_allowed():
    # 名义 300 / 3 = 100 = 10% < 20%
    r = _eval(planned_notional_usd=300.0, leverage=3.0)
    assert r["allow"] is True, r["reason"]
    con = [c for c in r["checks"] if c["name"] == "constitution"][0]
    assert con["ok"] is True and con["value"]["margin_usd"] == pytest.approx(100.0)


def test_sl_pct_red_lines_still_enforced():
    r1 = _eval(sl_pct=0.001)          # 0.1% < MIN_SL_PCT(0.5%)
    assert r1["allow"] is False and "sl_too_tight" in r1["reason"]
    r2 = _eval(sl_pct=0.9)            # 90% > MAX_SL_PCT(50%)
    assert r2["allow"] is False and "sl_too_wide" in r2["reason"]


# ───────────────── 2. 名义暴露硬顶（不依赖杠杆口径）─────────────────

def test_notional_pct_cap(monkeypatch):
    monkeypatch.setenv("RISK_OFFICER_MAX_NOTIONAL_PCT", "2.0")
    r = _eval(equity_usd=1000.0, planned_notional_usd=2500.0, leverage=10.0)   # 2.5×
    assert r["allow"] is False and "notional_pct" in r["reason"]
    r2 = _eval(equity_usd=1000.0, planned_notional_usd=1500.0, leverage=10.0)  # 1.5×
    assert r2["allow"] is True, r2["reason"]


# ───────────────── 3. 辩论联动（主周期 reject + 风险共识低 ⇒ 否决）─────────────────

def test_debate_reject_with_low_risk_vetoes(monkeypatch):
    monkeypatch.setattr(
        "backend.services.mlto.brain_debate.debate_context",
        lambda sym, tier="", hours=3.0: {"verdict": "reduce", "primary_horizon": "intraday",
                                         "primary_verdict": "reject", "risk_min": 0.2,
                                         "horizon_verdicts": {"intraday": "reject"},
                                         "horizon_conflict": True, "ts": "2026-09-20 11:00:00"})
    r = _eval(planned_notional_usd=300.0)
    assert r["allow"] is False and "debate_reject" in r["reason"], r["reason"]
    assert r["debate"]["primary_horizon"] == "intraday"


def test_debate_reject_but_healthy_risk_does_not_veto(monkeypatch):
    """主周期 reject 但风险共识健康 ⇒ 不否决（辩论已通过 conviction×0.6 表达过，不重复惩罚）。"""
    monkeypatch.setattr(
        "backend.services.mlto.brain_debate.debate_context",
        lambda sym, tier="", hours=3.0: {"primary_horizon": "intraday", "primary_verdict": "reject",
                                         "risk_min": 0.9, "horizon_verdicts": {}})
    r = _eval(planned_notional_usd=300.0)
    assert r["allow"] is True, r["reason"]


def test_debate_veto_switch_off(monkeypatch):
    monkeypatch.setenv("RISK_OFFICER_VETO_ON_DEBATE_REJECT", "false")
    monkeypatch.setattr(
        "backend.services.mlto.brain_debate.debate_context",
        lambda sym, tier="", hours=3.0: {"primary_verdict": "reject", "risk_min": 0.1})
    r = _eval(planned_notional_usd=300.0)
    assert r["allow"] is True, "关掉辩论联动后不应因辩论否决"


# ───────────────── 4. 开关 / 落库 / 接线不许被摘掉 ─────────────────

def test_disabled_records_skip(monkeypatch):
    monkeypatch.setenv("RISK_OFFICER_ENABLED", "false")
    r = _eval(planned_notional_usd=1200.0, leverage=3.0)
    assert r["allow"] is True, "总闸关闭时不得否决"
    assert any(c.get("skipped") for c in r["checks"])


def test_decision_is_persisted_and_queryable():
    from sqlalchemy import text

    from backend.database.connection import analytics_engine

    r = RO.evaluate_open(account_id=14, symbol="TESTCOIN", side="long", tier="mid", mode="paper",
                         equity_usd=1000.0, planned_notional_usd=300.0, leverage=3.0,
                         sl_pct=0.02, persist=True)
    assert r["allow"] is True
    with analytics_engine.connect() as c:
        row = c.execute(text(
            "select allow, reason, inputs_json, checks_json from risk_officer_decisions "
            "where symbol='TESTCOIN' order by id desc limit 1")).fetchone()
    assert row is not None, "风控官判定必须落库（否则否决只存在于日志里）"
    assert row[0] is True
    assert "planned_notional_usd" in (row[2] or ""), "inputs_json 必须含输入实参（可复盘）"
    assert "constitution" in (row[3] or ""), "checks_json 必须含逐项检查"


def test_midlong_helpers_calls_risk_officer_before_order():
    """接线 ratchet：下单前必须过风控官，且传了 equity/名义/杠杆三个实参。"""
    src = (ROOT / "backend/services/full_auto/midlong_helpers.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_ro_eval"]
    assert calls, "midlong_helpers 里没有风控官调用（接线被摘掉了）"
    kwargs = {k.arg for k in calls[0].keywords}
    for need in ("equity_usd", "planned_notional_usd", "leverage", "mode"):
        assert need in kwargs, f"风控官调用缺实参 {need}（这正是过去空转的原因）"
    i_ro = src.find("_ro_eval(")
    i_prop = src.find("_extra_kwargs = {")
    assert 0 < i_ro < i_prop, "风控官必须在下单前（_extra_kwargs 之前）执行"


def test_status_and_daily_report_shape():
    st = RO.status(hours=1.0)
    for k in ("enabled", "max_notional_pct", "veto_on_debate_reject", "debate_risk_floor", "producer"):
        assert k in st
    rep = RO.daily_report(hours=1.0)
    for k in ("total", "allow", "veto", "veto_rate", "top_reasons", "config"):
        assert k in rep
