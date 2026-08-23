"""决策融合仲裁器单测（阶段0）：v2 决策矩阵全行覆盖 + 出场通道熔断器。"""
from backend.services.decision_fusion_arbiter import (
    FusionDecision, decide_scalp, decide_mid, decide_long, ExitChannelBreaker,
)

# ── decide_scalp 决策矩阵 ──

def test_llm_hard_veto_overrides_everything():
    d = decide_scalp(pwin=0.9, llm_veto="liquidation_cluster")
    assert d.action == "hold" and d.source == "llm" and not d.allowed

def test_credit_shadow_blocks_source():
    d = decide_scalp(pwin=0.9, credit=0.0)
    assert d.action == "standdown" and not d.allowed

def test_pwin_missing_fail_closed():
    d = decide_scalp(pwin=None)
    assert d.action == "hold" and d.reason == "pwin_unavailable"

def test_pwin_below_min_holds():
    d = decide_scalp(pwin=0.54)
    assert d.action == "hold" and d.reason == "pwin_below_min"

def test_pwin_55_observe_size_factor_source():
    d = decide_scalp(pwin=0.55, factor_score=50, direction="long")
    assert d.action == "trade" and d.source == "factor"
    assert d.size_mult == 0.5

def test_pwin_60_strong_size():
    d = decide_scalp(pwin=0.60)
    assert d.action == "trade" and d.size_mult == 0.75

def test_high_factor_score_gets_no_size_bonus():
    d = decide_scalp(pwin=0.55, factor_score=95)
    assert d.size_mult == 0.5  # 反证据保护：score 不给仓位加分

def test_thesis_same_direction_hybrid():
    d = decide_scalp(pwin=0.55, direction="long", thesis_dir="long", thesis_conf=0.8)
    assert d.action == "trade" and d.source == "hybrid"

def test_thesis_weak_conflict_ignored():
    d = decide_scalp(pwin=0.55, direction="long", thesis_dir="short", thesis_conf=0.4)
    assert d.action == "trade"

def test_thesis_strong_conflict_needs_arbitration():
    d = decide_scalp(pwin=0.55, direction="long", thesis_dir="short", thesis_conf=0.7)
    assert d.action == "arbitrate"

def test_thesis_conflict_arbitration_denied():
    d = decide_scalp(pwin=0.55, direction="long", thesis_dir="short", thesis_conf=0.7, llm_arbitration=False)
    assert d.action == "hold" and d.reason == "thesis_conflict_denied"

def test_thesis_conflict_arbitration_allowed_quarter_size():
    d = decide_scalp(pwin=0.55, direction="long", thesis_dir="short", thesis_conf=0.7, llm_arbitration=True)
    assert d.action == "trade" and d.size_mult == 0.25 and d.source == "hybrid"

def test_rr_floor_blocks_bad_structure():
    d = decide_scalp(pwin=0.8, tp_pct=0.0114, sl_pct=0.0126)  # RR=0.9
    assert d.action == "hold" and d.reason == "rr_below_floor"

def test_rr_floor_passes_good_structure():
    d = decide_scalp(pwin=0.8, tp_pct=0.015, sl_pct=0.0115)  # RR=1.3
    assert d.action == "trade"

# ── decide_mid ──

def test_mid_factor_below_threshold_holds():
    assert decide_mid(False, "long").action == "hold"

def test_mid_pure_factor_ok():
    d = decide_mid(True, "long")
    assert d.action == "trade" and d.source == "factor" and d.size_mult == 1.0

def test_mid_thesis_align_hybrid():
    d = decide_mid(True, "long", thesis_dir="long", thesis_conf=0.8)
    assert d.action == "trade" and d.source == "hybrid"

def test_mid_thesis_conflict_skips():
    d = decide_mid(True, "long", thesis_dir="short", thesis_conf=0.7)
    assert d.action == "hold" and d.reason == "thesis_conflict_skip"

def test_mid_weak_conflict_passes():
    assert decide_mid(True, "long", thesis_dir="short", thesis_conf=0.4).action == "trade"

# ── decide_long ──

def test_long_thesis_standdown():
    d = decide_long(thesis_state="standdown", l1_state="up")
    assert d.action == "standdown" and d.source == "llm"

def test_long_l1_up_trades():
    d = decide_long(thesis_state=None, l1_state="up")
    assert d.action == "trade"

def test_long_l1_sideways_holds():
    assert decide_long(l1_state="sideways").action == "hold"

# ── ExitChannelBreaker ──

def test_breaker_key_format():
    assert ExitChannelBreaker.key("trend_review_close", "long") == "long|trend_review_close"

def test_breaker_shadows_only_after_30_samples_below_threshold():
    b = ExitChannelBreaker()
    for i in range(29):
        b.feed("bad_channel", "mid", win=False)
    assert not b.is_shadow("bad_channel", "mid")
    b.feed("bad_channel", "mid", win=False)  # 第30笔，wr=0 < 0.40
    assert b.is_shadow("bad_channel", "mid")

def test_breaker_no_shadow_when_wr_ok():
    b = ExitChannelBreaker()
    for i in range(30):
        b.feed("good_channel", "mid", win=(i % 2 == 0))  # wr≈0.5
    assert not b.is_shadow("good_channel", "mid")

def test_breaker_recovers_after_improvement():
    b = ExitChannelBreaker()
    for _ in range(30):
        b.feed("recover", "mid", win=False)
    assert b.is_shadow("recover", "mid")
    for _ in range(30):
        b.feed("recover", "mid", win=True)
    assert not b.is_shadow("recover", "mid")

def test_breaker_export_load_roundtrip():
    b = ExitChannelBreaker()
    for _ in range(30):
        b.feed("rt", "mid", win=False)
    b2 = ExitChannelBreaker()
    b2.load(b.export())
    assert b2.is_shadow("rt", "mid")
