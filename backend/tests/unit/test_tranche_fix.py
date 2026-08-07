"""Phase D: tranche gate 接线修复 — 单元测试

修复三件事:
  Fix 1: compute_margin_pct 算出的分档保证金比例此前从不传到下单（dead sizing），
         现在经 try_execute_independent_agent_open → proposal.extra →
         proposal_execution 叠乘到最终 size_multiplier。
  Fix 2: advance_tranche 此前在 open_gate 通过后无条件推进，3 次拒单即锁死。
         现在仅在真正发出 buy/sell（action != hold）时推进。
  Fix 3: reset_tranche 此前只在 invalidation/should_close（本函数 close 分支）触发；
         SL/TP/staged-TP/max_hold 在别处平仓不复位 tranche，导致 stage 停在高档
         再也开不出。现在 orchestrator 检测到「tranche>0 且无持仓」即复位。

覆盖:
  A. compute_margin_pct 分档值（stage 0/1/2/3 + NIBBLE）
  B. try_execute_independent_agent_open 接收并透传 tranche_margin_pct 到 proposal
  C. proposal_execution 把 tranche_margin_pct 作为 size 乘子（含 0% 拒单）
  D. orchestrator: action == hold（neutral 方向）时 tranche 不推进
  E. orchestrator: action == buy/sell 时 tranche 推进
  F. orchestrator: stage≥3 经 3 次确认开仓才能到达（与 D/E 组合验证）
  G. orchestrator: 持仓被外部平掉后停在锁死档（stage≥3 & 无持仓）→ 复位到 0
  H. orchestrator: 首次开仓 tick（tranche=0 & 无持仓）→ 不误清
     以及分档建仓进行中（stage 1/2 & 无持仓）→ 不误清（避免打断建仓）
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

# 确保可 import backend.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ═══════════════════════════════════════════════════════════════════
# 测试辅助：构造最小 packet + stub 掉 MLTO 上游 pipeline
# （与 test_invalidation_close.py 同构，保证 run_tick 不依赖 DB/LLM）
# ═══════════════════════════════════════════════════════════════════
def _make_packet(symbol="BTC", tier="long", price=59000.0, portfolio=None):
    from backend.services.mlto.types import PerceptionPacket
    return PerceptionPacket(
        symbol=symbol, tier=tier, session_id="s1", ts=0.0, price=price,
        market_summary_sym={}, orchestrator={}, quant_brief={},
        analyst_reports={}, portfolio=portfolio or {},
    )


# 模块级 holder：把 thesis 注入到 stubbed thesis_store.get_or_create
_STUB_THESIS = []
# 模块级 holder：注入 hub（决定 action 是 buy/sell 还是 hold）
_STUB_HUB = []


def _stub_pipeline(monkeypatch, hub_direction="long", hub_action="BUILD"):
    from backend.services.mlto import (
        evidence_ingest, layered_memory, qual_layer, quant_layer,
        decision_hub, debate_layer, thesis_store,
    )
    from backend.services.mlto.types import HubDecision, QualUpdateResult

    def _fake_get_or_create(session_id, symbol, tier, regime_hash, db=None):
        return _STUB_THESIS[0]

    monkeypatch.setattr(thesis_store, "get_or_create", _fake_get_or_create, raising=False)
    monkeypatch.setattr(thesis_store, "apply_regime_reset", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(thesis_store, "apply_llm_update", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(thesis_store, "append_event", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(thesis_store, "update_hub", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(thesis_store, "_persist", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(evidence_ingest, "ingest_tick", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(evidence_ingest, "build_regime_hash", lambda *a, **k: "", raising=False)
    monkeypatch.setattr(layered_memory, "retrieve", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(
        qual_layer, "update_thesis",
        lambda *a, **k: QualUpdateResult(direction=hub_direction, thesis_summary="x"),
        raising=False,
    )
    monkeypatch.setattr(quant_layer, "compute", lambda *a, **k: [], raising=False)
    _fake_hub = HubDecision(
        action=hub_action, direction=hub_direction, composite=0.7, adjusted=0.7,
        consistency=0.7, open_readiness=60, reason_text="stub",
    )
    _STUB_HUB.clear()
    _STUB_HUB.append(_fake_hub)
    monkeypatch.setattr(
        decision_hub, "fuse_signals", lambda *a, **k: _STUB_HUB[0], raising=False,
    )
    monkeypatch.setattr(debate_layer, "should_debate", lambda *a, **k: False, raising=False)


def _stub_open_gate_allow(monkeypatch, allowed=True, reason=""):
    from backend.services.mlto import open_gate
    monkeypatch.setattr(open_gate, "allow", lambda *a, **k: (allowed, reason), raising=False)


def _setup_thesis(tranche_stage=0, invalidation=None, direction="long"):
    from backend.services.mlto.types import ThesisDTO
    t = ThesisDTO(
        thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
        direction=direction, open_readiness=60, tranche_stage=tranche_stage,
        invalidation=invalidation or {},
    )
    _STUB_THESIS.clear()
    _STUB_THESIS.append(t)
    return t


# ═══════════════════════════════════════════════════════════════════
# A. compute_margin_pct 分档值
# ═══════════════════════════════════════════════════════════════════
class TestComputeMarginPct:
    def _hub(self, action="BUILD"):
        from backend.services.mlto.types import HubDecision
        return HubDecision(
            action=action, direction="long", composite=0.7, adjusted=0.7,
            consistency=0.7, open_readiness=60, reason_text="x",
        )

    def test_build_stage0_is_30pct(self):
        from backend.services.mlto import tranche_gate
        t = _setup_thesis(tranche_stage=0)
        assert tranche_gate.compute_margin_pct(t, self._hub("BUILD"), False) == 0.30

    def test_build_stage1_is_30pct(self):
        from backend.services.mlto import tranche_gate
        t = _setup_thesis(tranche_stage=1)
        assert tranche_gate.compute_margin_pct(t, self._hub("BUILD"), False) == 0.30

    def test_build_stage2_is_20pct(self):
        from backend.services.mlto import tranche_gate
        t = _setup_thesis(tranche_stage=2)
        assert tranche_gate.compute_margin_pct(t, self._hub("BUILD"), False) == 0.20

    def test_build_stage3_is_0pct(self):
        """stage≥3 → 0%（tranche 已耗尽，不再加仓）。"""
        from backend.services.mlto import tranche_gate
        t = _setup_thesis(tranche_stage=3)
        assert tranche_gate.compute_margin_pct(t, self._hub("BUILD"), False) == 0.0

    def test_nibble_stage0_is_15pct(self):
        from backend.services.mlto import tranche_gate
        t = _setup_thesis(tranche_stage=0)
        assert tranche_gate.compute_margin_pct(t, self._hub("NIBBLE"), False) == 0.15

    def test_nibble_stage1plus_is_10pct(self):
        from backend.services.mlto import tranche_gate
        t = _setup_thesis(tranche_stage=2)
        assert tranche_gate.compute_margin_pct(t, self._hub("NIBBLE"), False) == 0.10

    def test_wait_returns_zero(self):
        from backend.services.mlto import tranche_gate
        t = _setup_thesis(tranche_stage=0)
        assert tranche_gate.compute_margin_pct(t, self._hub("WAIT"), False) == 0.0


# ═══════════════════════════════════════════════════════════════════
# B. try_execute_independent_agent_open 接收并透传 tranche_margin_pct
# ═══════════════════════════════════════════════════════════════════
class TestTryExecutePassesMarginPct:
    def test_margin_pct_lands_in_proposal_extra(self, monkeypatch):
        """try_execute_independent_agent_open 把 tranche_margin_pct 透传到
        TradeProposal.extra，供 proposal_execution 作为 size 乘子。"""
        from backend.services.full_auto import midlong_helpers

        captured = {}

        class _Host:
            def get_trading_account_id(self, *a, **k):
                return 1
            def append_event(self, *a, **k):
                return None
            def evaluate_and_execute_proposal(self, *, db, session, proposal,
                                              market_summary, session_mode):
                captured["proposal"] = proposal
                return True

        # 跳过所有前置门禁（fixed_symbol / mtf / cooldown / structure_stop）
        monkeypatch.setattr(
            "backend.services.auto_coin_selector.get_fixed_symbols_for_session",
            lambda *a, **k: set(), raising=False,
        )
        monkeypatch.setattr(
            "backend.services.decision_core.midlong_mtf_constraint.evaluate_midlong_mtf_constraint",
            lambda **k: SimpleNamespace(veto=False, size_multiplier=1.0, reason=""),
            raising=False,
        )
        # [fail-closed / chop 适配] 无真实市场指标：跳过周线缺失与震荡判定
        monkeypatch.setattr(
            "backend.services.mlto.midlong_trade_design.is_chop_regime",
            lambda *a, **k: (False, ""),
            raising=False,
        )
        monkeypatch.setattr(
            "backend.config.settings.MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE",
            False, raising=False,
        )
        monkeypatch.setattr(
            "backend.config.settings.MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT",
            False, raising=False,
        )
        monkeypatch.setattr(
            midlong_helpers, "inject_midlong_indicators", lambda *a, **k: None, raising=False,
        )

        ok = midlong_helpers.try_execute_independent_agent_open(
            db=None,
            session=SimpleNamespace(status="running", session_id="s1"),
            sym="BTC", tier="long", action="buy", confidence=60,
            trade_nature="trend_follow",
            sl_pct=0.08, tp_pct=0.16,
            # [fail-closed 适配] 长线开仓要求本币周线 indicators_1w 已注入
            market_summary={"BTC": {"indicators_1w": {"ema20": 1.0}}},
            host=_Host(),
            tranche_margin_pct=0.20,
        )
        assert ok is True
        proposal = captured["proposal"]
        # tranche_margin_pct 经 from_agent(**extra) 落到 extra
        assert proposal.extra.get("tranche_margin_pct") == 0.20

    def test_default_margin_pct_is_1(self, monkeypatch):
        """未传 tranche_margin_pct → 默认 1.0（不缩，向后兼容）。"""
        from backend.services.full_auto import midlong_helpers

        captured = {}

        class _Host:
            def get_trading_account_id(self, *a, **k):
                return 1
            def append_event(self, *a, **k):
                return None
            def evaluate_and_execute_proposal(self, *, db, session, proposal,
                                              market_summary, session_mode):
                captured["proposal"] = proposal
                return True

        monkeypatch.setattr(
            "backend.services.auto_coin_selector.get_fixed_symbols_for_session",
            lambda *a, **k: set(), raising=False,
        )
        monkeypatch.setattr(
            "backend.services.decision_core.midlong_mtf_constraint.evaluate_midlong_mtf_constraint",
            lambda **k: SimpleNamespace(veto=False, size_multiplier=1.0, reason=""),
            raising=False,
        )
        # [fail-closed / chop 适配] 无真实市场指标：跳过周线缺失与震荡判定
        monkeypatch.setattr(
            "backend.services.mlto.midlong_trade_design.is_chop_regime",
            lambda *a, **k: (False, ""),
            raising=False,
        )
        monkeypatch.setattr(
            "backend.config.settings.MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE",
            False, raising=False,
        )
        monkeypatch.setattr(
            "backend.config.settings.MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT",
            False, raising=False,
        )
        monkeypatch.setattr(
            midlong_helpers, "inject_midlong_indicators", lambda *a, **k: None, raising=False,
        )

        midlong_helpers.try_execute_independent_agent_open(
            db=None,
            session=SimpleNamespace(status="running", session_id="s1"),
            sym="BTC", tier="long", action="buy", confidence=60,
            trade_nature="trend_follow",
            sl_pct=0.08, tp_pct=0.16,
            # [fail-closed 适配] 长线开仓要求本币周线 indicators_1w 已注入
            market_summary={"BTC": {"indicators_1w": {"ema20": 1.0}}},
            host=_Host(),
        )
        assert captured["proposal"].extra.get("tranche_margin_pct") == 1.0


# ═══════════════════════════════════════════════════════════════════
# C. proposal_execution 把 tranche_margin_pct 作为 size 乘子
# ═══════════════════════════════════════════════════════════════════
class TestProposalExecutionAppliesMarginPct:
    def _proposal(self, tranche_mult=1.0):
        from backend.services.decision_core.proposal import TradeProposal
        return TradeProposal.from_agent(
            sym="BTC", tier="long", action="buy", confidence=60,
            trade_nature="trend_follow", sl_pct=0.08, tp_pct=0.16,
            source_lane="trend_independent", tranche_margin_pct=tranche_mult,
        )

    def test_margin_pct_scales_size_multiplier(self, monkeypatch):
        """tranche_margin_pct=0.30 → dec['size_multiplier'] *= 0.30。"""
        from backend.services.full_auto import proposal_execution
        from backend.services.decision_core.execute_proposal import EvaluateVerdict

        # V5Gate 放行，无 size 缩仓
        monkeypatch.setattr(
            "backend.services.decision_core.execute_proposal.evaluate_proposal",
            lambda **k: EvaluateVerdict(allowed=True, reason="", adjustments={}),
            raising=False,
        )
        # budget 放行，无缩仓
        monkeypatch.setattr(
            "backend.services.budget_service.budget_service.scale_factor_for_layer",
            lambda *a, **k: 1.0, raising=False,
        )
        monkeypatch.setattr(
            "backend.services.orchestrator_derivatives.inject_derivatives_into_market_summary",
            lambda *a, **k: None, raising=False,
        )

        captured_dec = {}

        class _Host:
            midlong_persistence_allow = staticmethod(lambda *a, **k: True)
            resolve_independent_strategy = staticmethod(lambda *a, **k: SimpleNamespace(strategy_id="s1"))
            session_trading_mode = staticmethod(lambda *a, **k: "paper")
            persist_tcp_snapshot = staticmethod(lambda *a, **k: None)
            build_portfolio_for_agents = staticmethod(lambda *a, **k: {"balance": {"total_equity": 10000}})
            decision_price_consistency_ok = staticmethod(lambda *a, **k: (True, ""))
            append_event = staticmethod(lambda *a, **k: None)
            live_constitutional_pre_trade_check = staticmethod(lambda *a, **k: (True, ""))
            execute_live_trade = staticmethod(lambda *a, **k: None)
            safe_commit = staticmethod(lambda *a, **k: True)
            record_midlong_factor_snapshots = staticmethod(lambda *a, **k: None)
            def execute_paper_trade(self, db, session, strat, dec):
                captured_dec.update(dec)
                return True

        ok = proposal_execution.evaluate_and_execute_proposal(
            db=None,
            session=SimpleNamespace(paper_account_id=1, status="running"),
            proposal=self._proposal(tranche_mult=0.30),
            market_summary={"BTC": {}},
            host=_Host(),
            session_mode="running",
        )
        assert ok is True
        # size_multiplier 应被乘上 0.30（budget=1.0, V5=1.0, MTF=1.0 基线下）
        assert abs(float(captured_dec.get("size_multiplier", 1.0)) - 0.30) < 1e-9

    def test_margin_pct_zero_blocks_open(self, monkeypatch):
        """tranche_margin_pct=0.0（tranche 耗尽）→ 拒绝新开，返回 False。"""
        from backend.services.full_auto import proposal_execution
        from backend.services.decision_core.execute_proposal import EvaluateVerdict

        monkeypatch.setattr(
            "backend.services.decision_core.execute_proposal.evaluate_proposal",
            lambda **k: EvaluateVerdict(allowed=True, reason="", adjustments={}),
            raising=False,
        )
        monkeypatch.setattr(
            "backend.services.budget_service.budget_service.scale_factor_for_layer",
            lambda *a, **k: 1.0, raising=False,
        )
        monkeypatch.setattr(
            "backend.services.orchestrator_derivatives.inject_derivatives_into_market_summary",
            lambda *a, **k: None, raising=False,
        )

        called = {"paper": 0}

        class _Host:
            midlong_persistence_allow = staticmethod(lambda *a, **k: True)
            resolve_independent_strategy = staticmethod(lambda *a, **k: SimpleNamespace(strategy_id="s1"))
            session_trading_mode = staticmethod(lambda *a, **k: "paper")
            persist_tcp_snapshot = staticmethod(lambda *a, **k: None)
            build_portfolio_for_agents = staticmethod(lambda *a, **k: {"balance": {"total_equity": 10000}})
            decision_price_consistency_ok = staticmethod(lambda *a, **k: (True, ""))
            append_event = staticmethod(lambda *a, **k: None)
            live_constitutional_pre_trade_check = staticmethod(lambda *a, **k: (True, ""))
            execute_live_trade = staticmethod(lambda *a, **k: None)
            safe_commit = staticmethod(lambda *a, **k: True)
            record_midlong_factor_snapshots = staticmethod(lambda *a, **k: None)
            def execute_paper_trade(self, *a, **k):
                called["paper"] += 1
                return True

        ok = proposal_execution.evaluate_and_execute_proposal(
            db=None,
            session=SimpleNamespace(paper_account_id=1, status="running"),
            proposal=self._proposal(tranche_mult=0.0),
            market_summary={"BTC": {}},
            host=_Host(),
            session_mode="running",
        )
        assert ok is False
        assert called["paper"] == 0  # 0% margin 在下单前就拦下


# ═══════════════════════════════════════════════════════════════════
# D & E. orchestrator: advance_tranche 仅在 action != hold 时推进
# ═══════════════════════════════════════════════════════════════════
class TestAdvanceOnActionOnly:
    def test_hold_neutral_direction_does_not_advance(self, monkeypatch):
        """gate 通过但 hub 方向 neutral → action=hold → tranche 不推进。

        复现 Bug2 的核心：过了 gate 不等于要下单，方向中性时不应推进 tranche。
        """
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="neutral", hub_action="BUILD")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=0)
        packet = _make_packet(price=59000.0, portfolio={})

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action == "hold"
        assert t.tranche_stage == 0  # 未推进

    def test_buy_advances_tranche(self, monkeypatch):
        """gate 通过 + hub 方向 long → action=buy → tranche 推进到 1。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="long", hub_action="BUILD")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=0)
        packet = _make_packet(price=59000.0, portfolio={})

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action == "buy"
        assert t.tranche_stage == 1

    def test_sell_advances_tranche(self, monkeypatch):
        """gate 通过 + hub 方向 short → action=sell → tranche 推进。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="short", hub_action="BUILD")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=1)
        packet = _make_packet(price=59000.0, portfolio={})

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action == "sell"
        assert t.tranche_stage == 2

    def test_wait_action_does_not_advance(self, monkeypatch):
        """hub.action=WAIT → direction_to_action 返回 hold → 不推进。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="long", hub_action="WAIT")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=0)
        packet = _make_packet(price=59000.0, portfolio={})

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action == "hold"
        assert t.tranche_stage == 0


