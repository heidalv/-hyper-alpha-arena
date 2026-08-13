# -*- coding: utf-8 -*-
"""task-ae3 v3.1.0：中长线提示词 K 线扩充 / 变量注入 / 互验段渲染验证。

覆盖：
1. TIER_PROMPT_HINTS 去硬编码（mid 15m/1d、long 4h/1d/1w/1M；无 SL/杠杆死值）
2. _build_market_brief：mid 注入 15m K 线；long 注入 1M 月线锚（含不足标注）
3. 两模板渲染：全部 3.1.0 变量无残留、互验段注入
4. _build_prompt 变量透传（deep_context / short_overlay / cross views）
"""
import sys, os
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ──────────────────────────────────────────────────
# 1. TIER_PROMPT_HINTS 去硬编码
# ──────────────────────────────────────────────────

def test_tier_prompt_hints_mid_aligned():
    from backend.config.settings import TIER_PROMPT_HINTS
    mid = TIER_PROMPT_HINTS["mid"]
    # 3.1.0：15m/1h/4h 结构验证 + 1d 方向锚 + 2-8h 持仓
    assert "15m/1h/4h" in mid
    assert "1d" in mid
    assert "2-8 小时" in mid
    assert "至少 2h 内不得主动全平" in mid
    # 去硬编码：无 SL 死值 / 旧持仓目标 / 旧周期
    assert "SL 3-5%" not in mid
    assert "24-48h" not in mid
    assert "MID (swing, 1h/4h K)" not in mid


def test_tier_prompt_hints_long_aligned():
    from backend.config.settings import TIER_PROMPT_HINTS
    long_ = TIER_PROMPT_HINTS["long"]
    # 3.1.0：4h/1d/1w/1M 大周期 + 1h/4h 仅择时 + 3-7 天
    assert "4h/1d/1w/1M" in long_
    assert "1h/4h 仅作入场择时" in long_
    assert "3-7 天" in long_
    assert "72h 内不得主动全平" in long_
    # 去硬编码：无 SL/杠杆死值、无旧分批 TP 硬编码
    assert "SL 6-10%" not in long_
    assert "≤8x" not in long_
    assert "TP 由系统分批战略 TP 管理" not in long_
    assert "4h-1d K" not in long_


# ──────────────────────────────────────────────────
# 2. _build_market_brief：15m / 1M K 线注入
# ──────────────────────────────────────────────────

def _kline_rows(prefix="2026-08-10 12:", n=30):
    """构造 K 线行：与 inject 写入的 recent_klines 结构一致。"""
    return [
        {"datetime": f"{prefix}{i:02d}", "open": 100 + i, "high": 102 + i,
         "low": 99 + i, "close": 101 + i, "volume": 1000.0}
        for i in range(n)
    ]


def _mk_packet(tier, ms_extra=None):
    from backend.services.mlto.types import PerceptionPacket
    ms = {
        "current_price": 105.0,
        "atr_1d_pct": 0.02,
        "volatility_regime": "normal",
        "funding_rate": 0.0001,
        "short_overlay": {
            "direction": "long", "confidence": 0.7,
            "age_sec": 600, "summary": "15m 放量突破",
        },
    }
    if ms_extra:
        ms.update(ms_extra)
    return PerceptionPacket(
        symbol="BTC",
        tier=tier,
        session_id="sess-test",
        ts=1_752_000_000.0,
        price=105.0,
        market_summary_sym=ms,
        orchestrator={"long_bias": "yes", "long_confidence": 0.6},
        quant_brief={"alignment_score": 11, "evidence_available_ratio": 0.8, "direction": "long"},
        analyst_reports={},
    )


def test_market_brief_mid_contains_15m_kline():
    from backend.services.mlto.qual_layer import _build_market_brief
    pkt = _mk_packet(
        "mid",
        {"indicators_15m": {"recent_klines": _kline_rows()},
         "indicators_1h": {"recent_klines": _kline_rows(prefix="2026-08-10 11:", n=10)},
         "indicators_4h": {"recent_klines": _kline_rows(prefix="2026-08-10 08:", n=10)}},
    )
    brief = _build_market_brief(pkt)
    assert "[15m K线×30]" in brief, "mid 必须注入 15m K 线摘要"
    # 短线 overlay 独立可见
    assert "短线overlay: dir=long conf=0.7 age=600s" in brief


def test_market_brief_long_contains_1M(monkeypatch):
    from backend.services.mlto.qual_layer import _build_market_brief

    def fake_klines(symbol, tf, count=30):
        if tf == "1M":
            return _kline_rows(prefix="2026-0", n=16)
        return _kline_rows()

    monkeypatch.setattr(
        "backend.services.kline_data_service.kline_service.get_klines_from_db",
        fake_klines,
    )
    pkt = _mk_packet("long", {"indicators_1d": {"recent_klines": _kline_rows(prefix="2026-08-0", n=10)}})
    brief = _build_market_brief(pkt)
    assert "[1M K线×16]" in brief, "long 必须注入 1M 月线锚"


def test_market_brief_long_1M_insufficient_notice(monkeypatch):
    from backend.services.mlto.qual_layer import _build_market_brief

    def fake_klines(symbol, tf, count=30):
        return _kline_rows(prefix="2026-0", n=3)  # <12 根

    monkeypatch.setattr(
        "backend.services.kline_data_service.kline_service.get_klines_from_db",
        fake_klines,
    )
    pkt = _mk_packet("long")
    brief = _build_market_brief(pkt)
    assert "[1M] 月线数据不足（<12 根），暂缺月线锚" in brief


# ──────────────────────────────────────────────────
# 3. 两模板渲染：3.1.0 变量全部可用、无残留
# ──────────────────────────────────────────────────

