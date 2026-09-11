# -*- coding: utf-8 -*-
"""p2-agents-b 单测：EventImpact / ParamSearch / ExecutionQA + 实验卡闭环。

全部用构造数据，不碰真库。重点覆盖三类容易出错、且出错后**很难从结果上看出来**的地方：
  1. 统计正确性：Bonferroni 校正、时间序切分不泄漏未来、置信下界
  2. 判定语义：`inconclusive`（没数据）必须与 `fail`（没效果）区分
  3. 数据陷阱回归：跨交易所同名资产导致的假滑点（曾把 30 天均值从 5bp 拉到 42bp）
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List

import pytest


# ─────────────────────────── ParamSearch 统计 ───────────────────────────
def test_bonferroni_widens_z_with_more_trials():
    from backend.services.agents.param_search import _z_for

    assert _z_for(1) == pytest.approx(1.96)
    z5, z20 = _z_for(5), _z_for(20)
    assert z5 > 1.96, "扫 5 组必须比单次检验更严"
    assert z20 > z5, "扫得越多门槛越高"
    # α/k 的正态分位数：k=5 → α=0.01 双侧 → z≈2.576
    assert z5 == pytest.approx(2.576, abs=0.01)


def test_norm_ppf_matches_known_quantiles():
    from backend.services.agents.param_search import _norm_ppf

    assert _norm_ppf(0.975) == pytest.approx(1.9600, abs=1e-3)
    assert _norm_ppf(0.995) == pytest.approx(2.5758, abs=1e-3)
    assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)


def test_split_excess_preserves_time_order():
    """时间序不能随机切——随机切会把未来信息漏进训练集。"""
    from backend.services.agents.param_search import split_excess

    xs = list(range(100))
    train, test = split_excess(xs, 0.7)
    assert train == list(range(70))
    assert test == list(range(70, 100))
    assert max(train) < min(test), "训练集必须全部早于测试集"


def test_summarize_lower_bound_tightens_with_bigger_z():
    from backend.services.agents.param_search import summarize

    xs = [10.0, 12.0, 8.0, 15.0, 9.0, 11.0, 13.0, 7.0]
    loose = summarize(xs, z=1.96)
    strict = summarize(xs, z=3.0)
    assert loose["mean_bp"] == strict["mean_bp"]
    assert strict["lower_bp"] < loose["lower_bp"], "z 越大下界越保守"
    assert loose["hit_rate"] == 1.0


def test_summarize_refuses_single_sample():
    from backend.services.agents.param_search import summarize

    assert summarize([5.0])["mean_bp"] is None, "1 个样本算不出标准误，不能造数"


# ─────────────────────────── 指标求值 ───────────────────────────
def test_parse_scope_forms():
    from backend.services.experiments.metrics import parse_scope

    assert parse_scope("source:e5_2") == ("source", "e5_2")
    assert parse_scope("exec:7") == ("exec", "7")
    assert parse_scope("global") == ("global", "")
    assert parse_scope(None) == ("global", "")


def test_unknown_metric_is_not_ok_and_not_passed():
    from backend.services.experiments.metrics import evaluate_metric

    r = evaluate_metric({"metric": "no_such", "op": ">", "threshold": 1}, since_ms=0, until_ms=1)
    assert r["ok"] is False
    assert r["passed"] is None, "未知指标必须是'无结论'，不能算成不达标"
    assert "no_such" in r["detail"]["reason"]


def test_unknown_operator_rejected():
    from backend.services.experiments.metrics import evaluate_metric

    r = evaluate_metric({"metric": "signal_n", "op": "~=", "threshold": 1}, since_ms=0, until_ms=1)
    assert r["ok"] is False and r["passed"] is None


def test_metric_sample_shortage_is_inconclusive_not_failure(monkeypatch):
    """核心语义：样本不足 ≠ 不达标。混淆这两者会误杀好想法。"""
    import backend.services.experiments.metrics as M

    monkeypatch.setitem(M.METRIC_REGISTRY, "fake",
                        lambda scope, s, u, **kw: M._fail("样本仅 3 条", 3))
    r = M.evaluate_metric({"metric": "fake", "op": ">", "threshold": 0}, since_ms=0, until_ms=1)
    assert r["ok"] is False
    assert r["passed"] is None
    assert r["n"] == 3


def test_baseline_before_computes_difference(monkeypatch):
    """baseline=before 时 value 应是 after − before。"""
    import backend.services.experiments.metrics as M

    calls: List[tuple] = []

    def fake(scope, since, until, **kw):
        calls.append((since, until))
        # 第一次调用是当前窗口，第二次是基线窗口
        return {"value": 20.0 if len(calls) == 1 else 8.0, "n": 50, "ok": True, "detail": {}}

    monkeypatch.setitem(M.METRIC_REGISTRY, "fake", fake)
    r = M.evaluate_metric({"metric": "fake", "op": ">", "threshold": 5, "baseline": "before"},
                          since_ms=1000, until_ms=2000)
    assert r["value"] == pytest.approx(12.0), "20 − 8 = 12"
    assert r["passed"] is True
    assert r["detail"]["before"] == 8.0 and r["detail"]["after"] == 20.0
    assert calls[1] == (0, 1000), "基线窗口必须是紧邻的等长区间"


def test_baseline_shortage_makes_whole_metric_inconclusive(monkeypatch):
    import backend.services.experiments.metrics as M

    n = {"i": 0}

    def fake(scope, since, until, **kw):
        n["i"] += 1
        if n["i"] == 1:
            return {"value": 20.0, "n": 50, "ok": True, "detail": {}}
        return M._fail("基线窗口没数据", 2)

    monkeypatch.setitem(M.METRIC_REGISTRY, "fake", fake)
    r = M.evaluate_metric({"metric": "fake", "op": ">", "threshold": 5, "baseline": "before"},
                          since_ms=1000, until_ms=2000)
    assert r["ok"] is False and r["passed"] is None


@pytest.mark.parametrize("results,want_verdict", [
    ([(True, True), (True, True)], "pass"),
    ([(True, True), (True, False)], "fail"),
    ([(True, True), (False, None)], "inconclusive"),
    ([(False, None), (False, None)], "inconclusive"),
])
def test_expected_metrics_verdict_matrix(monkeypatch, results, want_verdict):
    import backend.services.experiments.metrics as M

    seq = list(results)

    def fake_eval(spec, *, since_ms, until_ms):
        ok, passed = seq.pop(0)
        return {"metric": "m", "scope": "s", "op": ">", "threshold": 1,
                "value": 5 if ok else None, "n": 100 if ok else 0,
                "ok": ok, "passed": passed, "detail": {"reason": "样本不足" if not ok else ""}}

    monkeypatch.setattr(M, "evaluate_metric", fake_eval)
    out = M.evaluate_expected_metrics([{}, {}], since_ms=0, until_ms=1)
    assert out["verdict"] == want_verdict


def test_empty_metrics_is_inconclusive():
    from backend.services.experiments.metrics import evaluate_expected_metrics

    out = evaluate_expected_metrics([], since_ms=0, until_ms=1)
    assert out["verdict"] == "inconclusive"


def test_exec_scope_all_means_whole_book(monkeypatch):
    """未配置 ledger 账户时 scope 是 exec:all，不能当成非法账户 id 报错。"""
    import backend.services.experiments.metrics as M

    seen: Dict[str, Any] = {}

    def fake_stats(*, account_id, since_ms, until_ms):
        seen["acct"] = account_id
        return {"n_orders": 500, "median_slippage_bp": 5.0, "notes": []}

    monkeypatch.setattr(M, "_exec_stats", lambda a, s, u: fake_stats(account_id=a, since_ms=s, until_ms=u))
    r = M.m_slippage_bp("exec:all", 0, 1, min_n=10)
    assert r["ok"] is True and r["value"] == 5.0
    assert seen["acct"] is None, "all 应转成不限账户"


# ─────────────────────────── 实验卡生命周期 ───────────────────────────
def test_changes_config_detects_apply_flag():
    from backend.services.experiments.lifecycle import _changes_config

    assert _changes_config({"change": {"apply": True}}) is True
    assert _changes_config({"change": {"apply": False}}) is False
    assert _changes_config({"change": {}}) is False
    assert _changes_config({"change": '{"apply": true}'}) is True, "JSONB 可能以字符串回来"
    assert _changes_config({"change": "not json"}) is False


def test_extensions_reads_nested_result():
    from backend.services.experiments.lifecycle import _extensions

    assert _extensions({"result": {"extensions": 2}}) == 2
    assert _extensions({"result": '{"extensions": 3}'}) == 3
    assert _extensions({"result": None}) == 0
    assert _extensions({}) == 0


def test_config_changing_card_refuses_auto_start(monkeypatch):
    """改配置型卡必须人工确认——这条线不会因为 Agent 升到 advise 就被跨过。

    注意打桩方式：`lifecycle` 里写的是 `from backend.services.analysis import ledgers`，
    它解析的是**包属性**而不是 sys.modules 条目，所以必须直接改模块对象上的函数，
    替换 sys.modules 会被绕过（然后测试就会穿透到真库）。
    """
    import backend.services.analysis.ledgers as real_ledgers
    import backend.services.experiments.lifecycle as L

    card = {"id": "x1", "status": "proposed", "change": {"apply": True}}
    monkeypatch.setattr(real_ledgers, "list_experiments", lambda **kw: [card])

    def refuse(*a, **kw):
        raise AssertionError("改配置型卡不该被自动 start")

    monkeypatch.setattr(real_ledgers, "transition_experiment", refuse)
    res = L.start_experiment("x1", by="test")
    assert res["ok"] is False
    assert "人工确认" in res["reason"]

    # force=true 才放行
    calls: List[tuple] = []
    monkeypatch.setattr(real_ledgers, "transition_experiment",
                        lambda eid, st, **kw: (calls.append((eid, st)), True)[1])
    res2 = L.start_experiment("x1", by="test", force=True)
    assert res2["ok"] is True and calls == [("x1", "running")]


# ─────────────────────────── ExecutionQA ───────────────────────────
def test_base_symbol_strips_quote_suffixes():
    from backend.services.agents.execution_qa import _base_symbol

    assert _base_symbol("BTCUSDT") == "BTC"
    assert _base_symbol("ETH-USDT") == "ETH"
    assert _base_symbol("SOL/USD") == "SOL"
    assert _base_symbol("ONDO") == "ONDO", "不该把 ONDO 削成 ON"
    assert _base_symbol("BTC") == "BTC"


def _order(**kw) -> Dict[str, Any]:
    base = {
        "id": 1, "account_id": 1, "exchange": "binance", "symbol": "BTC", "side": "buy",
        "order_type": "market", "price": None, "quantity": 1.0, "filled_quantity": 1.0,
        "filled_price": 100.0, "fee": 0.04, "status": "filled", "close_reason": None,
        "trade_nature": "swing", "created_at": dt.datetime(2026, 8, 1, 12, 0, 0),
        "filled_at": dt.datetime(2026, 8, 1, 12, 0, 0),
    }
    base.update(kw)
    return base


def test_slippage_uses_same_exchange_reference(monkeypatch):
    """回归：跨所同名资产。ON 在 okx 约 72 美元、在 binance 约 0.23 美元（372 倍）。
    基准价按 exchange 分键前，这类订单会产出 ±90000bp 的假滑点。"""
    import backend.services.agents.execution_qa as E

    orders = [_order(exchange="binance", symbol="ON", filled_price=0.2354)]
    monkeypatch.setattr(E, "load_orders", lambda *a, **kw: orders)
    minute = int(dt.datetime(2026, 8, 1, 12, 0, 0).timestamp()) // 60 * 60
    monkeypatch.setattr(E, "_minute_closes", lambda *a, **kw: {
        ("binance", "ON"): {minute: 0.2331},      # 正确基准
        ("okx", "ON"): {minute: 85.33},           # 同名不同资产，必须不被选中
    })
    st = E.execution_stats(since_ms=0, until_ms=10 ** 13)
    assert st["n_slippage_samples"] == 1
    # (0.2354 − 0.2331)/0.2331 ≈ 98.7bp，而非拿 okx 价算出的 −9972bp
    assert st["median_slippage_bp"] == pytest.approx(98.7, abs=1.0)


def test_absurd_slippage_is_dropped_with_note(monkeypatch):
    import backend.services.agents.execution_qa as E

    minute = int(dt.datetime(2026, 8, 1, 12, 0, 0).timestamp()) // 60 * 60
    orders = [_order(id=i, filled_price=100.5) for i in range(5)]
    orders.append(_order(id=99, filled_price=90000.0))     # 基准取错的典型形态
    monkeypatch.setattr(E, "load_orders", lambda *a, **kw: orders)
    monkeypatch.setattr(E, "_minute_closes", lambda *a, **kw: {("binance", "BTC"): {minute: 100.0}})
    st = E.execution_stats(since_ms=0, until_ms=10 ** 13)
    assert st["n_slippage_samples"] == 5, "荒谬值必须被剔除"
    assert st["median_slippage_bp"] == pytest.approx(50.0, abs=0.1)
    assert any("基准价异常" in n for n in st["notes"])


def test_limit_order_uses_its_own_price_as_reference(monkeypatch):
    import backend.services.agents.execution_qa as E

    orders = [_order(order_type="limit", price=100.0, filled_price=100.2)]
    monkeypatch.setattr(E, "load_orders", lambda *a, **kw: orders)
    called = {"k": False}

    def no_kline(*a, **kw):
        called["k"] = True
        return {}

    monkeypatch.setattr(E, "_minute_closes", no_kline)
    st = E.execution_stats(since_ms=0, until_ms=10 ** 13)
    assert st["median_slippage_bp"] == pytest.approx(20.0, abs=0.1)
    assert called["k"] is False, "限价单有委托价就不该去查 K 线"


def test_sell_side_slippage_sign(monkeypatch):
    """卖便宜了也是吃亏，符号必须为正。"""
    import backend.services.agents.execution_qa as E

    orders = [_order(side="sell", order_type="limit", price=100.0, filled_price=99.0)]
    monkeypatch.setattr(E, "load_orders", lambda *a, **kw: orders)
    monkeypatch.setattr(E, "_minute_closes", lambda *a, **kw: {})
    st = E.execution_stats(since_ms=0, until_ms=10 ** 13)
    assert st["median_slippage_bp"] == pytest.approx(100.0, abs=0.1)


def test_reject_and_partial_rates(monkeypatch):
    import backend.services.agents.execution_qa as E

    orders = (
        [_order(id=i, order_type="limit", price=100.0) for i in range(6)]
        + [_order(id=10, status="rejected", close_reason="rejected")]
        + [_order(id=11, order_type="limit", price=100.0, filled_quantity=0.4, quantity=1.0)]
        + [_order(id=12, status="pending", filled_price=None)]
    )
    monkeypatch.setattr(E, "load_orders", lambda *a, **kw: orders)
    monkeypatch.setattr(E, "_minute_closes", lambda *a, **kw: {})
    st = E.execution_stats(since_ms=0, until_ms=10 ** 13)
    assert st["n_orders"] == 9 and st["n_rejected"] == 1
    assert st["reject_rate"] == pytest.approx(1 / 9, abs=1e-4)
    assert st["n_partial"] == 1
    assert st["partial_rate"] == pytest.approx(1 / 7, abs=1e-4)


def test_fee_bp_from_notional(monkeypatch):
    import backend.services.agents.execution_qa as E

    orders = [_order(order_type="limit", price=100.0, filled_price=100.0,
                     filled_quantity=2.0, fee=0.1)]
    monkeypatch.setattr(E, "load_orders", lambda *a, **kw: orders)
    monkeypatch.setattr(E, "_minute_closes", lambda *a, **kw: {})
    st = E.execution_stats(since_ms=0, until_ms=10 ** 13)
    assert st["avg_fee_bp"] == pytest.approx(5.0, abs=0.01)   # 0.1/200 = 5bp


def test_exec_quality_scoring_needs_samples(monkeypatch):
    import backend.services.agents.execution_qa as E

    monkeypatch.setattr(E, "execution_stats",
                        lambda **kw: {"median_slippage_bp": 3.0, "n_slippage_samples": 4})
    row = {"prediction": {"side": "below", "threshold_bp": 8.0, "account_id": None},
           "created_ms": 1000, "expires_ms": 2000}
    assert E.score_exec_quality(row) is None, "样本 < 10 应保持 open，不能造分"

    monkeypatch.setattr(E, "execution_stats",
                        lambda **kw: {"median_slippage_bp": 3.0, "n_slippage_samples": 40})
    r = E.score_exec_quality(row)
    assert r["score"] == 1.0 and r["outcome"]["actual_side"] == "below"

    monkeypatch.setattr(E, "execution_stats",
                        lambda **kw: {"median_slippage_bp": 30.0, "n_slippage_samples": 40})
    assert E.score_exec_quality(row)["score"] == 0.0


# ─────────────────────────── EventImpact ───────────────────────────
def test_event_impact_flip_detection(monkeypatch):
    """显著性翻转是这个 Agent 的核心价值：一次性报告看不出来。"""
    import backend.services.agents.event_impact as EI

    now = EI.now_ms()
    reports = {
        "position.oi_jump": {"event_type": "position.oi_jump", "n_used": 400, "n_events_raw": 400,
                             "significant": False, "optimal_hold_h": 67, "mean_at_opt": 0.001,
                             "mean_lo_at_opt": -0.02, "hit_rate": 0.5,
                             "wilson_lo": 0.4, "wilson_hi": 0.6, "significant_horizons": []},
    }
    monkeypatch.setattr(EI, "load_reports", lambda refresh=False: (reports, now))
    monkeypatch.setattr(EI, "read_latest", lambda aid: {"findings": {"types": [
        {"event_type": "position.oi_jump", "significant": True, "conclusive": True,
         "n_used": 345, "mean_at_opt": 0.099},
    ]}})
    agent = EI.EventImpactAgent()
    f = agent.analyze([])
    assert len(f["flips"]) == 1
    flip = f["flips"][0]
    assert flip["from"] is True and flip["to"] is False
    assert flip["n_used_before"] == 345 and flip["n_used_now"] == 400
    # 翻转应触发"下架复核"实验卡
    advice = agent.advise(f)
    assert any("复核" in a.params["title"] for a in advice)


def test_event_impact_thin_sample_not_conclusive(monkeypatch):
    import backend.services.agents.event_impact as EI

    reports = {"announcement.listing": {"event_type": "announcement.listing", "n_used": 14,
                                        "n_events_raw": 14, "significant": True,
                                        "optimal_hold_h": 4, "mean_at_opt": 0.05,
                                        "mean_lo_at_opt": 0.01, "hit_rate": 0.8,
                                        "wilson_lo": 0.5, "wilson_hi": 0.95,
                                        "significant_horizons": [4]}}
    monkeypatch.setattr(EI, "load_reports", lambda refresh=False: (reports, EI.now_ms()))
    monkeypatch.setattr(EI, "read_latest", lambda aid: None)
    agent = EI.EventImpactAgent()
    f = agent.analyze([])
    assert f["types"][0]["conclusive"] is False, "n=14 < min_n=30 不该下结论"
    assert f["significant"] == [], "样本不足的显著性不算数"
    assert agent.predict(f) == []


def test_event_impact_stale_report_blocks_predictions(monkeypatch):
    """报告过期就不该拿陈旧结论去赌未来。"""
    import backend.services.agents.event_impact as EI

    old = EI.now_ms() - 10 * 86400 * 1000
    reports = {"x": {"event_type": "x", "n_used": 500, "n_events_raw": 500, "significant": True,
                     "optimal_hold_h": 4, "mean_at_opt": 0.05, "mean_lo_at_opt": 0.02,
                     "hit_rate": 0.7, "wilson_lo": 0.6, "wilson_hi": 0.8,
                     "significant_horizons": [4]}}
    monkeypatch.setattr(EI, "load_reports", lambda refresh=False: (reports, old))
    monkeypatch.setattr(EI, "read_latest", lambda aid: None)
    agent = EI.EventImpactAgent()
    errors: List[str] = []
    f = agent.analyze(errors)
    assert f["stale"] is True
    assert any("过期" in e for e in errors)
    assert agent.predict(f) == [] and agent.advise(f) == []


# ─────────────────────────── 基类实验卡落地 ───────────────────────────
class _StubAgent:
    """最小化 Agent，用来验证 base.run() 的实验卡分支。"""

    def __init__(self, mode):
        from backend.services.agents.base import ObservationAgent

        class S(ObservationAgent):
            agent_id = "stub_exp"
            max_mode = "advise"

            def analyze(self, errors):
                return {"ok": True}

            def advise(self, findings):
                from backend.services.agents.base import ACTION_PROPOSE_EXPERIMENT, Advice

                return [Advice(action=ACTION_PROPOSE_EXPERIMENT, target="t",
                               reason="r", params={
                                   "title": "T", "hypothesis": "H",
                                   "change": {"apply": False},
                                   "expected_metrics": [{"metric": "signal_n", "op": ">", "threshold": 1}],
                                   "window_hours": 24})]

        self.cls = S
        self.mode = mode


def test_observe_mode_never_writes_experiment_card(monkeypatch):
    """observe 模式只记录不落库——这是"没被验证过的 Agent 动不了任何东西"的底线。"""
    import backend.services.agents.base as B

    created: List[str] = []
    monkeypatch.setattr(B.ObservationAgent, "_propose_experiment",
                        lambda self, adv, errors: created.append("x") or "eid")
    monkeypatch.setattr(B, "credibility_of", lambda aid, days=60: {"n_scored": 0})
    monkeypatch.setattr(B, "gate_verdict", lambda cred, gate: {"passed": False, "reason": "无样本"})
    monkeypatch.setattr(B, "write_latest", lambda aid, payload: None)

    agent = _StubAgent("advise").cls(mode="advise")
    res = agent.run()
    assert res.mode == "observe", "可信度不足必须降级"
    assert created == [], "observe 模式不该落卡"
    assert res.experiments == []
    assert "仅记录不落库" in res.advice[0]["apply_note"]


def test_advise_mode_writes_experiment_card(monkeypatch):
    import backend.services.agents.base as B

    monkeypatch.setattr(B, "credibility_of", lambda aid, days=60: {"n_scored": 100})
    monkeypatch.setattr(B, "gate_verdict", lambda cred, gate: {"passed": True, "reason": "ok"})
    monkeypatch.setattr(B, "write_latest", lambda aid, payload: None)
    monkeypatch.setattr(B.ObservationAgent, "_propose_experiment",
                        lambda self, adv, errors: "exp-123")

    agent = _StubAgent("advise").cls(mode="advise")
    res = agent.run()
    assert res.mode == "advise"
    assert res.experiments == ["exp-123"]
    assert res.advice[0]["applied"] is True


def test_dry_run_never_writes_card(monkeypatch):
    import backend.services.agents.base as B

    monkeypatch.setattr(B, "credibility_of", lambda aid, days=60: {"n_scored": 100})
    monkeypatch.setattr(B, "gate_verdict", lambda cred, gate: {"passed": True, "reason": "ok"})
    calls: List[str] = []
    monkeypatch.setattr(B.ObservationAgent, "_propose_experiment",
                        lambda self, adv, errors: calls.append("x") or "e")

    agent = _StubAgent("advise").cls(mode="advise")
    res = agent.run(dry_run=True)
    assert calls == [] and res.experiments == []


def test_propose_experiment_requires_all_five_fields(monkeypatch):
    """五要素缺一不可——不许写"再观察观察"式的含糊建议。"""
    import backend.services.agents.base as B
    import backend.services.analysis.ledgers as real_ledgers

    seen: Dict[str, Any] = {}

    def create_experiment(**kw):
        seen.update(kw)
        if not kw.get("expected_metrics") or not kw.get("title"):
            return None
        return "eid"

    monkeypatch.setattr(real_ledgers, "create_experiment", create_experiment)

    agent = _StubAgent("advise").cls(mode="advise")
    good = B.Advice(action="propose_experiment", params={
        "title": "T", "hypothesis": "H", "change": {"apply": False},
        "expected_metrics": [{"metric": "signal_n", "op": ">", "threshold": 1}],
        "window_hours": 24})
    assert agent._propose_experiment(good, []) == "eid"
    assert seen["source"] == "stub_exp"

    bad = B.Advice(action="propose_experiment", params={"title": "T", "hypothesis": "H"})
    errs: List[str] = []
    assert agent._propose_experiment(bad, errs) is None


# ─────────────────────────── 注册完整性 ───────────────────────────
def test_all_six_agents_and_kinds_registered():
    from backend.services.agents.base import registered_agents
    from backend.services.agents.jobs import ALL_KINDS, ensure_registered

    ensure_registered()
    agents = registered_agents()
    for aid in ("signal_review", "anomaly", "timing", "event_impact", "param_search", "execution_qa"):
        assert aid in agents, f"{aid} 未注册"
    for kind in ("event_impact", "param_shift", "exec_quality"):
        assert kind in ALL_KINDS


def test_every_agent_kind_has_an_evaluator():
    """预测 kind 没有评估器 → 永远挂 open、48h 后判 void，等于白记。"""
    from backend.services.agents.jobs import ensure_evaluators, ensure_registered
    from backend.services.analysis.ledgers import _OUTCOME_EVALUATORS

    ensure_registered()
    ensure_evaluators()
    from backend.services.agents.base import get_agent, registered_agents

    for aid in registered_agents():
        agent = get_agent(aid)
        for kind in (agent.kinds or ()):
            assert kind in _OUTCOME_EVALUATORS or kind == "direction", \
                f"{aid} 的 kind={kind} 没有注册评估器"


def test_new_agents_capped_at_advise():
    """任何新 Agent 都不许有 act 权限。"""
    from backend.services.agents.base import get_agent
    from backend.services.agents.jobs import ensure_registered

    ensure_registered()
    for aid in ("event_impact", "param_search", "execution_qa"):
        assert get_agent(aid).max_mode == "advise"
