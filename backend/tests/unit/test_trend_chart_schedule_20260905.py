# -*- coding: utf-8 -*-
"""图审间隔调度：缺图优先、新鲜跳过、单轮封顶。"""
from __future__ import annotations


def test_order_chart_review_skips_fresh_and_caps(monkeypatch):
    from backend.services.analysis import tasks as t

    monkeypatch.setenv("ANALYSIS_TREND_CHART_FRESH_H", "4")
    monkeypatch.setenv("ANALYSIS_TREND_CHART_MAX_PER_CYCLE", "3")
    now = 1_000_000_000_000
    created = {
        "UNI": now - 1 * 3600 * 1000,       # 1h 新鲜，跳过
        "ASTER": None,                      # 缺图
        "VIRTUAL": None,                    # 缺图
        "XPL": now - 12 * 3600 * 1000,      # 过期
        "DOGE": now - 20 * 3600 * 1000,     # 过期但非中长线优先
        "LINK": now - 30 * 60 * 1000,       # 30min 新鲜，跳过
    }
    out = t.order_chart_review_symbols(
        ["DOGE", "UNI", "ASTER", "VIRTUAL", "XPL", "LINK"],
        priority=["UNI", "ASTER", "VIRTUAL", "XPL", "LINK"],
        now_ms=now,
        created_ms=created,
    )
    assert "UNI" not in out and "LINK" not in out
    assert out[0] in ("ASTER", "VIRTUAL")
    assert "XPL" in out
    assert len(out) == 3


def test_trend_chart_interval_defaults(monkeypatch):
    from backend.services.analysis import tasks as t

    monkeypatch.delenv("ANALYSIS_TREND_CHART_SEC", raising=False)
    monkeypatch.delenv("ANALYSIS_TREND_CHART_FRESH_H", raising=False)
    assert t.trend_chart_interval_sec() == 14400  # [2026-09-07] 28800→14400（4h，对齐 QuantAgent 节奏）
    assert t.chart_fresh_horizon_h() == 4.0
    monkeypatch.setenv("ANALYSIS_TREND_CHART_SEC", "600")
    assert t.trend_chart_interval_sec() == 1800  # 下限 30min
