"""[阶段3b] open_gate 精简为风险底线 单元测试。

覆盖 allow() 的 5 条风险底线 + recommend_open 接线：
  1. AI should_open + direction=long + 数据完整 + 固定符号 → PASSES（不被 readiness/reviews 拦）
  2. direction=neutral → blocked
  3. auto-coin symbol (tier=long) → blocked（固定交易对底线）
  4. AI recommend_open=False → blocked（LLM 自己拒绝）
  5. 关键数据缺失（price<=0 / market_summary 空）→ blocked
  6. readiness/reviews/prescreen 旧闸门不再拦截（near-miss 也能过）
  7. recommend_open 经 apply_llm_update 透传到 thesis
"""
from __future__ import annotations

import pytest

from backend.config import settings
from backend.services.mlto import open_gate
from backend.services.mlto.types import (
    HubDecision,
    PerceptionPacket,
    QualUpdateResult,
    ThesisDTO,
)


# ────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────
def _thesis(
    *,
    symbol: str = "BTC",
    tier: str = "long",
    direction: str = "long",
    open_readiness: int = 30,   # 故意远低于旧门槛 78，验证旧闸已删
    review_count: int = 0,      # 故意低于旧 min_reviews=3，验证旧闸已删
    recommend_open=None,
) -> ThesisDTO:
    return ThesisDTO(
        thesis_id="t1",
        session_id="sess1",
        symbol=symbol,
        tier=tier,
        direction=direction,
        open_readiness=open_readiness,
        review_count=review_count,
        recommend_open=recommend_open,
        thesis_summary="LLM 研判：多头趋势",
    )


def _hub(*, direction: str = "long", action: str = "BUILD") -> HubDecision:
    return HubDecision(
        action=action,
        direction=direction,
        composite=0.7,
        adjusted=0.6,
        consistency=0.9,
        open_readiness=30,
        reason_text="BUILD long",
    )


def _packet(
    *,
    symbol: str = "BTC",
    tier: str = "long",
    price: float = 100000.0,
    session_id: str = "sess1",
) -> PerceptionPacket:
    return PerceptionPacket(
        symbol=symbol,
        tier=tier,
        session_id=session_id,
        ts=0.0,
        price=price,
        market_summary_sym={"current_price": price},
        orchestrator={},
        quant_brief={},
        analyst_reports={},
        pre_screener_passed=False,   # 故意 fail，验证旧 pre_screener 闸已删
        pre_screener_reason="unit-test skip",
    )


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    """打开总开关，使 5 条底线生效（默认 settings 里为 False）。"""
    monkeypatch.setattr(settings, "MIDLONG_THESIS_OPEN_GATE", True)


@pytest.fixture(autouse=True)
def _no_chop(monkeypatch):
    """[2026-07-31 震荡禁开适配] 本套件聚焦 5 条风险底线本身，
    不测 chop_regime（无真实市场指标必然误判震荡）。统一跳过该底线。"""
    monkeypatch.setattr(
        "backend.services.mlto.midlong_trade_design.is_chop_regime",
        lambda *a, **k: (False, ""),
    )


@pytest.fixture
def fixed_symbols(monkeypatch):
    """让 get_fixed_symbols_for_session 返回固定白名单 {BTC, ETH}，不查 DB。"""
    monkeypatch.setattr(
        "backend.services.auto_coin_selector.get_fixed_symbols_for_session",
        lambda *a, **k: {"BTC", "ETH"},
    )


# ════════════════════════════════════════════════════════════════════
# A. 正常放行（AI should_open 驱动，旧闸不拦）
# ════════════════════════════════════════════════════════════════════
class TestPassThrough:
    def test_ai_should_open_passes_despite_low_readiness_reviews(self, fixed_symbols):
        """readiness=30 (<78) + reviews=0 (<3) + prescreen fail → 仍 PASSES。"""
        thesis = _thesis(open_readiness=30, review_count=0)
        hub = _hub(direction="long", action="BUILD")
        packet = _packet(symbol="BTC", tier="long")
        ok, reason = open_gate.allow(thesis, hub, packet, {})
        assert ok, f"应放行（AI should_open），实际被拦: {reason}"
        assert reason == "ok"

    def test_wait_action_with_direction_passes(self, fixed_symbols):
        """hub.action=WAIT 但方向明确 → 放行（AI 试探档）。"""
        thesis = _thesis()
        hub = _hub(direction="long", action="WAIT")
        packet = _packet()
        ok, _ = open_gate.allow(thesis, hub, packet, {})
        assert ok

    def test_short_direction_passes(self, fixed_symbols):
        thesis = _thesis(direction="short")
        hub = _hub(direction="short", action="NIBBLE")
        packet = _packet()
        ok, _ = open_gate.allow(thesis, hub, packet, {})
        assert ok


