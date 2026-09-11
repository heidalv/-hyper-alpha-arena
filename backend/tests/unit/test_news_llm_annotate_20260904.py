# -*- coding: utf-8 -*-
"""新闻标注走系统级网关（2026-09-04）。

原实现只走 `get_llm_config()`，而该函数**要求 tenant_id**；新闻服务是系统级
采集器，没有租户上下文 → 每条新闻都拿到 None → 静默回落关键词启发式。
后果不是"标注差一点"：库里 3790/3809 条标注都是词表打的（confidence 恒 0.3、
分类一律 general），强度与方向没有语义依据，于是 e5_5_news_hedge 在
7 组阈值 × 4 个时间窗上超额全为负、无一过成本线（14bp）。
放宽阈值救不了 —— 越放宽越趋近随机（命中率 0.50、超额 −2.4bp），缺的是语义理解。

锁四件事：契约量纲不被通用契约污染、网关结果被正确采纳、置信度能与关键词区分、
以及网关不可用时仍能回落关键词而不是抛异常。
"""
from __future__ import annotations

import pytest

from backend.services.analysis import schemas
from backend.services.news_intelligence_service import NewsIntelligenceService


@pytest.fixture()
def svc():
    s = NewsIntelligenceService()
    s._seen_hashes = set()
    return s


ITEM = {"title": "Major exchange halts withdrawals after $200M exploit", "source": "theblock"}


def test_契约量纲不复用通用契约():
    """news 的 direction 是 −1~+1 连续值、strength 是 1~5，
    与通用契约的 enum direction / 0~10 strength 不是一回事，混用会让标注量纲对不上库表。"""
    req = schemas.TASK_SCHEMAS["news_annotate"]["required"]
    assert req["direction"] == "number:-1:1"
    assert req["strength"] == "number:1:5"
    assert "enum" not in req["direction"], "不能退回 bullish/bearish 枚举"

    ok, _ = schemas.validate("news_annotate", {
        "direction": -0.85, "strength": 4, "duration": "short",
        "symbols": ["BTC"], "category": "exchange", "confidence": 0.9, "summary": "x",
    })
    assert ok
    bad, errs = schemas.validate("news_annotate", {
        "direction": -0.85, "strength": 9, "duration": "short",
        "symbols": ["BTC"], "category": "exchange", "confidence": 0.9, "summary": "x",
    })
    assert not bad and any("strength" in e for e in errs), "强度越界必须被拦下"


def test_契约随任务渲染出正确取值范围():
    c = schemas.output_contract("news_annotate")
    assert "[-1, 1]" in c and "[1, 5]" in c, "模型必须被告知量纲，否则只能靠猜"


def test_网关标注被正确采纳(svc, monkeypatch):
    class _Res:
        ok = True
        json = {"direction": -0.85, "strength": 4, "duration": "short",
                "symbols": ["btc", "eth"], "category": "exchange",
                "confidence": 0.9, "summary": "交易所遭攻击并暂停提现"}

    class _GW:
        def call(self, *a, **k):
            return _Res()

    monkeypatch.setattr(
        "backend.services.analysis.model_gateway.get_model_gateway", lambda: _GW())
    got = svc._annotate_via_gateway(ITEM)
    assert got is not None, "网关成功时不应回落关键词"
    assert got.direction == pytest.approx(-0.85)
    assert got.strength == 4
    assert got.category == "exchange"
    assert got.symbols == ["BTC", "ETH"], "币种应规范为大写"


def test_置信度必须高于关键词档位(svc, monkeypatch):
    """annotation_quality() 用 confidence<=0.35 判定"关键词标注"，
    LLM 标注若落在该区间就与词表混为一谈，质量统计随即失真。"""
    class _Res:
        ok = True
        json = {"direction": 0.1, "strength": 1, "duration": "short",
                "symbols": ["BTC"], "category": "general",
                "confidence": 0.05, "summary": "s"}

    class _GW:
        def call(self, *a, **k):
            return _Res()

    monkeypatch.setattr(
        "backend.services.analysis.model_gateway.get_model_gateway", lambda: _GW())
    got = svc._annotate_via_gateway(ITEM)
    assert got.confidence >= 0.4, "LLM 标注的置信度必须与关键词档位可区分"


def test_schema不合格时不采纳(svc, monkeypatch):
    class _Res:
        ok = True
        json = {"direction": 5.0, "strength": 99}  # 越界且缺字段

    class _GW:
        def call(self, *a, **k):
            return _Res()

    monkeypatch.setattr(
        "backend.services.analysis.model_gateway.get_model_gateway", lambda: _GW())
    assert svc._annotate_via_gateway(ITEM) is None, "不合格输出必须丢弃，不能写进库"


def test_网关不可用时回落而不抛异常(svc, monkeypatch):
    class _GW:
        def call(self, *a, **k):
            raise RuntimeError("all transports down")

    monkeypatch.setattr(
        "backend.services.analysis.model_gateway.get_model_gateway", lambda: _GW())
    assert svc._annotate_via_gateway(ITEM) is None
    # 采集主流程必须仍能产出标注（关键词兜底），不能因此中断
    assert svc._heuristic_analyze(ITEM) is not None


def test_配额分类为轻量():
    from backend.services.analysis.quota_guard import task_class
    assert task_class("news_annotate") == "light", "单条标题，上下文极小，不应占深度配额"
