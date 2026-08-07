"""Phase A: MLTO invalidation→close 三个静默失败修复 — 单元测试。

覆盖:
  Bug 1: _has_position 空 portfolio 但 DB 有持仓 → True
  Bug 2: thesis.should_close=True → orchestrator 发 action="close"
         (叙事类 invalidation 原来永远不触发 close)
  Bug 3: _mlto_close_symbol 用 DB 实际 side, 不从 thesis.direction 推断
         (thesis 失效后方向可能已翻转 → 旧逻辑用翻转方向平仓返回 None)
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

# 确保可 import backend.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ═══════════════════════════════════════════════════════════════════
# 测试辅助
# ═══════════════════════════════════════════════════════════════════
def _make_packet(symbol="BTC", tier="long", price=59000.0, portfolio=None, account_id=42):
    from backend.services.mlto.types import PerceptionPacket
    return PerceptionPacket(
        symbol=symbol, tier=tier, session_id="s1", ts=0.0, price=price,
        market_summary_sym={}, orchestrator={}, quant_brief={},
        analyst_reports={}, portfolio=portfolio or {}, account_id=account_id,
    )


def _stub_pipeline(monkeypatch, thesis):
    """stub 掉 run_tick 内的上游(避免依赖 DB/LLM/计算)。

    让 thesis_store.get_or_create 返回注入的 thesis; 其余走最小桩。
    """
    from backend.services.mlto import (
        evidence_ingest, layered_memory, qual_layer, quant_layer,
        decision_hub, debate_layer, thesis_store,
    )

    monkeypatch.setattr(
        thesis_store, "get_or_create",
        lambda *a, **k: thesis, raising=False,
    )
    monkeypatch.setattr(thesis_store, "apply_regime_reset", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(thesis_store, "apply_llm_update", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(thesis_store, "append_event", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(thesis_store, "update_hub", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(thesis_store, "_persist", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(evidence_ingest, "ingest_tick", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(evidence_ingest, "build_regime_hash", lambda *a, **k: "", raising=False)
    monkeypatch.setattr(layered_memory, "retrieve", lambda *a, **k: [], raising=False)
    from backend.services.mlto.types import QualUpdateResult
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


def _make_thesis(**kw):
    from backend.services.mlto.types import ThesisDTO
    defaults = dict(
        thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
        direction="long", open_readiness=60, tranche_stage=2,
    )
    defaults.update(kw)
    return ThesisDTO(**defaults)


def _fake_db_with_position(symbol="BTC", side="long", account_id=42):
    """构造一个假 db: query(...).filter(...).first() 返回一个 open 仓位。"""
    pos = SimpleNamespace(symbol=symbol.upper(), side=side, status="open")
    db = MagicMock()
    query = MagicMock()
    filtered = MagicMock()
    filtered.first.return_value = pos
    query.filter.return_value = filtered
    db.query.return_value = query
    return db, pos


def _fake_db_no_position():
    """构造一个假 db: query(...).first() 返回 None（无持仓）。"""
    db = MagicMock()
    query = MagicMock()
    filtered = MagicMock()
    filtered.first.return_value = None
    query.filter.return_value = filtered
    db.query.return_value = query
    return db


# ═══════════════════════════════════════════════════════════════════
# Bug 1: _has_position 查 DB 而非 portfolio dict
# ═══════════════════════════════════════════════════════════════════
class TestBug1HasPositionQueriesDB:
    def test_true_when_portfolio_empty_but_db_has_open_position(self):
        """portfolio={} 但 DB 有 open PaperPosition → True（核心修复点）。"""
        from backend.services.mlto.orchestrator import _has_position
        db, _ = _fake_db_with_position(symbol="BTC", side="long")
        packet = _make_packet(symbol="BTC", portfolio={})
        assert _has_position(packet, portfolio={}, db=db) is True

    def test_true_when_portfolio_missing_symbol_but_db_has_position(self):
        """portfolio 有别的 symbol 但 DB 有目标 symbol → True。"""
        from backend.services.mlto.orchestrator import _has_position
        db, _ = _fake_db_with_position(symbol="BTC", side="short")
        packet = _make_packet(symbol="BTC", portfolio={"positions": [{"symbol": "ETH"}]})
        assert _has_position(packet, portfolio=packet.portfolio, db=db) is True

    def test_false_when_db_has_no_open_position(self):
        """DB 无 open 仓位 → False（即便 portfolio 里有）。"""
        from backend.services.mlto.orchestrator import _has_position
        db = _fake_db_no_position()
        packet = _make_packet(symbol="BTC", portfolio={"positions": [{"symbol": "BTC"}]})
        # DB 是主路径：DB 说没有就没有（portfolio 兜底只在 DB 不可用时启用）
        assert _has_position(packet, portfolio=packet.portfolio, db=db) is False

    def test_falls_back_to_portfolio_when_db_none(self):
        """db=None 时回退到 portfolio dict（向后兼容）。"""
        from backend.services.mlto.orchestrator import _has_position
        packet = _make_packet(symbol="BTC", portfolio={"positions": [{"symbol": "BTC"}]})
        assert _has_position(packet, portfolio=packet.portfolio, db=None) is True

    def test_falls_back_to_portfolio_when_account_id_missing(self):
        """db 有但 account_id=None → 无法查 DB, 回退 portfolio。"""
        from backend.services.mlto.orchestrator import _has_position
        packet = _make_packet(symbol="BTC", portfolio={"positions": [{"symbol": "BTC"}]}, account_id=None)
        # db 不为 None 但 account_id 缺失 → 回退 portfolio
        assert _has_position(packet, portfolio=packet.portfolio, db=MagicMock()) is True

    def test_false_when_db_query_raises(self):
        """DB 查询抛异常 → 不阻塞决策, 回退 portfolio（空 → False）。"""
        from backend.services.mlto.orchestrator import _has_position
        db = MagicMock()
        db.query.side_effect = RuntimeError("db down")
        packet = _make_packet(symbol="BTC", portfolio={})
        assert _has_position(packet, portfolio={}, db=db) is False


# ═══════════════════════════════════════════════════════════════════
# Bug 2: thesis.should_close=True → orchestrator 发 close
# ═══════════════════════════════════════════════════════════════════
class TestBug2ShouldCloseTriggersClose:
    def test_should_close_emits_close_action(self, monkeypatch):
        """should_close=True + 有持仓 → action="close"（即便无价格类 invalidation）。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        t = _make_thesis(should_close=True, invalidation={})
        _stub_pipeline(monkeypatch, t)
        db, _ = _fake_db_with_position(symbol="BTC")
        packet = _make_packet(price=59000.0, portfolio={})

        result = MltoOrchestrator().run_tick(packet, db=db, portfolio=packet.portfolio)
        assert result.action == "close"
        assert "should_close" in result.reason

    def test_should_close_skipped_when_no_position(self, monkeypatch):
        """should_close=True 但无持仓 → 不发 close（防幽灵平仓）。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        from backend.services.mlto import open_gate
        t = _make_thesis(should_close=True, invalidation={})
        _stub_pipeline(monkeypatch, t)
        monkeypatch.setattr(open_gate, "allow", lambda *a, **k: (True, ""), raising=False)
        db = _fake_db_no_position()
        packet = _make_packet(price=59000.0, portfolio={})

        result = MltoOrchestrator().run_tick(packet, db=db, portfolio=packet.portfolio)
        assert result.action != "close"

    def test_should_close_resets_after_trigger(self, monkeypatch):
        """should_close 触发后立即复位为 False（一次性信号, 避免重复平仓）。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        t = _make_thesis(should_close=True, invalidation={})
        _stub_pipeline(monkeypatch, t)
        db, _ = _fake_db_with_position(symbol="BTC")
        packet = _make_packet(price=59000.0, portfolio={})

        MltoOrchestrator().run_tick(packet, db=db, portfolio=packet.portfolio)
        assert t.should_close is False

    def test_narrative_invalidation_now_triggers_via_should_close(self, monkeypatch):
        """叙事类 invalidation（无 price/operator）+ LLM should_close=True → close。

        这是 Bug2 的核心场景：原来叙事类 invalidation 永远不触发 close，
        现在靠 LLM 在 thesis_update 输出 should_close=true 驱动。
        """
        from backend.services.mlto.orchestrator import MltoOrchestrator
        t = _make_thesis(
            should_close=True,
            invalidation={"narrative": "趋势结构破坏", "condition": "周线下行"},
        )
        _stub_pipeline(monkeypatch, t)
        db, _ = _fake_db_with_position(symbol="BTC")
        packet = _make_packet(price=59000.0, portfolio={})

        result = MltoOrchestrator().run_tick(packet, db=db, portfolio=packet.portfolio)
        assert result.action == "close"

    def test_price_invalidation_takes_priority_over_should_close(self, monkeypatch):
        """价格类 invalidation 触发时, should_close 字段不被复位（它没参与）。"""
        from backend.services.mlto.orchestrator import MltoOrchestrator
        t = _make_thesis(
            should_close=False,
            invalidation={"price": 60000.0, "operator": "<", "condition": "跌破支撑"},
        )
        _stub_pipeline(monkeypatch, t)
        db, _ = _fake_db_with_position(symbol="BTC")
        packet = _make_packet(price=59000.0, portfolio={})

        result = MltoOrchestrator().run_tick(packet, db=db, portfolio=packet.portfolio)
        assert result.action == "close"
        assert "invalidation_triggered" in result.reason