# ════════════════════════════════════════════════════════════════════
# B. 底线 1：direction != neutral
# ════════════════════════════════════════════════════════════════════
class TestDirectionFloor:
    def test_neutral_direction_blocked(self, fixed_symbols):
        thesis = _thesis(direction="neutral")
        hub = _hub(direction="neutral", action="WAIT")
        packet = _packet()
        ok, reason = open_gate.allow(thesis, hub, packet, {})
        assert not ok
        assert "neutral" in reason


# ════════════════════════════════════════════════════════════════════
# C. 底线 3：固定交易对边界（auto-coin 长线拦截）
# ════════════════════════════════════════════════════════════════════
class TestFixedSymbolFloor:
    def test_auto_coin_long_blocked(self, fixed_symbols):
        """BTC/ETH 在白名单；DOGE 不在 → tier=long 视为 auto-coin → 拦截。"""
        thesis = _thesis(symbol="DOGE", tier="long", direction="long")
        hub = _hub(direction="long", action="BUILD")
        packet = _packet(symbol="DOGE", tier="long")
        ok, reason = open_gate.allow(thesis, hub, packet, {})
        assert not ok
        assert "auto-coin" in reason or "fixed-symbol" in reason

    def test_fixed_symbol_long_passes(self, fixed_symbols):
        """BTC 在白名单 → tier=long 放行。"""
        thesis = _thesis(symbol="BTC", tier="long")
        hub = _hub(direction="long", action="BUILD")
        packet = _packet(symbol="BTC", tier="long")
        ok, _ = open_gate.allow(thesis, hub, packet, {})
        assert ok

    def test_auto_coin_check_skipped_for_mid(self, fixed_symbols):
        """tier=mid 不走固定交易对底线（mid 路径仍运行）。"""
        thesis = _thesis(symbol="DOGE", tier="mid")
        hub = _hub(direction="long", action="BUILD")
        packet = _packet(symbol="DOGE", tier="mid")
        ok, _ = open_gate.allow(thesis, hub, packet, {})
        assert ok

    def test_empty_whitelist_does_not_block(self, monkeypatch):
        """白名单为空（未配置）→ 不拦截（容错优先，交给执行层守卫）。"""
        monkeypatch.setattr(
            "backend.services.auto_coin_selector.get_fixed_symbols_for_session",
            lambda *a, **k: set(),
        )
        thesis = _thesis(symbol="DOGE", tier="long")
        hub = _hub(direction="long", action="BUILD")
        packet = _packet(symbol="DOGE", tier="long")
        ok, _ = open_gate.allow(thesis, hub, packet, {})
        assert ok


# ════════════════════════════════════════════════════════════════════
# D. 底线 4：recommend_open 尊重
# ════════════════════════════════════════════════════════════════════
class TestRecommendOpen:
    def test_llm_recommend_open_false_blocked(self, fixed_symbols):
        """LLM 明确 recommend_open=False → 拦截（AI 自己说不建议开仓）。"""
        thesis = _thesis(recommend_open=False)
        hub = _hub(direction="long", action="BUILD")
        packet = _packet()
        ok, reason = open_gate.allow(thesis, hub, packet, {})
        assert not ok
        assert "recommend_open" in reason

    def test_llm_recommend_open_true_passes(self, fixed_symbols):
        thesis = _thesis(recommend_open=True)
        hub = _hub(direction="long", action="BUILD")
        packet = _packet()
        ok, _ = open_gate.allow(thesis, hub, packet, {})
        assert ok

    def test_llm_recommend_open_none_passes(self, fixed_symbols):
        """LLM 未明确给出（None）→ 不拦截，按 AI should_open 默认放行。"""
        thesis = _thesis(recommend_open=None)
        hub = _hub(direction="long", action="BUILD")
        packet = _packet()
        ok, _ = open_gate.allow(thesis, hub, packet, {})
        assert ok