def test_template_render_swing_all_vars():
    from backend.services.agent_prompt_service import render_agent_task
    vars_ = {
        "symbol": "BTC",
        "market_brief": "价格: 105\n[15m] RSI=55.5 | 量比=1.2\n[1h] RSI=52.1\n[4h] RSI=49.8\n[1d] RSI=51.0",
        "thesis_block": "（thesis 块）",
        "memory_block": "（记忆）",
        "delta_block": "（增量）",
        "constraints": "（约束）",
        "deep_context": "### 深度市场数据块\n15m 结构: 上升",
        "short_overlay": "- direction: long\n- confidence: 0.7\n- age_sec: 600\n- summary: 15m 放量突破",
        "long_timing_view": "- direction: align\n- timing_score: 70/100\n- key_levels: {'support': 99, 'resistance': 108}",
        "mid_thesis_view": "（无中线观点）",
        "mid_view_request": "",
    }
    text = render_agent_task("task_swing_thesis_update", vars_, consumer="test.prompt.midlong:mid")
    assert "{{" not in text, "渲染后不得残留未替换变量"
    assert "BTC" in text
    assert "价格: 105" in text                       # market_brief 注入
    assert "短线建议" in text and "放量突破" in text   # short_overlay 段
    assert "长线择时视图" in text and "timing_score: 70/100" in text  # 互验段
    assert "深度市场数据" in text and "15m 结构: 上升" in text
    assert "15m / 1h / 4h" in text.replace("**", "") or "15m / 1h / 4h" in text  # 模板聚焦周期


def test_template_render_trend_all_vars():
    from backend.services.agent_prompt_service import render_agent_task
    vars_ = {
        "symbol": "ETH",
        "market_brief": "价格: 3500\n[4h] RSI=60.2\n[1d] RSI=58.8\n[1w] RSI=55.1",
        "thesis_block": "（thesis 块）",
        "memory_block": "（记忆）",
        "delta_block": "（增量）",
        "constraints": "（约束）",
        "deep_context": "### 月线结构\n历史高=4893 低=896 位置=65%",
        "short_overlay": "- direction: short\n- confidence: 0.6\n- age_sec: 900\n- summary: 4h 顶背离",
        "long_timing_view": "（无长线择时视图）",
        "mid_thesis_view": "- direction: long\n- llm_conviction: 65\n- summary: 4h 结构企稳",
        "mid_view_request": '',
    }
    text = render_agent_task("task_trend_thesis_update", vars_, consumer="test.prompt.midlong:long")
    assert "{{" not in text, "渲染后不得残留未替换变量"
    assert "ETH" in text
    assert "价格: 3500" in text
    assert "短线建议" in text and "4h 顶背离" in text
    assert "中线当前观点" in text and "llm_conviction: 65" in text  # 互验段
    assert "月线结构" in text and "4893" in text
    assert "4h / 1d / 1w / 1M" in text.replace("**", "") or "4h / 1d / 1w / 1M" in text


# ──────────────────────────────────────────────────
# 4. _build_prompt：3.1.0 变量透传
# ──────────────────────────────────────────────────

def test_build_prompt_passes_vars_mid(monkeypatch):
    from backend.services.mlto.qual_layer import _build_prompt
    from backend.services.mlto.types import ThesisDTO

    monkeypatch.setattr(
        "backend.services.agent_deep_context.build_full_deep_context",
        lambda *a, **k: "### 深度市场数据块\n[15m] 结构验证: 放量上破",
    )
    pkt = _mk_packet(
        "mid",
        {"indicators_15m": {"recent_klines": _kline_rows()},
         "indicators_1h": {"recent_klines": _kline_rows(prefix="2026-08-10 11:", n=10)}},
    )
    thesis = ThesisDTO(thesis_id="t1", session_id="sess-test", symbol="BTC", tier="mid")
    prompt = _build_prompt(thesis, "（记忆）", "（增量）", "（约束）", pkt)
    # market_brief 注入
    assert "价格: 105" in prompt
    assert "[15m K线×30]" in prompt
    # deep_context 透传
    assert "深度市场数据块" in prompt and "放量上破" in prompt
    # short_overlay 透传
    assert "短线建议" in prompt and "放量突破" in prompt
    # 互验段（无长线 thesis → 占位不报错）
    assert "（无长线择时视图）" in prompt or "长线择时视图" in prompt


def test_build_prompt_passes_vars_long(monkeypatch):
    from backend.services.mlto.qual_layer import _build_prompt
    from backend.services.mlto.types import ThesisDTO

    monkeypatch.setattr(
        "backend.services.agent_deep_context.build_trend_deep_context",
        lambda *a, **k: "### 月线结构\n历史高=4893 低=896 位置=65%",
    )
    monkeypatch.setattr(
        "backend.services.kline_data_service.kline_service.get_klines_from_db",
        lambda symbol, tf, count=30: _kline_rows(prefix="2026-0", n=14) if tf == "1M" else _kline_rows(),
    )
    pkt = _mk_packet("long", {"indicators_1d": {"recent_klines": _kline_rows(prefix="2026-08-0", n=10)}})
    thesis = ThesisDTO(thesis_id="t2", session_id="sess-test", symbol="BTC", tier="long")
    prompt = _build_prompt(thesis, "（记忆）", "（增量）", "（约束）", pkt)
    assert "价格: 105" in prompt
    assert "[1M K线×14]" in prompt          # 1M 月线锚注入
    assert "历史高=4893" in prompt           # deep_context 透传
    assert "短线建议" in prompt
    # 互验段（无中线 thesis → 占位）
    assert "（无中线观点）" in prompt or "中线当前观点" in prompt


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
