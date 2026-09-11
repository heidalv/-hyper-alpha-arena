# -*- coding: utf-8 -*-
"""[2026-09-09 第十八轮] 1h 派生择时读数进入 LLM 分析简报 + 24h 字段键名修复。

## 背景（用户提问「中线分析是不是要加入 1 小时 K 线数据」的调查结论）

1h 原始 K 线**早已**在中线分析里（`qual_layer` 的 `[1h K线×30]` +
`build_full_deep_context(15m/1h/4h/1d×30)`，2026-08-10 v3.1.0 起）。
缺的是**由 1h 派生的两个被证明有边际的择时读数**：
`price_change_24h_pct`（24h 涨跌）与 `range_24h_high/low`（24h 区间位置）——
它们此前只被闸门使用（位置闸 / learned 门 / regime_agent），
LLM 简报里一行都没有。

## 契约

1. `_h1_timing_line`：优先用注入字段渲染 `[1h择时]`；缺字段时从
   `indicators_1h.recent_klines` 兜底计算；都缺 → 返回空串（不阻塞）；
2. `_build_market_brief` 输出含 `[1h择时]` 行；
3. `brain._cheap_evidence_score` 认 `price_change_24h_pct`
   （此前只认 change_24h/chg_24h/pct_24h → 恒 0，「死水」恒触发）。
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.mlto import qual_layer  # noqa: E402


def test_h1_timing_line_from_injected_fields():
    ms = {
        "current_price": 105.0,
        "price_change_1h_pct": 0.8,
        "price_change_24h_pct": 4.2,
        "range_24h_high": 110.0,
        "range_24h_low": 100.0,
    }
    line = qual_layer._h1_timing_line(ms, 105.0)
    assert line.startswith("[1h择时]")
    assert "1h涨跌=+0.80%" in line
    assert "24h涨跌=+4.20%" in line
    assert "24h区间位置=50%" in line  # (105-100)/(110-100)=50%


def test_h1_timing_line_fallback_from_klines():
    closes = [100.0] * 24 + [104.0]  # 25 根：chg24 = +4%
    rows = [{"close": c, "high": c + 1, "low": c - 1} for c in closes]
    ms = {"current_price": 104.0, "indicators_1h": {"recent_klines": rows}}
    line = qual_layer._h1_timing_line(ms, 104.0)
    assert "24h涨跌=+4.00%" in line, line
    # 位置 = (104-99)/(105-99) ≈ 83%
    assert "24h区间位置=" in line


def test_h1_timing_line_empty_when_no_data():
    assert qual_layer._h1_timing_line({}, 100.0) == ""
    assert qual_layer._h1_timing_line({"indicators_1h": {}}, 0.0) == ""


def test_market_brief_contains_h1_timing():
    packet = SimpleNamespace(
        tier="mid",
        market_summary_sym={
            "current_price": 100.0,
            "price_change_24h_pct": 3.5,
            "price_change_1h_pct": 0.4,
            "range_24h_high": 102.0,
            "range_24h_low": 96.0,
        },
        orchestrator={},
        quant_brief={},
        analyst_reports={},
        portfolio={},
    )
    brief = qual_layer._build_market_brief(packet)
    assert "[1h择时]" in brief, brief
    assert "24h涨跌=+3.50%" in brief


def test_cheap_evidence_score_reads_price_change_24h_pct():
    from backend.services.mlto.brain import _cheap_evidence_score

    # 24h 涨跌 6% → 加分（此前键名不匹配恒 0 → 反而扣「死水」）
    hot = _cheap_evidence_score("BTC", {"BTC": {"price_change_24h_pct": 6.0}})
    dead = _cheap_evidence_score("BTC", {"BTC": {"price_change_24h_pct": 0.2}})
    assert hot["score"] > dead["score"], (hot, dead)
    assert any("24h波动" in r for r in hot.get("reasons") or []), hot
    assert any("死水" in r for r in dead.get("reasons") or []), dead
    # 旧键名（选币链路）仍兼容
    legacy = _cheap_evidence_score("BTC", {"BTC": {"change_24h": 6.0}})
    assert any("24h波动" in r for r in legacy.get("reasons") or []), legacy