# ════════════════════════════════════════════════════════════════════
# E. 底线 2：数据完整性
# ════════════════════════════════════════════════════════════════════
class TestDataCompleteness:
    def test_zero_price_blocked(self, fixed_symbols):
        thesis = _thesis()
        hub = _hub(direction="long", action="BUILD")
        packet = _packet(price=0.0)
        ok, reason = open_gate.allow(thesis, hub, packet, {})
        assert not ok
        assert "data" in reason

    def test_empty_market_summary_blocked(self, fixed_symbols):
        thesis = _thesis()
        hub = _hub(direction="long", action="BUILD")
        packet = _packet()
        packet.market_summary_sym = {}
        ok, reason = open_gate.allow(thesis, hub, packet, {})
        assert not ok
        assert "data" in reason


# ════════════════════════════════════════════════════════════════════
# F. 底线 5：hub action 合法
# ════════════════════════════════════════════════════════════════════
class TestHubActionFloor:
    def test_unknown_hub_action_blocked(self, fixed_symbols):
        thesis = _thesis()
        hub = _hub(direction="long", action="HOLD")  # 非法 action
        packet = _packet()
        ok, reason = open_gate.allow(thesis, hub, packet, {})
        assert not ok
        assert "hub_action" in reason


# ════════════════════════════════════════════════════════════════════
# G. 总开关关闭：仍守 direction neutral 底线
# ════════════════════════════════════════════════════════════════════
class TestMasterSwitch:
    def test_gate_disabled_still_blocks_neutral(self, monkeypatch, fixed_symbols):
        monkeypatch.setattr(settings, "MIDLONG_THESIS_OPEN_GATE", False)
        thesis = _thesis(direction="neutral")
        hub = _hub(direction="neutral", action="WAIT")
        packet = _packet()
        ok, _ = open_gate.allow(thesis, hub, packet, {})
        assert not ok

    def test_gate_disabled_passes_non_neutral(self, monkeypatch, fixed_symbols):
        monkeypatch.setattr(settings, "MIDLONG_THESIS_OPEN_GATE", False)
        thesis = _thesis(direction="long")
        hub = _hub(direction="long", action="WAIT")
        packet = _packet()
        ok, _ = open_gate.allow(thesis, hub, packet, {})
        assert ok


# ════════════════════════════════════════════════════════════════════
# H. recommend_open 经 apply_llm_update 透传
# ════════════════════════════════════════════════════════════════════
class TestRecommendOpenWired:
    def test_apply_llm_update_copies_recommend_open_false(self):
        from backend.services.mlto import thesis_store

        thesis = _thesis(recommend_open=None)
        qual = QualUpdateResult(
            direction="long",
            thesis_summary="看多",
            recommend_open=False,
        )
        thesis_store.apply_llm_update(thesis, qual, db=None)
        assert thesis.recommend_open is False

    def test_apply_llm_update_copies_recommend_open_true(self):
        from backend.services.mlto import thesis_store

        thesis = _thesis(recommend_open=None)
        qual = QualUpdateResult(
            direction="long",
            thesis_summary="看多",
            recommend_open=True,
        )
        thesis_store.apply_llm_update(thesis, qual, db=None)
        assert thesis.recommend_open is True

    def test_apply_llm_update_true_overrides_previous_false(self):
        """新一轮 LLM 的 recommend_open=True 覆盖上一轮的 False。"""
        from backend.services.mlto import thesis_store

        thesis = _thesis(recommend_open=False)
        qual = QualUpdateResult(
            direction="long",
            thesis_summary="转多",
            recommend_open=True,
        )
        thesis_store.apply_llm_update(thesis, qual, db=None)
        assert thesis.recommend_open is True


# ════════════════════════════════════════════════════════════════════
# I. describe_gate_status 与 allow 一致
# ════════════════════════════════════════════════════════════════════
class TestDescribeGateStatus:
    def test_describe_reflects_5_floors(self, fixed_symbols):
        thesis = _thesis()
        hub = _hub(direction="long", action="BUILD")
        packet = _packet()
        status = open_gate.describe_gate_status(thesis, hub, packet, {})
        keys = {c["key"] for c in status["checks"]}
        # 5 条底线
        assert keys == {"direction", "data_complete", "fixed_symbol", "recommend_open", "hub_action"}
        assert status["can_open"] is True
