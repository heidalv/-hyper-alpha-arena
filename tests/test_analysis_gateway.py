# -*- coding: utf-8 -*-
"""v3 方向 2（p0-model-gateway）：ModelGateway / QuotaGuard / schemas / ledgers 评分 / 非峰时调度 纯逻辑单测。

不出网、不连库：三条传输用假传输替换，analysis_runs 落库与 QuotaGuard 用量流水用 monkeypatch 截断。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


# ───────────────────────────── fixtures ─────────────────────────────
class FakeTransport:
    """可编程假传输：outputs 为 list（依次弹出）或 callable(system, user) → text；raise_exc 触发失败。"""

    def __init__(self, name, outputs=None, configured=True, raise_exc=None, model=None):
        self.name = name
        self._outputs = list(outputs or [])
        self._configured = configured
        self._raise = raise_exc
        self._model = model or f"fake-{name}"
        self.calls = []

    def configured(self):
        return (True, "ok") if self._configured else (False, "未配置")

    def model_name(self):
        return self._model

    def status(self):
        ok, why = self.configured()
        return {"transport": self.name, "configured": ok, "detail": why, "model": self._model}

    def complete(self, system, user, *, max_tokens, temperature, timeout_s, task, **_kw):
        from backend.services.analysis.model_gateway import RawCompletion

        self.calls.append((system, user, task))
        if self._raise:
            raise self._raise
        out = self._outputs.pop(0) if self._outputs else "{}"
        text = out(system, user) if callable(out) else out
        return RawCompletion(text=text, input_tokens=100, output_tokens=50, model=self._model)


@pytest.fixture
def quiet_ledgers(monkeypatch):
    """截断 analysis_runs / llm_quota_usage 落库与回读，记录在内存。"""
    from backend.services.analysis import ledgers, quota_guard

    runs = []
    monkeypatch.setattr(ledgers, "record_run", lambda run: (runs.append(run), run.id)[1])
    monkeypatch.setattr(ledgers, "ensure_schema", lambda: None)
    monkeypatch.setattr(quota_guard.QuotaGuard, "_load", lambda self: None)

    def _record(self, transport, task, **kw):
        import time as _t

        now = int(_t.time() * 1000)
        with self._lock:
            self._prune((transport, quota_guard.task_class(task)), now).append(now)

    monkeypatch.setattr(quota_guard.QuotaGuard, "record", _record)
    return runs


def _brief(direction="bullish", strength=6, conf=0.7, factors=None, regime="trend_up"):
    return json.dumps({
        "direction": direction, "strength": strength, "confidence": conf, "regime": regime, "regime_confidence": 0.6,
        "bucket_weights": {"trend": 0.6, "cashflow": 0.3, "research": 0.1},
        "symbol_views": [{"symbol": "BTCUSDT", "direction": direction, "strength": strength, "horizon_hours": 24, "reason": "x"}],
        "key_factors": factors or ["资金费偏低", "OI 上升 3%", "BTC 站上 EMA200"], "risks": ["宏观数据"], "summary": "s",
    }, ensure_ascii=False)


def _gateway(a, b, arb=None, quiet=None):
    from backend.services.analysis.model_gateway import ModelGateway
    from backend.services.analysis.quota_guard import QuotaGuard

    tr = {"minimax": a, "glm_opencode": b}
    if arb is not None:
        tr[arb.name] = arb
    return ModelGateway(quota=QuotaGuard(), transports=tr)


# ───────────────────────────── schemas ─────────────────────────────
def test_schema_validate_daily_brief_ok_and_errors():
    from backend.services.analysis import schemas

    ok, errs = schemas.validate("daily_brief", json.loads(_brief()))
    assert ok and not errs
    ok, errs = schemas.validate("daily_brief", {"direction": "sideways", "strength": 12, "bucket_weights": {"trend": 0.9, "cashflow": 0.9}})
    assert not ok
    assert any("direction" in e for e in errs) and any("strength" in e for e in errs) and any("权重和" in e for e in errs)
    assert schemas.direction_to_int("bearish") == -1 and schemas.direction_to_int("Bullish") == 1 and schemas.direction_to_int("x") == 0
    assert "模板" in schemas.output_contract("event_impact") and "half_life_hours" in schemas.output_contract("event_impact")


# ───────────────────────────── compare / merge ─────────────────────────────
def test_compare_outputs_scoring_and_merge():
    from backend.services.analysis.model_gateway import compare_outputs, merge_outputs

    a = json.loads(_brief("bullish", 6, 0.7))
    b = json.loads(_brief("bullish", 5, 0.6, factors=["资金费偏低", "OI 上升", "ETH 弱于 BTC"]))
    c = compare_outputs(a, b)
    assert c["direction_agree"] and c["strength_diff"] == 1.0 and c["raw_score"] >= 0.7
    d = compare_outputs(a, json.loads(_brief("bearish", 5)))
    assert not d["direction_agree"] and d["raw_score"] < 0.5
    # 同向但强度差 6 → 强度分归零，仍可能低于阈值
    e = compare_outputs(a, json.loads(_brief("bullish", 0, factors=["完全不同的依据"])))
    assert e["raw_score"] < 0.7
    m = merge_outputs(a, b)
    assert m["direction"] == "bullish" and m["strength"] == 5.5 and abs(m["confidence"] - 0.65) < 1e-9
    assert len(m["key_factors"]) == 5  # 去重合并


def test_compare_outputs_neutral_neutral_is_not_directional_consensus():
    from backend.services.analysis.model_gateway import compare_outputs

    a = {"direction": "neutral", "strength": 3, "key_factors": ["超买", "缺图审", "资金费正"]}
    b = {"direction": "neutral", "strength": 2, "key_factors": ["超买", "缺图审", "因子偏空"]}
    c = compare_outputs(a, b)
    assert c["both_neutral"] is True
    assert c["direction_agree"] is False
    assert c["raw_score"] < 0.7
    long_ok = compare_outputs(
        {"direction": "bullish", "strength": 6, "key_factors": ["结构", "资金费"]},
        {"direction": "long", "strength": 7, "key_factors": ["结构", "OI"]},
    )
    assert long_ok["direction_agree"] is True and long_ok["raw_score"] >= 0.7


# ───────────────────────────── dual_call protocol ─────────────────────────────
def test_dual_call_agreement_yields_consensus(quiet_ledgers):
    a = FakeTransport("minimax", [_brief("bullish", 6)])
    b = FakeTransport("glm_opencode", [_brief("bullish", 5)])
    gw = _gateway(a, b, FakeTransport("deepseek"))
    res = gw.dual_call("daily_brief", "sys", "ctx", parallel=False)
    assert res.status == "ok" and res.accepted and res.consensus_score >= 0.7
    assert res.final["direction"] == "bullish" and res.arbiter is None
    # 盲评：两条主传输拿到同一份输入
    assert a.calls[0][1] == b.calls[0][1] == "ctx"
    roles = [r.role for r in quiet_ledgers]
    assert roles.count("primary") == 2 and roles.count("consensus") == 1
    cons = [r for r in quiet_ledgers if r.role == "consensus"][0]
    assert cons.consensus_score == res.consensus_score and cons.meta["accepted"] is True


def test_dual_call_disagreement_arbiter_sides_with_b(quiet_ledgers):
    a = FakeTransport("minimax", [_brief("bullish", 7)])
    b = FakeTransport("glm_opencode", [_brief("bearish", 6)])
    arb_out = json.dumps({"verdict": "B", "direction": "bearish", "strength": 6, "confidence": 0.7, "rationale": "r", "key_factors": ["x"]})
    arb = FakeTransport("deepseek", [arb_out])
    gw = _gateway(a, b, arb)
    res = gw.dual_call("daily_brief", "sys", "ctx", parallel=False, arbiter="deepseek")
    assert res.status == "ok" and res.accepted and res.consensus_score == 0.7
    assert res.final["direction"] == "bearish" and res.final["arbitration"]["verdict"] == "B"
    assert res.arbiter is not None and res.arbiter.role if hasattr(res.arbiter, "role") else True
    # 仲裁看到了两份结论
    assert "结论 A" in arb.calls[0][1] and "结论 B" in arb.calls[0][1]


def test_dual_call_both_neutral_does_not_accept_or_arbitrate(quiet_ledgers):
    a = FakeTransport("minimax", [_brief("neutral", 3, 0.4)])
    b = FakeTransport("glm_opencode", [_brief("neutral", 2, 0.45)])
    arb = FakeTransport("deepseek", [_brief("bullish", 6)])
    res = _gateway(a, b, arb).dual_call("daily_brief", "sys", "ctx", parallel=False)
    assert res.status == "ok" and not res.accepted
    assert (res.final or {}).get("direction") == "neutral"
    assert res.arbiter is None
    assert arb.calls == []
    assert any("中性" in n for n in res.notes)


def test_dual_call_disagreement_arbiter_neither_is_not_signal(quiet_ledgers):
    a = FakeTransport("minimax", [_brief("bullish", 7)])
    b = FakeTransport("glm_opencode", [_brief("bearish", 6)])
    arb = FakeTransport("deepseek", [json.dumps({"verdict": "neither", "direction": "neutral", "strength": 0, "confidence": 0.3, "rationale": "r", "key_factors": []})])
    res = _gateway(a, b, arb).dual_call("daily_brief", "sys", "ctx", parallel=False, arbiter="deepseek")
    assert res.status == "ok" and not res.accepted and res.final is None and res.consensus_score < 0.7


def test_dual_call_single_vote_is_degraded_never_accepted(quiet_ledgers):
    a = FakeTransport("minimax", [_brief("bullish", 9, conf=1.0)])
    b = FakeTransport("glm_opencode", raise_exc=RuntimeError("boom"))
    res = _gateway(a, b, FakeTransport("deepseek", configured=False)).dual_call("daily_brief", "sys", "ctx", parallel=False)
    assert res.status == "degraded" and not res.accepted and res.consensus_score <= 0.6
    assert res.final["direction"] == "bullish"
    assert any("boom" in n for n in res.notes)


def test_dual_call_substitutes_arbiter_for_unavailable_primary(quiet_ledgers):
    a = FakeTransport("minimax", configured=False)
    b = FakeTransport("glm_opencode", [_brief("bullish", 6)])
    arb = FakeTransport("glm_opencode_alt", [_brief("bullish", 5)])
    res = _gateway(a, b, arb).dual_call("daily_brief", "sys", "ctx", parallel=False)
    assert res.status == "ok" and res.accepted
    assert sorted(p.transport for p in res.primaries) == ["glm_opencode", "glm_opencode_alt"]
    assert any("顶替" in n for n in res.notes)


def test_dual_call_all_unavailable_is_skipped(quiet_ledgers, monkeypatch):
    from backend.services.analysis import model_gateway as mg

    alerts = []
    monkeypatch.setattr(mg.ModelGateway, "_alert_skipped", staticmethod(lambda task, notes: alerts.append(task)))
    res = _gateway(FakeTransport("minimax", configured=False), FakeTransport("glm_opencode", configured=False),
                   FakeTransport("deepseek", configured=False)).dual_call("daily_brief", "sys", "ctx", parallel=False)
    assert res.status == "skipped" and res.final is None and alerts == ["daily_brief"]


def test_call_schema_failure_and_auth_cooldown(quiet_ledgers):
    a = FakeTransport("minimax", ['{"direction": "sideways"}'])
    gw = _gateway(a, FakeTransport("glm_opencode", raise_exc=RuntimeError("401 Authentication Failed")))
    r = gw.call("daily_brief", "sys", "ctx", transport="minimax")
    assert not r.ok and r.json == {"direction": "sideways"} and "schema" in r.error
    r2 = gw.call("daily_brief", "sys", "ctx", transport="glm_opencode")
    assert not r2.ok and "Authentication" in r2.error
    ok, why = gw.available("glm_opencode")
    assert not ok and "冷却" in why
    assert gw.status()["transports"]["glm_opencode"]["configured"] is False


def test_extract_json_from_fenced_and_chatty_text():
    from backend.services.analysis.model_gateway import extract_json

    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('思考：{"x":1} 不对… 最终答案：{"direction": "bullish", "strength": 3}')["direction"] == "bullish"
    assert extract_json("没有 json") is None


# ───────────────────────────── QuotaGuard ─────────────────────────────
def test_quota_guard_budgets_and_peak(quiet_ledgers, monkeypatch):
    from backend.services.analysis.quota_guard import Budget, QuotaGuard, estimate_tokens, is_glm_peak, next_off_peak

    b = Budget(deep_daily=2, event_daily=3, light_daily=5, max_context_tokens=1000, max_output_tokens=500, calls_5h=100,
               calls_weekly=1000, allow_glm_peak=False)
    q = QuotaGuard(b)
    assert q.check("minimax", "daily_brief", est_context_tokens=100).ok
    assert q.check("minimax", "daily_brief", est_context_tokens=5000).action == "reject"
    assert q.check("minimax", "daily_brief", est_context_tokens=100, max_output_tokens=900).action == "reject"
    q.record("minimax", "daily_brief", model="m", input_tokens=1, output_tokens=1, latency_ms=1, ok=True)
    q.record("minimax", "daily_brief", model="m", input_tokens=1, output_tokens=1, latency_ms=1, ok=False)  # 失败也计
    d = q.check("minimax", "daily_brief", est_context_tokens=100)
    assert d.action == "degrade" and "今日已用 2/2" in d.reason
    # 不同任务类独立计
    assert q.check("minimax", "event_impact", est_context_tokens=100).ok
    # 峰时：周三 15:00 北京时间
    wed_15 = datetime(2026, 9, 2, 7, 0, tzinfo=timezone.utc)  # = 15:00 +08
    assert is_glm_peak(wed_15) and not is_glm_peak(wed_15 + timedelta(hours=4))
    assert not is_glm_peak(datetime(2026, 9, 5, 7, 0, tzinfo=timezone.utc))  # 周六
    assert q.check("glm_opencode", "daily_brief", est_context_tokens=100, when=wed_15).action == "degrade"
    assert q.check("glm_opencode", "event_impact", est_context_tokens=100, when=wed_15).ok  # 事件评估不受峰时限制
    assert q.check("minimax", "daily_brief", est_context_tokens=100, when=wed_15).action == "degrade"  # 日预算已满，与峰时无关
    assert next_off_peak(wed_15).hour == 18
    assert estimate_tokens("hello world") >= 2 and estimate_tokens("资金费率极端") >= 5


def test_quota_guard_5h_window(quiet_ledgers):
    from backend.services.analysis.quota_guard import Budget, QuotaGuard

    b = Budget(deep_daily=50, event_daily=50, light_daily=50, max_context_tokens=1000, max_output_tokens=500, calls_5h=2,
               calls_weekly=1000, allow_glm_peak=True)
    q = QuotaGuard(b)
    q.record("deepseek", "daily_brief", model="m", input_tokens=1, output_tokens=1, latency_ms=1, ok=True)
    q.record("deepseek", "event_impact", model="m", input_tokens=1, output_tokens=1, latency_ms=1, ok=True)
    d = q.check("deepseek", "adhoc", est_context_tokens=10)
    assert d.action == "degrade" and "5 小时窗" in d.reason
    snap = q.snapshot()
    assert snap["transports"]["deepseek"]["calls_5h"] == 2 and snap["budget"]["calls_5h"] == 2


# ───────────────────────────── ledgers scoring ─────────────────────────────
def test_score_direction_math():
    from backend.services.analysis.ledgers import _score_direction, _kline_base

    ret_bp, btc_bp, excess_bp, hit, brier = _score_direction(1, 100.0, 102.0, 50000.0, 50500.0, 0.8)
    assert round(ret_bp) == 200 and round(btc_bp) == 100 and round(excess_bp) == 100 and hit == 1 and abs(brier - 0.04) < 1e-9
    ret_bp, _, excess_bp, hit, brier = _score_direction(-1, 100.0, 102.0, 50000.0, 50500.0, 0.8)
    assert round(ret_bp) == -200 and round(excess_bp) == -100 and hit == 0 and abs(brier - 0.64) < 1e-9
    ret_bp, _, _, hit, _ = _score_direction(0, 100.0, 100.2, None, None, None)
    assert hit == 1 and ret_bp < 0
    assert _kline_base("BTCUSDT") == "BTC" and _kline_base("eth/usdt:usdt") == "ETH" and _kline_base("1000PEPEUSDT") == "1000PEPE"


# ───────────────────────────── scheduling ─────────────────────────────
def test_off_peak_cron_shifts_peak_hours(monkeypatch):
    from backend.services.analysis import scheduling

    monkeypatch.setattr(scheduling, "local_hour_to_scheduler_hour", lambda h: h)
    assert scheduling.off_peak_cron(7, 0) == {"hour": 7, "minute": 0}
    assert scheduling.off_peak_cron(15, 0) == {"hour": 18, "minute": 5}
    assert scheduling.off_peak_cron(4, 0, day_of_week="mon") == {"hour": 4, "minute": 0, "day_of_week": "mon"}


# ───────────────────────────── context pack rendering ─────────────────────────────
def test_context_pack_trims_to_budget():
    from backend.services.analysis.context_pack import ContextPack

    layers = {
        "market": {"symbols": {f"S{i}": {"last": i} for i in range(8)}, "correlation_pairs": [{"a": "x", "b": "y", "rho": 0.9}] * 20},
        "flows": {"events": [{"t": "news.high_impact", "title": "很长的标题" * 40}] * 40},
        "positions": {"open": [{"sym": "BTC"}] * 30},
        "performance": {"signal_sources": [{"source": "s"}] * 20},
    }
    pack = ContextPack(task="daily_brief", data_cutoff_ms=1, layers=layers)
    full = pack.to_prompt_text(10 ** 9)
    small = pack.to_prompt_text(1500)
    assert len(small) < len(full)
    assert pack.hash == ContextPack(task="x", data_cutoff_ms=2, layers=layers).hash  # hash 只依赖 layers
    assert "pack hash" in small
