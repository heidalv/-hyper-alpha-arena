"""决策融合仲裁器单测（阶段0）：v2 决策矩阵全行覆盖。"""
import pytest

from backend.services import scalp_meta_trainer as _smt
from backend.services.decision_fusion_arbiter import (
    FusionDecision, decide_scalp, decide_mid, decide_long,
)


@pytest.fixture(autouse=True)
def _meta_model_usable(monkeypatch):
    """[2026-09-12] 本文件测的是分档/门槛机制本身：钉住 meta_model_usable=True。

    生产元模型 v3 自评 OOS AUC 0.522<0.53 → usable=False 时，decide_scalp 走
    探索配额路径（pwin_model_unusable_explore*），分档断言全部错位——生产模型
    状态泄漏进单测。这里显式钉住 True，机制测试与生产状态解耦；
    unusable 探索路径的契约在 scalp 侧专门测试覆盖。
    """
    monkeypatch.setattr(_smt, "meta_model_usable", lambda: True)

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

def _isolate(monkeypatch, *, tiers="0.60:0.75,0.55:0.50,0.50:0.25", probe_quota="0"):
    """把被测逻辑与生产 .env 隔离。

    decide_scalp() 首行调用 _maybe_reload_env()，它会 load_dotenv(override=True)
    把 .env 灌回 os.environ —— 这会盖掉 monkeypatch 的 setenv/delenv，导致下面
    这些用例实际测的是"当前生产门槛"而非"机制本身"。

    生产门槛随回溯证据收紧（2026-09-02：12.8 万条已结算信号显示 pwin<0.55 各档
    净收益全为负），档位已从三档砍到两档 0.60:0.75,0.55:0.50 —— 最低档即 0.55。
    下面的用例要验证的是"分档机制在给定档位下取值正确"，故显式钉住三档配置；
    分档模式的硬地板取 _tiers[-1][0]，与 FUSION_SCALP_PWIN_MIN 无关。
    """
    import backend.services.decision_fusion_arbiter as arb
    monkeypatch.setattr(arb, "_maybe_reload_env", lambda: None)
    monkeypatch.setenv("FUSION_PWIN_TIERED", "true")
    monkeypatch.setenv("FUSION_PWIN_TIERS", tiers)
    monkeypatch.setenv("FUSION_PROBE_DAILY_QUOTA", probe_quota)
    # [2026-09-12] 生产 .env 有 FUSION_PROBE_DAILY_QUOTA_PAPER=60：_probe_quota_cap
    # 按 mode 优先读 *_PAPER / *_LIVE 键，只设全局键会被生产值盖掉（实测 cap=60
    # 而非 3）。三键同钉，机制测试与生产配额解耦。
    monkeypatch.setenv("FUSION_PROBE_DAILY_QUOTA_PAPER", probe_quota)
    monkeypatch.setenv("FUSION_PROBE_DAILY_QUOTA_LIVE", probe_quota)
    return arb

def test_pwin_below_min_holds(monkeypatch):
    """[2026-08-29 v2 分档契约] 0.54 落在 [0.50,0.55) 档 → 0.25x 放行（非 hold）；
    低于最低档且探针关闭时 → hold。"""
    _isolate(monkeypatch, probe_quota="0")
    d54 = decide_scalp(pwin=0.54)
    assert d54.action == "trade" and abs(d54.size_mult - 0.25) < 1e-9
    d = decide_scalp(pwin=0.40)
    assert d.action == "hold" and d.reason == "pwin_below_min"

def test_pwin_probe_quota_guarantees_flow(monkeypatch, tmp_path):
    """保底流量机制：地板之下 pwin≥探针线且配额有余 → 0.125x 探针放行；
    配额耗尽 → hold；MR 不参与探针（MR 地板是保本口径，探它=负EV）。

    生产已将 FUSION_PROBE_DAILY_QUOTA 置 0（探针单经回溯为负期望），且探针仓位
    在 2026-09-01 由钉死的 0.125x 改为可配的 FUSION_PROBE_SIZE_MULT（生产 0.25）。
    此处显式开启配额并钉住仓位——验证的是"机制启用时行为正确"，与生产开关解耦。
    """
    arb = _isolate(monkeypatch, probe_quota="3")
    monkeypatch.setattr(arb, "_PROBE_QUOTA_PATH", str(tmp_path / "probe.json"))
    monkeypatch.setenv("FUSION_PROBE_MIN_PWIN", "0.45")
    monkeypatch.setenv("FUSION_PROBE_MIN_SCORE", "0")
    monkeypatch.setenv("FUSION_PROBE_SIZE_MULT", "0.125")
    d = decide_scalp(pwin=0.47, account_id=991)
    assert d.action == "trade" and d.reason == "pwin_probe_quota"
    assert abs(d.size_mult - 0.125) < 1e-9
    # 配额耗尽（本用例设 3 笔/天）
    for _ in range(3):
        arb.probe_quota_bump(991)
    d2 = decide_scalp(pwin=0.47, account_id=991)
    assert d2.action == "hold" and d2.reason == "pwin_below_min"
    # MR 不探针
    d3 = decide_scalp(pwin=0.47, account_id=992, is_mr=True)
    assert d3.action == "hold" and d3.reason == "pwin_below_min"

def test_pwin_tier_boundaries(monkeypatch):
    """分档边界：0.52→0.25x / 0.57→0.50x / 0.62→0.75x。"""
    _isolate(monkeypatch, probe_quota="0")
    assert abs(decide_scalp(pwin=0.52).size_mult - 0.25) < 1e-9
    assert abs(decide_scalp(pwin=0.57).size_mult - 0.50) < 1e-9
    assert abs(decide_scalp(pwin=0.62).size_mult - 0.75) < 1e-9

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