class TestBug2QualParseShouldClose:
    def test_parse_result_reads_should_close_true(self):
        from backend.services.mlto.qual_layer import _parse_result
        raw = {"direction": "long", "thesis_summary": "x", "should_close": True}
        r = _parse_result(raw)
        assert r.should_close is True

    def test_parse_result_reads_should_close_false_default(self):
        from backend.services.mlto.qual_layer import _parse_result
        raw = {"direction": "long", "thesis_summary": "x"}
        r = _parse_result(raw)
        assert r.should_close is False

    def test_apply_llm_update_persists_should_close(self):
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import QualUpdateResult
        t = _make_thesis(should_close=False)
        qual = QualUpdateResult(direction="short", thesis_summary="thesis 破裂", should_close=True)
        thesis_store.apply_llm_update(t, qual, db=None)
        assert t.should_close is True

    def test_apply_llm_update_skipped_when_llm_silent(self):
        """LLM 未发言（无 summary/direction）→ 不覆盖已有 should_close。"""
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import QualUpdateResult
        t = _make_thesis(should_close=True)
        qual = QualUpdateResult(direction="", thesis_summary="", should_close=False)
        thesis_store.apply_llm_update(t, qual, db=None)
        assert t.should_close is True  # 未被覆盖


# ═══════════════════════════════════════════════════════════════════
# Bug 3: _mlto_close_symbol 用 DB 实际 side
# ═══════════════════════════════════════════════════════════════════
class TestBug3CloseUsesDBSide:
    def _session(self, acct_id=42):
        return SimpleNamespace(paper_account_id=acct_id)

    def test_db_long_position_closed_with_db_side_even_if_thesis_flipped(self, monkeypatch):
        """thesis.direction=short(已翻转) 但 DB 持仓是 long → 用 DB 的 long 平仓。

        这是 Bug3 核心场景：旧逻辑用 thesis.direction=short 去平 → 找不到 short 仓 → 返回 False。
        """
        from backend.services.full_auto import mlto_cycle
        from backend.services.mlto.types import ThesisDTO

        calls = []
        class _PE:
            @staticmethod
            def close_position(db, acct_id, sym, side, reason="manual", **k):
                calls.append(side)
                return {"closed_fully": True}
        monkeypatch.setattr(
            "backend.services.paper_trading_engine.paper_engine", _PE, raising=False,
        )
        t = ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
            direction="short",  # thesis 已翻转到反向
        )
        db, _ = _fake_db_with_position(symbol="BTC", side="long")  # 实际持仓 long
        ok = mlto_cycle._mlto_close_symbol(
            db=db, session=self._session(), symbol="BTC", thesis=t, reason="x",
        )
        assert ok is True
        assert calls == ["long"]  # 用 DB 实际 side, 不是 thesis.direction

    def test_db_short_position_closed_with_db_side(self, monkeypatch):
        from backend.services.full_auto import mlto_cycle
        from backend.services.mlto.types import ThesisDTO

        calls = []
        class _PE:
            @staticmethod
            def close_position(db, acct_id, sym, side, reason="manual", **k):
                calls.append(side)
                return {"closed_fully": True}
        monkeypatch.setattr(
            "backend.services.paper_trading_engine.paper_engine", _PE, raising=False,
        )
        t = ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
            direction="long",  # thesis 说 long
        )
        db, _ = _fake_db_with_position(symbol="BTC", side="short")  # 实际持仓 short
        ok = mlto_cycle._mlto_close_symbol(
            db=db, session=self._session(), symbol="BTC", thesis=t, reason="x",
        )
        assert ok is True
        assert calls == ["short"]  # 用 DB 实际 side

    def test_db_query_falls_back_to_thesis_when_no_position(self, monkeypatch):
        """DB 查不到 open 仓位 → 退回 thesis.direction 兜底。"""
        from backend.services.full_auto import mlto_cycle
        from backend.services.mlto.types import ThesisDTO

        calls = []
        class _PE:
            @staticmethod
            def close_position(db, acct_id, sym, side, reason="manual", **k):
                calls.append(side)
                return {"closed_fully": True}
        monkeypatch.setattr(
            "backend.services.paper_trading_engine.paper_engine", _PE, raising=False,
        )
        t = ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
            direction="long",
        )
        db = _fake_db_no_position()  # DB 无仓位
        ok = mlto_cycle._mlto_close_symbol(
            db=db, session=self._session(), symbol="BTC", thesis=t, reason="x",
        )
        assert ok is True
        assert calls == ["long"]  # thesis.direction 兜底

    def test_db_none_falls_back_to_thesis(self, monkeypatch):
        """db=None → 跳过 DB 查询, 用 thesis.direction（向后兼容旧测试）。"""
        from backend.services.full_auto import mlto_cycle
        from backend.services.mlto.types import ThesisDTO

        calls = []
        class _PE:
            @staticmethod
            def close_position(db, acct_id, sym, side, reason="manual", **k):
                calls.append(side)
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
        assert calls == ["short"]

    def test_returns_false_when_no_position_anywhere(self, monkeypatch):
        """DB 无仓 + thesis.direction 平仓也返回 None → False。"""
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
        db = _fake_db_no_position()
        ok = mlto_cycle._mlto_close_symbol(
            db=db, session=self._session(), symbol="BTC", thesis=t, reason="x",
        )
        assert ok is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
