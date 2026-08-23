"""来源归因/通道熔断 单测（阶段2）。"""
import os
import tempfile

import backend.services.source_attribution as sa


def _fresh():
    # 隔离状态文件
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    os.remove(tmp.name)
    sa._STATE_PATH = tmp.name
    a = sa.SourceAttribution()
    return a, tmp.name


def test_normalize_reason():
    assert sa.normalize_reason("trend_broken: 原始多头论点不成立") == "trend_broken"
    assert sa.normalize_reason("[no_progress] hold=18h") == "[no_progress]"
    assert sa.normalize_reason("tp") == "tp"
    assert sa.normalize_reason("") == "?"

def test_tag_and_record_and_credit():
    a, path = _fresh()
    a.tag_position(101, source="factor", nature="scalp", symbol="XRP")
    r = a.record_close(101, pnl=1.0, fee=0.1, close_reason="tp", tier="short", symbol="XRP", nature="scalp")
    assert r["n"] == 1 and r["net"] == 0.9
    assert a.credit("factor", "scalp", "XRP") == 1.0

def test_source_shadow_after_20_negative():
    a, path = _fresh()
    for i in range(20):
        a.tag_position(1000 + i, source="factor", nature="scalp", symbol="SOL")
        a.record_close(1000 + i, pnl=-1.0, fee=0.0, close_reason="sl", tier="short", symbol="SOL", nature="scalp")
    assert a.credit("factor", "scalp", "SOL") == 0.0

def test_source_recovers_after_40_positive():
    a, path = _fresh()
    for i in range(20):
        a.tag_position(2000 + i, source="factor", nature="scalp", symbol="SOL")
        a.record_close(2000 + i, pnl=-1.0, fee=0.0, close_reason="sl", tier="short", symbol="SOL", nature="scalp")
    assert a.credit("factor", "scalp", "SOL") == 0.0
    for i in range(20):
        a.tag_position(3000 + i, source="factor", nature="scalp", symbol="SOL")
        a.record_close(3000 + i, pnl=2.0, fee=0.0, close_reason="tp", tier="short", symbol="SOL", nature="scalp")
    # n=40 且期望>=0 → 解除 shadow
    assert a.credit("factor", "scalp", "SOL") == 1.0

def test_breaker_shadows_channel_at_30():
    a, path = _fresh()
    for i in range(29):
        a.tag_position(4000 + i, source="llm", nature="swing", symbol="BTC")
        a.record_close(4000 + i, pnl=-1.0, fee=0.0, close_reason="trend_broken", tier="mid", symbol="BTC", nature="swing")
    assert not a.exit_channel_shadow("trend_broken", "mid")
    a.tag_position(4029, source="llm", nature="swing", symbol="BTC")
    a.record_close(4029, pnl=-1.0, fee=0.0, close_reason="trend_broken", tier="mid", symbol="BTC", nature="swing")
    assert a.exit_channel_shadow("trend_broken", "mid")

def test_breaker_normalizes_long_reason():
    a, path = _fresh()
    for i in range(30):
        a.tag_position(5000 + i, source="llm", nature="swing", symbol="BTC")
        a.record_close(5000 + i, pnl=-1.0, fee=0.0,
                       close_reason="trend_broken: 多周期共振反向", tier="mid",
                       symbol="BTC", nature="swing")
    assert a.exit_channel_shadow("trend_broken: 多周期共振反向", "mid")
    assert a.exit_channel_shadow("trend_broken", "mid")

def test_persistence_roundtrip():
    a, path = _fresh()
    for i in range(20):
        a.tag_position(6000 + i, source="factor", nature="scalp", symbol="SOL")
        a.record_close(6000 + i, pnl=-1.0, fee=0.0, close_reason="sl", tier="short", symbol="SOL", nature="scalp")
    a._maybe_save(force=True)
    b = sa.SourceAttribution()
    b._ensure_loaded()
    assert b.credit("factor", "scalp", "SOL") == 0.0

def test_llm_arbitrate_disabled_returns_none():
    os.environ["FUSION_ARBITRATE_LLM"] = "false"
    assert sa.llm_arbitrate_conflict(symbol="XRP", direction="long", thesis_dir="short",
                                     thesis_conf=0.7, pwin=0.6, factor_score=50) is None
    os.environ.pop("FUSION_ARBITRATE_LLM", None)