# ═══════════════════════════════════════════════════════════════════
# F. stage≥3 只能经 3 次确认开仓到达（与 D/E 组合）
# ═══════════════════════════════════════════════════════════════════
class TestStageThreeOnlyAfterThreeOpens:
    def test_three_buy_ticks_reach_stage3(self, monkeypatch):
        """连续 3 个 buy tick → stage 0→1→2→3。stage3 时 margin=0%。"""
        from backend.services.mlto import tranche_gate
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="long", hub_action="BUILD")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=0)
        packet = _make_packet(price=59000.0, portfolio={})

        hub = _STUB_HUB[0]
        # tick 1: stage 0 → 1
        MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert t.tranche_stage == 1
        assert tranche_gate.compute_margin_pct(t, hub, False) == 0.30
        # tick 2: stage 1 → 2
        MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert t.tranche_stage == 2
        assert tranche_gate.compute_margin_pct(t, hub, False) == 0.20
        # tick 3: stage 2 → 3
        MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert t.tranche_stage == 3
        # stage≥3 → margin 0%（tranche 已耗尽）
        assert tranche_gate.compute_margin_pct(t, hub, False) == 0.0

    def test_three_hold_ticks_never_reach_stage3(self, monkeypatch):
        """连续 3 个 hold（neutral）tick → stage 始终 0，不会被推到锁死区。

        这是 Bug2 的回归保护：此前 3 次 hold 也会推进 tranche 到 stage3，
        触发 compute_margin_pct=0% 永久锁死。
        """
        from backend.services.mlto import tranche_gate
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="neutral", hub_action="BUILD")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=0)
        packet = _make_packet(price=59000.0, portfolio={})

        hub = _STUB_HUB[0]
        for _ in range(3):
            r = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
            assert r.action == "hold"
        assert t.tranche_stage == 0
        # 仍能开（margin != 0）
        assert tranche_gate.compute_margin_pct(t, hub, False) == 0.30


