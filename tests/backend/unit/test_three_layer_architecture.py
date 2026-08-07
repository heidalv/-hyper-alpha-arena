"""SwingAgent + BudgetService 单元测试

2026-07-06：LayerBudgetManager 已并入 BudgetService（预算单一事实来源），
本文件预算相关用例改为直接测 BudgetService。
"""
import pytest


pytestmark = pytest.mark.unit


# ────────────── SwingAgent ──────────────

def test_swing_is_swing_nature():
    from backend.services.swing_agent import SwingAgent
    sa = SwingAgent()
    assert sa.is_swing_nature("swing") is True
    assert sa.is_swing_nature("scalp") is False


def test_swing_normalize_high_confidence_opens():
    """置信度≥55 + 盈亏比≥1.8 → should_open=True。"""
    from backend.services.swing_agent import SwingAgent
    sa = SwingAgent()
    result = {"action": "buy", "confidence": 65, "direction": "long",
              "sl_pct": 0.03, "tp_pct": 0.07, "risk_reward": 2.33}
    dec = sa._normalize(result, "BTC")
    assert dec.action == "buy"
    assert dec.should_open is True
    assert dec.risk_reward >= 1.8


def test_swing_normalize_low_rr_blocks():
    """盈亏比 < 1.8 → should_open=False。"""
    from backend.services.swing_agent import SwingAgent
    sa = SwingAgent()
    result = {"action": "buy", "confidence": 70, "direction": "long",
              "sl_pct": 0.05, "tp_pct": 0.06, "risk_reward": 1.2}
    dec = sa._normalize(result, "BTC")
    assert dec.should_open is False  # 盈亏比不足


def test_swing_normalize_hold():
    """hold → should_open=False。"""
    from backend.services.swing_agent import SwingAgent
    sa = SwingAgent()
    result = {"action": "hold", "confidence": 40}
    dec = sa._normalize(result, "BTC")
    assert dec.should_open is False


def test_swing_fallback_neutral_hold():
    """LLM 失败 + 中期信号不足 → hold。"""
    from backend.services.swing_agent import SwingAgent
    sa = SwingAgent()
    dec = sa._fallback("BTC", {"BTC": {"orchestrator": {"mid_bias": "neutral", "mid_conf": 0.1}}})
    assert dec.action == "hold"


def test_swing_fallback_strong_mid_bias():
    """LLM 失败 + 中期强信号 → 规则回退开仓。

    注：_normalize 内的 MTF 融合此前在无 4h/1d 指标数据时会用硬编码 neutral=35
    稀释 conf（2026-07-06 已修：无数据不融合），本用例不提供指标数据，故 conf
    原样透传，验证强中期 bias 能触发回退开仓。
    """
    from backend.services.swing_agent import SwingAgent
    sa = SwingAgent()
    dec = sa._fallback("BTC", {"BTC": {"orchestrator": {"mid_bias": "bullish", "mid_conf": 0.6}}})
    assert dec.action == "buy"
    assert dec.should_open is True


# ────────────── BudgetService（预算单一事实来源）──────────────

def test_nature_to_layer_mapping():
    from backend.services.budget_service import BudgetService
    bs = BudgetService()
    assert bs.nature_to_layer("scalp") == "scalp"
    assert bs.nature_to_layer("intraday") == "scalp"
    assert bs.nature_to_layer("swing") == "swing"
    assert bs.nature_to_layer("trend_follow") == "trend"
    assert bs.nature_to_layer("position") == "trend"
    assert bs.nature_to_layer("") == "swing"  # 默认


def test_tier_to_layer_mapping():
    from backend.services.budget_service import BudgetService
    bs = BudgetService()
    assert bs.tier_to_layer("short") == "scalp"
    assert bs.tier_to_layer("mid") == "swing"
    assert bs.tier_to_layer("long") == "trend"
    # 未知 tier 退到 nature 映射，最终默认 swing
    assert bs.tier_to_layer("weird") == "swing"


def test_layer_allocation_single_source():
    """层分配比例来自 BudgetService 唯一定义（env 默认 40/45/15，总和≈1）。"""
    from backend.services.budget_service import BudgetService
    bs = BudgetService()
    alloc = bs.layer_allocations
    assert set(alloc.keys()) == {"scalp", "swing", "trend"}
    assert all(0.0 <= v <= 1.0 for v in alloc.values())
    assert abs(sum(alloc.values()) - 1.0) < 1e-6


def test_can_open_within_budget():
    """预算内 → can_open=True。"""
    from backend.services.budget_service import BudgetService
    from unittest.mock import patch
    bs = BudgetService()
    # mock 层已用保证金为 0（测试环境无持仓），只验证额度算法
    with patch.object(bs, '_query_layer_used_margin', return_value=0.0):
        # equity=$1000, swing 层=45% → $450，已用$0 → 可用$450 ≥ 50
        assert bs.can_open("mid", 50, 1000) is True


def test_can_open_over_budget():
    """超预算 → can_open=False。"""
    from backend.services.budget_service import BudgetService
    from unittest.mock import patch
    bs = BudgetService()
    with patch.object(bs, '_query_layer_used_margin', return_value=0.0):
        # scalp 层=40% → $400，请求 $500 → 不够
        assert bs.can_open("short", 500, 1000) is False


def test_budget_service_no_reverse_dependency():
    """回归护栏：BudgetService 不得再 import 已删除的 layer_budget_manager。

    只校验 import 语句（真正的反向依赖），不误伤 docstring 里对历史类名的说明。
    """
    import inspect
    from backend.services import budget_service as _mod
    src = inspect.getsource(_mod)
    assert "from backend.services.layer_budget_manager" not in src
    assert "import layer_budget_manager" not in src
    # 旧模块文件应已物理删除
    import importlib.util
    assert importlib.util.find_spec("backend.services.layer_budget_manager") is None
