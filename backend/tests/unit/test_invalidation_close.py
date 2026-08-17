"""阶段3e: invalidation 驱动 close 路径 — 单元测试

补 MLTO "无退出缺口"：原 MltoTickResult 只发 buy/sell/hold, 从不发 close。
LLM 产出的 invalidation 无人消费。本测试覆盖新增的 close 路径。

覆盖:
  A. _invalidation_triggered: 价格类(operator)各分支 + 叙事类(返回 False)
  B. orchestrator 在 invalidation 触发 + 有持仓 时发 action="close"
  C. orchestrator 无持仓时即便 invalidation 触发也不发 close(防幽灵平仓)
  D. orchestrator 无 invalidation 时不发 close(向后兼容)
  E. mlto_cycle._mlto_close_symbol: 按 thesis.direction 调 paper_engine.close_position
  F. tranche_gate.reset_tranche: tranche_stage 归 0(决策6)
  G. orchestrator close 时同步 reset tranche
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

# 确保可 import backend.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ═══════════════════════════════════════════════════════════════════
# A. _invalidation_triggered
# ═══════════════════════════════════════════════════════════════════
class TestInvalidationTriggered:
    def test_lt_below_level_triggers(self):
        from backend.services.mlto.orchestrator import _invalidation_triggered
        inv = {"price": 60000.0, "operator": "<", "condition": "跌破周线支撑"}
        assert _invalidation_triggered(inv, 59000.0) is True

    def test_lt_above_level_not_triggers(self):
        from backend.services.mlto.orchestrator import _invalidation_triggered
        inv = {"price": 60000.0, "operator": "<", "condition": "跌破周线支撑"}
        assert _invalidation_triggered(inv, 61000.0) is False

    def test_lt_exactly_at_level_not_triggers(self):
        """严格小于: 价格 == level 不触发(operator '<' 而非 '<=')。"""
        from backend.services.mlto.orchestrator import _invalidation_triggered
        inv = {"price": 60000.0, "operator": "<"}
        assert _invalidation_triggered(inv, 60000.0) is False

    def test_gt_above_level_triggers(self):
        from backend.services.mlto.orchestrator import _invalidation_triggered
        inv = {"price": 60000.0, "operator": ">", "condition": "突破止损"}
        assert _invalidation_triggered(inv, 60500.0) is True

    def test_gt_below_level_not_triggers(self):
        from backend.services.mlto.orchestrator import _invalidation_triggered
        inv = {"price": 60000.0, "operator": ">"}
        assert _invalidation_triggered(inv, 59500.0) is False

    def test_narrative_only_returns_false(self):
        """叙事类 invalidation(无 price)由 LLM 复评, 机器不触发 close。"""
        from backend.services.mlto.orchestrator import _invalidation_triggered
        inv = {"narrative": "趋势结构破坏", "condition": "周线下行"}
        assert _invalidation_triggered(inv, 59000.0) is False

    def test_narrative_with_zero_price_returns_false(self):
        from backend.services.mlto.orchestrator import _invalidation_triggered
        inv = {"price": 0, "narrative": "结构破坏"}
        assert _invalidation_triggered(inv, 59000.0) is False

    def test_missing_operator_returns_false(self):
        """有 price 但缺 operator → 无法机器判定。"""
        from backend.services.mlto.orchestrator import _invalidation_triggered
        inv = {"price": 60000.0, "condition": "跌破支撑"}
        assert _invalidation_triggered(inv, 59000.0) is False

    def test_empty_invalidation(self):
        from backend.services.mlto.orchestrator import _invalidation_triggered
        assert _invalidation_triggered({}, 59000.0) is False
        assert _invalidation_triggered(None, 59000.0) is False

    def test_zero_current_price_returns_false(self):
        """packet.price=0(数据缺失)时不误触发。"""
        from backend.services.mlto.orchestrator import _invalidation_triggered
        inv = {"price": 60000.0, "operator": "<"}
        assert _invalidation_triggered(inv, 0.0) is False

    def test_le_ge_operators(self):
        from backend.services.mlto.orchestrator import _invalidation_triggered
        assert _invalidation_triggered({"price": 60000.0, "operator": "<="}, 60000.0) is True
        assert _invalidation_triggered({"price": 60000.0, "operator": ">="}, 60000.0) is True
        assert _invalidation_triggered({"price": 60000.0, "operator": "≥"}, 60001.0) is True


# ═══════════════════════════════════════════════════════════════════
# 测试辅助: 构造一个最小化 packet + stub 掉 MLTO 上游 pipeline
# ═══════════════════════════════════════════════════════════════════
def _make_packet(symbol="BTC", tier="long", price=59000.0, portfolio=None):
    from backend.services.mlto.types import PerceptionPacket
    return PerceptionPacket(
        symbol=symbol, tier=tier, session_id="s1", ts=0.0, price=price,
        market_summary_sym={}, orchestrator={}, quant_brief={},
        analyst_reports={}, portfolio=portfolio or {},
    )


def _stub_pipeline(monkeypatch):
    """stub 掉 run_tick 内的上游(避免依赖 DB/LLM/计算)。

    让 thesis_store.get_or_create 返回一个可控 thesis;
    evidence/qual/quant/debate/hub 都走最小桩。
    """
    from backend.services.mlto import (
        evidence_ingest, layered_memory, qual_layer, quant_layer,
        decision_hub, debate_layer, thesis_store,
    )
    from backend.services.mlto.types import QualUpdateResult

    def _fake_get_or_create(session_id, symbol, tier, regime_hash, db=None):
        # 返回测试里注入的 thesis(通过 packet 透传不方便, 改用模块变量)
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
        lambda *a, **k: QualUpdateResult(direction="long", thesis_summary="x"),
        raising=False,
    )
    monkeypatch.setattr(quant_layer, "compute", lambda *a, **k: [], raising=False)
    from backend.services.mlto.types import HubDecision
    _fake_hub = HubDecision(
        action="BUILD", direction="long", composite=0.7, adjusted=0.7,
        consistency=0.7, open_readiness=60, reason_text="stub",
    )
    monkeypatch.setattr(
        decision_hub, "fuse_signals", lambda *a, **k: _fake_hub, raising=False,
    )
    monkeypatch.setattr(debate_layer, "should_debate", lambda *a, **k: False, raising=False)


# 模块级 holder 用于把 thesis 注入到 stubbed get_or_create
_STUB_THESIS = []


# ═══════════════════════════════════════════════════════════════════
# B. orchestrator: invalidation + 持仓 → close
# ═══════════════════════════════════════════════════════════════════
class TestOrchestratorEmitsClose:
    def _setup_thesis(self, invalidation):
        from backend.services.mlto.types import ThesisDTO
        t = ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
            direction="long", open_readiness=60, tranche_stage=2,
            invalidation=invalidation,
        )
        _STUB_THESIS.clear()
        _STUB_THESIS.append(t)
        return t

    def test_close_when_invalidation_triggers_and_has_position(self, monkeypatch):
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch)
        t = self._setup_thesis({"price": 60000.0, "operator": "<", "condition": "跌破周线支撑"})
        packet = _make_packet(price=59000.0, portfolio={"positions": [{"symbol": "BTC"}]})

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action == "close"
        assert "invalidation_triggered" in result.reason
        assert result.thesis is t

    def test_no_close_when_no_position(self, monkeypatch):
        """无持仓 → 不发 close(防幽灵平仓), 继续走 open_gate 路径。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        from backend.services.mlto import open_gate
        _stub_pipeline(monkeypatch)
        self._setup_thesis({"price": 60000.0, "operator": "<", "condition": "跌破支撑"})
        # 无 portfolio
        packet = _make_packet(price=59000.0, portfolio={})
        # open_gate 会拦下中性/无信号, 走 hold; 关键是不应得 close
        monkeypatch.setattr(open_gate, "allow", lambda *a, **k: (True, ""), raising=False)

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action != "close"

    def test_no_close_when_narrative_invalidation(self, monkeypatch):
        """叙事类 invalidation(无 price) → 机器不触发, 交给 LLM 复评。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        from backend.services.mlto import open_gate
        _stub_pipeline(monkeypatch)
        self._setup_thesis({"narrative": "趋势结构破坏", "condition": "周线下行"})
        packet = _make_packet(price=59000.0, portfolio={"positions": [{"symbol": "BTC"}]})
        monkeypatch.setattr(open_gate, "allow", lambda *a, **k: (True, ""), raising=False)

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action != "close"

    def test_no_close_when_invalidation_not_yet_breached(self, monkeypatch):
        """有持仓 + 价格类 invalidation 但价格未跌破 → 不 close。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        from backend.services.mlto import open_gate
        _stub_pipeline(monkeypatch)
        self._setup_thesis({"price": 60000.0, "operator": "<", "condition": "跌破支撑"})
        packet = _make_packet(price=61000.0, portfolio={"positions": [{"symbol": "BTC"}]})
        monkeypatch.setattr(open_gate, "allow", lambda *a, **k: (True, ""), raising=False)

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action != "close"

    def test_no_close_when_no_invalidation(self, monkeypatch):
        """向后兼容: thesis 无 invalidation → 永不 close。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        from backend.services.mlto import open_gate
        _stub_pipeline(monkeypatch)
        self._setup_thesis({})
        packet = _make_packet(price=59000.0, portfolio={"positions": [{"symbol": "BTC"}]})
        monkeypatch.setattr(open_gate, "allow", lambda *a, **k: (True, ""), raising=False)

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action != "close"


# ═══════════════════════════════════════════════════════════════════
# C. orchestrator close 时 reset tranche(决策6)
# ═══════════════════════════════════════════════════════════════════
class TestOrchestratorCloseResetsTranche:
    def test_tranche_resets_on_close(self, monkeypatch):
        from backend.services.mlto.orchestrator import MltoOrchestrator
        _stub_pipeline(monkeypatch)
        from backend.services.mlto.types import ThesisDTO
        t = ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
            direction="long", open_readiness=60, tranche_stage=3,
            invalidation={"price": 60000.0, "operator": "<", "condition": "跌破支撑"},
        )
        _STUB_THESIS.clear()
        _STUB_THESIS.append(t)
        packet = _make_packet(price=59000.0, portfolio={"positions": [{"symbol": "BTC"}]})

        result = MltoOrchestrator().run_tick(packet, db=None, portfolio=packet.portfolio)
        assert result.action == "close"
        assert t.tranche_stage == 0  # 决策6: close 后 tranche 归 0


# ═══════════════════════════════════════════════════════════════════
# E. _mlto_close_symbol: 按 thesis.direction 平仓
# ═══════════════════════════════════════════════════════════════════
class TestMltoCloseSymbol:
    def _session(self):
        return SimpleNamespace(paper_account_id=42)

    def test_long_direction_closes_long_side(self, monkeypatch):
        from backend.services.full_auto import mlto_cycle
        from backend.services.mlto.types import ThesisDTO

        calls = []
        class _PE:
            @staticmethod
            def close_position(db, acct_id, sym, side, reason="manual", **k):
                calls.append((sym, side, reason))
                return {"closed_fully": True}
        monkeypatch.setattr(
            "backend.services.paper_trading_engine.paper_engine", _PE, raising=False,
        )
        t = ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
            direction="long",
        )
        ok = mlto_cycle._mlto_close_symbol(
            db=None, session=self._session(), symbol="btc",
            thesis=t, reason="invalidation_triggered: 跌破支撑",
        )
        assert ok is True
        assert calls == [("BTC", "long", "invalidation_triggered: 跌破支撑")]
        assert len(calls) == 1  # 只平 long 一边

    def test_short_direction_closes_short_side(self, monkeypatch):
        from backend.services.full_auto import mlto_cycle
        from backend.services.mlto.types import ThesisDTO

        calls = []
        class _PE:
            @staticmethod
            def close_position(db, acct_id, sym, side, reason="manual", **k):
                calls.append((sym, side))
                return {"closed_fully": True}
        monkeypatch.setattr(
            "backend.services.paper_trading_engine.paper_engine", _PE, raising=False,
        )
        t = ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
            direction="short",
        )
        ok = mlto_cycle._mlto_close_symbol(
            db=None, session=self._session(), symbol="BTC", thesis=t, reason="x",
        )
        assert ok is True
        assert calls == [("BTC", "short")]

    def test_neutral_direction_tries_both_sides(self, monkeypatch):
        from backend.services.full_auto import mlto_cycle
        from backend.services.mlto.types import ThesisDTO

        calls = []
        class _PE:
            @staticmethod
            def close_position(db, acct_id, sym, side, reason="manual", **k):
                calls.append(side)
                # 模拟只有 short 仓位存在
                return {"closed_fully": True} if side == "short" else None
        monkeypatch.setattr(
            "backend.services.paper_trading_engine.paper_engine", _PE, raising=False,
        )
        t = ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
            direction="neutral",
        )
        ok = mlto_cycle._mlto_close_symbol(
            db=None, session=self._session(), symbol="BTC", thesis=t, reason="x",
        )
        assert ok is True
        assert "long" in calls and "short" in calls  # 两边都尝试

    def test_no_position_returns_false(self, monkeypatch):
        """close_position 返回 None(无仓) → 返回 False。"""
        from backend.services.full_auto import mlto_cycle
        from backend.services.mlto.types import ThesisDTO

        class _PE:
            @staticmethod
            def close_position(db, acct_id, sym, side, reason="manual", **k):
                return None
        monkeypatch.setattr(
            "backend.services.paper_trading_engine.paper_engine", _PE, raising=False,
        )
        t = ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
            direction="long",
        )
        ok = mlto_cycle._mlto_close_symbol(
            db=None, session=self._session(), symbol="BTC", thesis=t, reason="x",
        )
        assert ok is False

    def test_no_account_id_returns_false(self, monkeypatch):
        from backend.services.full_auto import mlto_cycle
        from backend.services.mlto.types import ThesisDTO

        called = {"n": 0}
        class _PE:
            @staticmethod
            def close_position(*a, **k):
                called["n"] += 1
                return None
        monkeypatch.setattr(
            "backend.services.paper_trading_engine.paper_engine", _PE, raising=False,
        )
        t = ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
            direction="long",
        )
        ok = mlto_cycle._mlto_close_symbol(
            db=None, session=SimpleNamespace(paper_account_id=None, account_id=None),
            symbol="BTC", thesis=t, reason="x",
        )
        assert ok is False
        assert called["n"] == 0  # 没调到 close_position


# ═══════════════════════════════════════════════════════════════════
# F. tranche_gate.reset_tranche
# ═══════════════════════════════════════════════════════════════════
class TestResetTranche:
    def test_resets_to_zero(self):
        from backend.services.mlto import tranche_gate
        from backend.services.mlto.types import ThesisDTO
        t = ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")
        t.tranche_stage = 3
        tranche_gate.reset_tranche(t)
        assert t.tranche_stage == 0

    def test_reset_idempotent(self):
        from backend.services.mlto import tranche_gate
        from backend.services.mlto.types import ThesisDTO
        t = ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")
        tranche_gate.reset_tranche(t)
        tranche_gate.reset_tranche(t)
        assert t.tranche_stage == 0

    def test_advance_after_reset_starts_from_zero(self):
        """reset 后再 advance 应从 stage 1 开始(不是停在 0, 也不是接着旧的 3)。"""
        from backend.services.mlto import tranche_gate
        from backend.services.mlto.types import ThesisDTO
        t = ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")
        t.tranche_stage = 3
        tranche_gate.reset_tranche(t)
        tranche_gate.advance_tranche(t)
        assert t.tranche_stage == 1


# ═══════════════════════════════════════════════════════════════════
# [2026-08-17] TestExecuteLaneCloseDispatch 已删：
# execute_mlto_lane 函数已删除（旧长线 MLTO lane LLM 下线）。
# _mlto_close_symbol 本身仍被 _maintain_mlto_theses_for_session 的 close 分支使用，
# 相关测试保留于本文文件其它处。
# ═══════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