# ═══════════════════════════════════════════════════════════════════
# G. orchestrator: 外部平仓后停在锁死档（stage≥3 & 无持仓）→ 复位
# ═══════════════════════════════════════════════════════════════════
class TestResetOnExternalClose:
    def test_resets_when_stage3_and_no_position(self, monkeypatch):
        """[Fix3] 3 次开仓到达 stage3 后仓位被 SL/TP/staged 在别处平掉 →
        下一个 MLTO tick 看到 stage=3 且无持仓 → 复位到 0，解除永久锁死。"""
        from backend.services.mlto import tranche_gate
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="long", hub_action="BUILD")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=3)
        hub = _STUB_HUB[0]
        # stage≥3 复位前：margin=0%（锁死）
        assert tranche_gate.compute_margin_pct(t, hub, False) == 0.0
        # 无 portfolio（仓位已被外部平掉）
        packet = _make_packet(price=59000.0, portfolio={})

        MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        # 复位后 stage=0 → 随后 buy 推到 1，margin 恢复 30%
        assert t.tranche_stage == 1
        assert tranche_gate.compute_margin_pct(t, hub, False) == 0.30

    def test_reset_unlocks_permanent_lock(self, monkeypatch):
        """[Fix3 核心] 没有 Fix3 时 stage3 无持仓会永远停在 stage3（margin=0%，
        gate 放行也开不出），是任务描述的「permanent lock until invalidation reset」。
        有 Fix3 后 stage3 无持仓 → 复位 → 能重新开仓。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="long", hub_action="BUILD")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=3)
        packet = _make_packet(price=59000.0, portfolio={})

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        # 复位 → 开仓成功（action=buy），不再是 hold/锁死
        assert result.action == "buy"
        assert t.tranche_stage == 1


# ═══════════════════════════════════════════════════════════════════
# H. 不误复位：首次开仓（stage 0）和分档建仓进行中（stage 1/2）
# ═══════════════════════════════════════════════════════════════════
class TestNoFalseResetOnFirstOpen:
    def test_stage0_no_position_advances_normally(self, monkeypatch):
        """[Fix3 安全性] 首次开仓 tick：tranche=0 且无持仓 → 不触发复位，
        随后正常 advance 到 1。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="long", hub_action="BUILD")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=0)
        packet = _make_packet(price=59000.0, portfolio={})

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action == "buy"
        assert t.tranche_stage == 1  # 正常推进，未被错误地停在 0

    def test_stage1_no_position_not_reset(self, monkeypatch):
        """[Fix3 安全性] 分档建仓进行中：stage=1 且无持仓（开仓与持仓落库之间
        天然有 1 tick 延迟）→ 不复位。stage 1 的 margin=30% 本来就能继续开，
        复位会错误打断正在进行的分档建仓。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="long", hub_action="BUILD")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=1)
        packet = _make_packet(price=59000.0, portfolio={})

        MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        # 不复位 → 正常 advance 到 2（不是被打回 0 再到 1）
        assert t.tranche_stage == 2

    def test_stage2_no_position_not_reset(self, monkeypatch):
        """[Fix3 安全性] stage=2 且无持仓 → 不复位，正常推进到 3。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch, hub_direction="long", hub_action="BUILD")
        _stub_open_gate_allow(monkeypatch, allowed=True)
        t = _setup_thesis(tranche_stage=2)
        packet = _make_packet(price=59000.0, portfolio={})

        MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert t.tranche_stage == 3  # 不复位 → 正常 advance 到 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
