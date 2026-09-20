# -*- coding: utf-8 -*-
"""[轮129 2026-09-20] 六分析师数值化信号层：契约、可解释性、**"承诺 vs 交付"防复发**。

## 为什么必须有这个文件
用户原话：「之前说主脑终于能拿到新闻等数据了，结果都是假的，根本就没有做」。
历次"设计了没做"能长期存活，根因只有一个：**没有产物可查**。本文件把三件事钉死：
  1. 六域**契约**存在且每域都声明了数据源表（改契约必须改这里）；
  2. 每轮运行**每域要么产出数值信号、要么产出显式 missing 行**（禁止静默不产出）；
  3. 信号真的**进了主脑上下文**（context_pack 的 `analysts` 层，能被 prompt 渲染）——
     只在库里躺着不算"落地"。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.analysts import signals as S  # noqa: E402
from backend.services.analysts import service as SVC  # noqa: E402
from backend.services.analysts import scorers as SC  # noqa: E402


# ───────────────── 1. 契约 ─────────────────

def test_six_domains_declared_with_cn_names_and_sources():
    assert S.DOMAINS == ["fundamental", "technical", "sentiment", "flow", "macro", "kline_deep"], \
        "六域清单变了：这是承诺本身，改动必须同步 signals.py 顶部说明与用户指令记录"
    for d in S.DOMAINS:
        spec = S.DOMAIN_SPECS[d]
        assert spec.get("cn") and spec.get("role"), f"{d} 缺中文名/职责"
        assert spec.get("tables"), f"{d} 未声明数据源表（禁止无源信号）"
        assert spec.get("missing_when"), f"{d} 未声明缺源判定条件"
        assert isinstance(spec.get("known_gaps"), list), f"{d} 必须显式列出已知缺口（可为空表）"


def test_scorer_registry_matches_contract():
    assert set(SC.SCORERS) == set(S.DOMAINS), "scorer 注册表与契约不一致（有域没人算 / 有人算没声明）"


def test_missing_signal_requires_reason():
    """缺源必须写原因 —— 这是"不许静默缺失"的机制保证。"""
    with pytest.raises(ValueError):
        S.AnalystSignal(domain="macro", symbol="*", score=0.0, confidence=0.0,
                        data_quality=S.QUALITY_MISSING)


def test_score_and_confidence_clamped():
    sig = S.AnalystSignal(domain="flow", symbol="BTC", score=9.9, confidence=5.0,
                          data_quality=S.QUALITY_OK)
    assert sig.score == 1.0 and sig.confidence == 1.0
    sig2 = S.AnalystSignal(domain="flow", symbol="BTC", score=float("nan"), confidence=-3,
                           data_quality=S.QUALITY_OK)
    assert sig2.score == 0.0 and sig2.confidence == 0.0


# ───────────────── 2. 每域必须"交付或显式缺" ─────────────────

def test_every_domain_returns_signal_or_explicit_missing():
    """实跑六域：每域至少一个信号，且 missing 的必须带 reason + missing_sources。"""
    sigs = SC.score_all(["BTC", "ETH"])
    by_domain: dict[str, list] = {}
    for s in sigs:
        by_domain.setdefault(s.domain, []).append(s)
    assert set(by_domain) == set(S.DOMAINS), f"有域完全没有输出：{set(S.DOMAINS) - set(by_domain)}"
    for dom, group in by_domain.items():
        for s in group:
            assert S.SCORE_MIN <= s.score <= S.SCORE_MAX
            assert 0.0 <= s.confidence <= 1.0
            assert s.producer, f"{dom} 信号缺 producer（不可回溯）"
            if s.data_quality == S.QUALITY_MISSING:
                assert s.reason, f"{dom} missing 无原因"
                assert s.missing_sources, f"{dom} missing 未列出缺哪些源"
            else:
                assert s.evidence, f"{dom} 有值信号但没有 evidence（不可解释）"


def test_technical_as_of_is_sane_epoch():
    """[轮129 修正] `crypto_klines.timestamp` 是**秒**；首版除以 1000 得到 1970 年。"""
    sigs = [s for s in SC.score_technical(["BTC"]) if s.data_quality != S.QUALITY_MISSING]
    if not sigs:
        pytest.skip("无 K 线数据")
    as_of = sigs[0].as_of
    assert as_of.startswith("20"), f"technical.as_of 明显异常（epoch 单位错？）: {as_of}"


# ───────────────── 3. 落库 + 契约报告 ─────────────────

def test_run_once_persists_and_report_matches():
    r = SVC.run_once(["BTC", "ETH"], persist=True)
    assert r["enabled"] is True
    assert r["written"] == r["signals"] > 0, "run_once 必须把（含 missing）全部信号写库"
    rep = SVC.contract_report()
    assert rep["ok"] is True, f"契约报告读取失败：{rep.get('error')}"
    # 交付的域必须出现在报告里；未交付的域必须给出原因（而不是空白）
    delivered = {d for d, b in rep["delivered"].items() if b.get("ok", 0) + b.get("weak", 0) > 0}
    assert delivered, "48h 内没有任何域交付 —— 整层失效"
    for d in rep["undelivered"]:
        assert rep["missing_reasons"].get(d), f"{d} 被判未交付却没有给出原因"


def test_kline_deep_reports_missing_not_fake_neutral():
    """K线深度：产物表 0 行时必须报 missing（**不许**返回中性 0 冒充有信号）。

    现状（轮129 实测）：KlineAnalyst 每 24h 烧 ~222 次 LLM 调用但产物未落库。
    这条测试的作用是：**等哪天有人把产物落库了**，它会失败并提示更新契约与说明。
    """
    sigs = SC.score_kline_deep(["BTC"])
    assert sigs, "kline_deep 必须至少返回一条（missing 也算）"
    assert any(s.data_quality == S.QUALITY_MISSING for s in sigs), \
        "kline_ai_analysis_logs 现在应有产物了？请核对并更新 signals.py 的已知缺口说明"


# ───────────────── 4. 真的进了主脑上下文 ─────────────────

def test_context_pack_includes_analysts_layer():
    from backend.services.analysis import context_pack as cp

    pack = cp.build("midlong_thesis", symbols=["BTC", "ETH"])
    an = pack.layers.get("analysts")
    assert isinstance(an, dict) and an.get("available") is True, \
        "主脑上下文缺少 analysts 层 —— 信号等于没落地（这正是用户抱怨的'说了没做'）"
    assert an.get("symbols"), "analysts 层没有按标的的信号"
    txt = pack.to_prompt_text(200000)
    assert "analysts" in txt, "prompt 文本里没有 analysts 层（渲染阶段被丢掉）"


def test_analysts_layer_declares_missing_explicitly():
    from backend.services.analysis import context_pack as cp

    pack = cp.build("midlong_thesis", symbols=["BTC"])
    an = pack.layers.get("analysts") or {}
    if an.get("available"):
        assert isinstance(an.get("missing"), list), "缺失域必须以 missing 列表暴露给主脑，而不是静默省略"


# ───────────────── 5. 混合打分：一等 alpha 信号入权重（轮131）─────────────────

def test_blend_excludes_missing_domains_without_faking_neutral():
    """缺产的域必须**排除并归一化**，不能当 0 参与（那会假装"中性"拉低信号）。"""
    from backend.services.analysts import service as SVC

    r = SVC.blend_for_symbol("BTC", tier="mid", conviction=40.0)
    assert r["n_domains"] >= 1
    assert "kline_deep" not in r["contributions"], "缺产的域不得当成 0 参与混合"
    assert any("kline_deep" in m for m in r["missing"]), "缺产必须显式可见"


def test_blend_gain_is_bounded(monkeypatch):
    """混合有界：|blended − before| ≤ before × gain（gain 默认 0.15）。"""
    from backend.services.analysts import service as SVC

    monkeypatch.setenv("ANALYST_BLEND_GAIN", "0.15")
    r = SVC.blend_for_symbol("BTC", tier="mid", conviction=50.0)
    assert abs(r["blended"] - r["conviction_before"]) <= 50.0 * 0.15 + 1e-6
    assert -1.0 <= r["analyst_score"] <= 1.0


def test_low_confidence_domain_gets_less_weight(monkeypatch):
    """低信心信号按比例减权（不是丢掉、也不是等权）。"""
    from backend.services.analysts import service as SVC

    monkeypatch.setattr(SVC, "latest_signals", lambda *a, **k: [
        {"domain": "technical", "symbol": "BTC", "score": 1.0, "confidence": 1.0,
         "data_quality": "ok", "n_samples": 1, "as_of": "", "evidence": {},
         "missing_sources": [], "reason": "", "ts": ""},
        {"domain": "flow", "symbol": "BTC", "score": -1.0, "confidence": 0.1,
         "data_quality": "ok", "n_samples": 1, "as_of": "", "evidence": {},
         "missing_sources": [], "reason": "", "ts": ""},
    ])
    r = SVC.blend_for_symbol("BTC", tier="mid", conviction=50.0)
    assert r["analyst_score"] > 0.3, r


def test_blend_shadow_persisted_and_report():
    from sqlalchemy import text

    from backend.database.connection import analytics_engine
    from backend.services.analysts import service as SVC

    r = SVC.blend_for_symbol("ETH", tier="mid", conviction=30.0)
    assert SVC.record_blend_shadow(r, applied=False) is True
    with analytics_engine.connect() as c:
        row = c.execute(text(
            "select symbol, conviction_before, analyst_score, applied from analyst_blend_shadow "
            "where symbol='ETH' order by id desc limit 1")).fetchone()
    assert row is not None and row[0] == "ETH" and row[3] is False
    rep = SVC.blend_shadow_report(hours=2.0)
    assert rep["ok"] is True and rep["n"] >= 1 and "domain_presence" in rep


def test_brain_wires_blend_shadow_and_apply_switch():
    """接线 ratchet：主脑必须调混合打分；默认**影子**（APPLY 不设时不得改 conviction）。"""
    import ast as _ast

    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8", errors="replace")
    names = set()
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.Call):
            f = node.func
            names.add(f.attr if isinstance(f, _ast.Attribute) else (f.id if isinstance(f, _ast.Name) else ""))
    assert "blend_for_symbol" in names, "brain.py 未调用混合打分（接线被摘掉）"
    assert "record_blend_shadow" in names, "brain.py 未落影子对照"
    assert 'os.getenv("ANALYST_BLEND_APPLY", "false")' in src, "生效开关默认值必须是 false（影子）"
