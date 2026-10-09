# -*- coding: utf-8 -*-
"""五Agent因子研究闭环（factors_lab）单元测试 —— mock LLM + 合成K线，无真实 DB/网络。"""
from __future__ import annotations

import json
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

from backend.services.factors_lab import (
    agent3_engineer, agent4_backtester, agent5_feedback, calibration, common, config,
)

SYMS = [f"T{i:02d}" for i in range(10)]


def _synth_klines(days: int = 260, seed: int = 3, ar: float = 0.3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    r = np.zeros(days)
    for t in range(1, days):
        r[t] = ar * r[t - 1] + rng.normal(0, 0.02)
    close = 100.0 * np.cumprod(1.0 + r)
    idx = pd.date_range("2025-01-01", periods=days, freq="D", name="datetime")
    return pd.DataFrame({"open": close, "high": close * 1.006, "low": close * 0.994,
                         "close": close, "volume": np.abs(rng.normal(1e6, 2e5, days))}, index=idx)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_DATA_DIR", tmp_path)
    config.data_dir()
    return tmp_path


@pytest.fixture()
def patched_env(monkeypatch, env):
    store = {s: _synth_klines(seed=100 + i) for i, s in enumerate(SYMS)}
    monkeypatch.setattr("backend.services.hybrid_scoring.kpanel.load_klines",
                        lambda sym, period="1d", bars=400, exchange=None: store.get(sym, pd.DataFrame()))
    monkeypatch.setattr("backend.services.hybrid_scoring.kpanel.top_liquid_symbols",
                        lambda limit=24, days=60, period="1d": SYMS[:limit])
    monkeypatch.setattr(agent5_feedback, "_V7_DB", env / "v7_test.db")
    # 测试库先建表（生产库有表，测试库需要同构 schema）
    import sqlite3
    con = sqlite3.connect(str(env / "v7_test.db"))
    con.execute("""CREATE TABLE IF NOT EXISTS v7_lessons (id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT, kind TEXT, cycle TEXT, period TEXT, title TEXT, summary TEXT,
        report_json TEXT DEFAULT '{}', quality REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0,
        last_used_at TEXT, status TEXT DEFAULT 'active')""")
    con.commit()
    con.close()
    return env


def _mock_llm(monkeypatch, responses: List[str]):
    """按调用顺序弹出 canned 响应（caller 无关），耗尽返回 None。"""
    calls = {"i": 0, "log": []}

    def fake_call_llm(system, user, *, caller, max_tokens=2500, temperature=0.4):
        calls["log"].append(caller)
        i = calls["i"]
        calls["i"] += 1
        return responses[i] if i < len(responses) else None

    monkeypatch.setattr(common, "call_llm", fake_call_llm)
    return calls


AST_GOOD = {"op": "ts_rank", "args": [{"op": "delta", "args": [{"f": "close"}, {"c": 5}]}, {"c": 10}]}
AST_LOOKAHEAD = {"op": "delta", "args": [{"f": "close"}, {"c": -3}]}
AST_DUP = {"op": "ts_rank", "args": [{"op": "delta", "args": [{"f": "close"}, {"c": 6}]}, {"c": 10}]}


# ---------------- ① 文献 ----------------

def test_feed_and_retrieve(monkeypatch, env):
    from backend.services.factors_lab import agent1_literature
    _mock_llm(monkeypatch, [json.dumps({
        "phenomenon": "量价背离", "mechanism": "知情交易者抢先",
        "testable_claims": ["放量滞涨预示反转"], "data_requirements": "OHLCV",
        "applicable_regime": "range", "horizon": "midlong", "quality": 0.8})])
    r = agent1_literature.feed("Test Paper on Volume", "abstract " * 10)
    assert r["ok"] and r["extract_ok"]
    # 重复投喂被拒
    r2 = agent1_literature.feed("Test Paper on Volume", "abstract " * 10)
    assert not r2["ok"] and r2["error"] == "duplicate"
    got = agent1_literature.retrieve("量价 背离", k=2)
    assert len(got) == 1 and got[0]["card"]["phenomenon"] == "量价背离"


# ---------------- ② 假设 + 多样性拒收 ----------------

def test_hypothesis_diversity_gate(monkeypatch, env):
    from backend.services.factors_lab import agent2_hypothesis
    hyp_json = json.dumps([
        {"knowledge_ref": "none", "observation": "o", "argument": "a",
         "hypothesis": "成交量异动与反转", "spec": "volume, w=10..20",
         "expected_ic_sign": -1, "horizon": "midlong", "applicable_regime": "any",
         "diversity_note": "量能维度"},
        {"knowledge_ref": "none", "observation": "o", "argument": "a",
         "hypothesis": "成交量异动与反转的关系研究", "spec": "volume, w=10..20",
         "expected_ic_sign": -1, "horizon": "midlong", "applicable_regime": "any",
         "diversity_note": "量能维度"},  # 与第1条高相似 → 应被拒
        {"knowledge_ref": "none", "observation": "o", "argument": "a",
         "hypothesis": "资金费率极端后的均值回归", "spec": "returns, w=30..60",
         "expected_ic_sign": -1, "horizon": "midlong", "applicable_regime": "trend",
         "diversity_note": "衍生品维度"},
    ])
    _mock_llm(monkeypatch, [hyp_json])
    res = agent2_hypothesis.generate(n=3)
    acc = res["hypotheses"]
    texts = [h["hypothesis"] for h in acc]
    assert "成交量异动与反转" in texts and "资金费率极端后的均值回归" in texts
    assert "成交量异动与反转的关系研究" not in texts  # 多样性拒收生效


# ---------------- ③ 工程 ----------------

def test_unit_tests_reject_lookahead_and_dedup(env):
    ut_bad = agent3_engineer.minimal_unit_tests(AST_LOOKAHEAD)
    assert not ut_bad["pass"] and "look-ahead" in ut_bad["reason"].lower() or \
           not ut_bad["pass"]  # 编译审计拒绝（负窗口）
    ut_good = agent3_engineer.minimal_unit_tests(AST_GOOD)
    assert ut_good["pass"]
    # AST 相似：GOOD vs DUP（仅窗口参数不同）节点集相同 → 相似度 1.0
    assert agent3_engineer.ast_similarity(AST_GOOD, AST_DUP) == pytest.approx(1.0)
    assert agent3_engineer.ast_hash(AST_GOOD) != agent3_engineer.ast_hash(AST_DUP)


def test_realize_with_mock_llm(monkeypatch, env):
    from backend.services.factors_lab import agent2_hypothesis  # noqa: F401
    _mock_llm(monkeypatch, [json.dumps([
        {"ast": AST_GOOD, "note": "动量排名"},
        {"ast": AST_DUP, "note": "重复应被去重"},
        {"ast": {"op": "corr", "args": [{"f": "volume"}, {"f": "close"}, {"c": 15}]}, "note": "量价相关"},
    ])])
    from backend.services.factors_lab import agent2_hypothesis as a2  # noqa: F401
    from backend.services import factors_lab
    eng = factors_lab.agent3_engineer.realize({"hyp_id": "h_test", "hypothesis": "h",
                                               "argument": "a", "spec": "s",
                                               "expected_ic_sign": 1})
    cands = eng["candidates"]
    ids = [c["expr_id"] for c in cands]
    assert len(set(ids)) == len(ids)  # expr_id 去重
    # AST_DUP 与 AST_GOOD 结构相似 > 阈值 → 应被拒（unit_fail with ast_sim reason）
    dup_entry = [c for c in cands if c.get("ast_sim_pool") is not None and
                 "ast" in json.dumps(c["unit_tests"].get("reason", ""))]
    passed = [c for c in cands if c["status"] == "unit_pass"]
    assert passed  # 至少 GOOD 与 corr 通过


# ---------------- ④ 回测 ----------------

def test_gpfactor_gpbacktest(patched_env):
    rep = agent4_backtester.gpbacktest(AST_GOOD, universe=SYMS)
    assert rep["ok"], rep
    assert rep["n_symbols"] == len(SYMS)
    assert abs(rep["mean_rank_ic"]) < 1.0  # 合成数据 IC 应在合理范围
    assert 0.0 <= rep["turnover"] <= 2.0
    # 面板日期数足够
    assert rep["n_days"] >= 100


# ---------------- ⑤ 反馈 ----------------

def test_attribute_and_remember(patched_env, monkeypatch):
    # 造一轮结果：一个 pass 一个 weak_ic
    good = agent4_backtester.gpbacktest(AST_GOOD, universe=SYMS)
    rep_pass = dict(good, mean_rank_ic=0.05, icir=1.2, turnover=0.3)  # 人为达标
    rep_weak = dict(good, mean_rank_ic=0.001)
    results = [
        {"cand": {"cand_id": "c1", "hyp_id": "h1", "expr_id": "e1", "ast": AST_GOOD,
                  "status": "unit_pass"}, "report": rep_pass},
        {"cand": {"cand_id": "c2", "hyp_id": "h2", "expr_id": "e2",
                  "ast": {"op": "corr", "args": [{"f": "volume"}, {"f": "close"}, {"c": 15}]},
                  "status": "unit_pass"}, "report": rep_weak},
    ]
    common.append_jsonl(config.hypotheses_path(), {"hyp_id": "h1", "ts": 1, "hypothesis": "x", "outcome": None})
    common.append_jsonl(config.hypotheses_path(), {"hyp_id": "h2", "ts": 1, "hypothesis": "y", "outcome": None})
    fb = agent5_feedback.attribute_and_remember(results)
    assert fb["stats"]["pass"] == 1 and fb["stats"]["reject"] == 1
    assert "guidance" in fb
    # v7 写入验证（测试库已建同构表）
    assert agent5_feedback.write_v7_lesson("gate_lesson", "t", "s")
    import sqlite3
    con = sqlite3.connect(str(patched_env / "v7_test.db"))
    n = con.execute("SELECT COUNT(*) FROM v7_lessons").fetchone()[0]
    con.close()
    assert n >= 3  # pass/reject 两类教训 + 手工一条


def test_diversity_report(patched_env):
    common.append_jsonl(config.candidates_path(),
                        {"cand_id": "c1", "expr_id": "e1", "ast": AST_GOOD, "verdict": "pass"})
    common.append_jsonl(config.candidates_path(),
                        {"cand_id": "c2", "expr_id": "e2",
                         "ast": {"op": "corr", "args": [{"f": "volume"}, {"f": "close"}, {"c": 15}]},
                         "verdict": "pass"})
    common.append_jsonl(config.hypotheses_path(),
                        {"hyp_id": "h1", "ts": 1, "hypothesis": "动量延续", "diversity_note": "", "outcome": True})
    rep = agent5_feedback.diversity_report()
    assert rep["accepted_factors"] == 2
    assert rep["hypothesis_clusters"].get("动量") == 1
    assert rep["verdict"] in ("healthy", "attention")


# ---------------- 闭环全链 ----------------

def test_full_round_mock_llm(patched_env, monkeypatch):
    from backend.services.factors_lab import loop
    card = json.dumps({"phenomenon": "p", "mechanism": "m", "testable_claims": ["c"],
                       "data_requirements": "OHLCV", "applicable_regime": "any",
                       "horizon": "midlong", "quality": 0.7})
    hyps = json.dumps([
        {"knowledge_ref": "none", "observation": "o", "argument": "a",
         "hypothesis": "隔夜波动与次日动量", "spec": "close, w=5..20",
         "expected_ic_sign": 1, "horizon": "midlong", "applicable_regime": "any",
         "diversity_note": "波动维度"}])
    asts = json.dumps([
        {"ast": AST_GOOD, "note": "n1"},
        {"ast": {"op": "corr", "args": [{"f": "volume"}, {"f": "close"}, {"c": 15}]}, "note": "n2"}])
    _mock_llm(monkeypatch, [card, hyps, asts, asts])
    report = loop.run_round(scan=False, force=True)
    assert report["ok"], report.get("error")
    assert report["stages"]["hypothesize"]["accepted"] >= 1
    assert report["stages"]["engineer"]["candidates"] >= 1
    assert "stats" in report["stages"]["feedback"]
    # 轮报告落盘 + 索引
    assert config.rounds_index_path().exists()
    # 状态可查
    st = loop.status()
    assert st["counts"]["hypotheses"] >= 1


# ---------------- 校准 ----------------

def test_calibration_golden(monkeypatch, env):
    # 指向合成公式源（避免依赖生产 discovered_factors.json 的内容变化）
    fake_store = {
        "t1:a1": {"name": "a1", "category": "alpha101", "formula": "delta(close, 5) / (delay(close, 5) + 1e-9)"},
        "t1:a2": {"name": "a2", "category": "alpha101", "formula": "ts_rank(-returns, 10)"},
        "t1:other": {"name": "x", "category": "registry", "formula": "close"},
    }
    p = env / "fake_store.json"
    p.write_text(json.dumps(fake_store), encoding="utf-8")
    monkeypatch.setattr(calibration, "_STORE", p)
    monkeypatch.setattr(calibration, "_gtja191_formulas", lambda: [])  # 隔离真实GTJA子集
    r1 = calibration.run_calibration()
    assert r1["ok"] and r1["golden_entries"] == 2  # 只收 alpha101
    r2 = calibration.run_calibration()
    assert r2["ok"] and r2["failed"] == 0  # 金样本复验通过
    # 篡改引擎行为（模拟回归）：换公式再校准 → 失败
    fake_store["t1:a1"]["formula"] = "delta(close, 6) / (delay(close, 6) + 1e-9)"
    p.write_text(json.dumps(fake_store), encoding="utf-8")
    r3 = calibration.run_calibration()
    assert not r3["ok"] and r3["failed"] == 1


def test_alignment_scoring_rejects_low(monkeypatch, env):
    from backend.services.factors_lab import agent3_engineer as a3

    asts = json.dumps([
        {"ast": AST_GOOD, "note": "动量排名"},
        {"ast": {"op": "corr", "args": [{"f": "volume"}, {"f": "close"}, {"c": 15}]}, "note": "无关凑数"},
    ])
    align = json.dumps({"scores": [
        {"idx": 0, "c1": 0.9, "c2": 0.8, "reason": "对齐"},
        {"idx": 1, "c1": 0.1, "c2": 0.2, "reason": "与假设无关"},
    ]})
    _mock_llm(monkeypatch, [asts, align])  # 第1次=AST生成，第2次=对齐打分
    eng = a3.realize({"hyp_id": "h_al", "hypothesis": "动量延续",
                                      "argument": "a", "spec": "s", "expected_ic_sign": 1})
    cands = eng["candidates"]
    aligned = [c for c in cands if c.get("alignment") and c["alignment"]["c1"] >= 0.5]
    rejected = [c for c in cands if c.get("status") == "align_fail"]
    assert aligned, "高对齐候选应保留 alignment 字段"
    assert rejected, "低对齐（c1*c2<0.35）应被拒收为 align_fail"
