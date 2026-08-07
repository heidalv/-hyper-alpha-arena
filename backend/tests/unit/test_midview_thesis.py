"""阶段2: thesis mid_view 子结构 — 单元测试

验证合并基础：mid 分析以子结构形式嵌入长线 thesis，且 mid_view=None 全链路
退化为现状（向后兼容）。

覆盖：
  A. MidViewDTO 构造 + 默认 + from_dict/to_dict 往返
  B. ThesisDTO.mid_view 向后兼容（None）+ 已设置
  C. qual_layer._parse_result 解析 mid_view（含缺失/异常容错）
  D. qual_layer._build_prompt 长线 prompt 含 mid_view 请求；中线不含
  E. thesis_store.apply_llm_update 持久化 mid_view（+ 不覆盖历史）
  F. thesis_store._persist / _row_to_dto 读写 mid_view_json
  G. quant_layer 产出 mid_timing 信号（+ 无 mid_view 时不产出）
  H. evidence_ingest 长线也 ingest mid_bias（+ mid 衰减覆盖）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

# 确保可 import backend.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ═══════════════════════════════════════════════════════════════════
# A. MidViewDTO 构造 + 默认 + 往返
# ═══════════════════════════════════════════════════════════════════
class TestMidViewDTO:
    def test_defaults(self):
        from backend.services.mlto.types import MidViewDTO
        mv = MidViewDTO()
        assert mv.direction == "neutral"
        assert mv.timing_score == 50
        assert mv.timing_rationale == ""
        assert mv.key_levels is None
        assert mv.invalidation_for_timing == ""
        assert mv.updated_at == 0.0

    def test_to_dict_roundtrip(self):
        from backend.services.mlto.types import MidViewDTO
        mv = MidViewDTO(
            direction="align",
            timing_score=72,
            timing_rationale="4h 高点突破回踩",
            key_levels={"support": 60000, "resistance": 62000},
            invalidation_for_timing="跌破 59500",
            updated_at=1234567890.0,
        )
        d = mv.to_dict()
        assert d["direction"] == "align"
        assert d["timing_score"] == 72
        assert d["key_levels"]["support"] == 60000
        # from_dict 往返
        mv2 = MidViewDTO.from_dict(d)
        assert mv2.direction == "align"
        assert mv2.timing_score == 72
        assert mv2.invalidation_for_timing == "跌破 59500"

    def test_from_dict_none_and_empty(self):
        """None / 空 dict / 非 dict → None（向后兼容）。"""
        from backend.services.mlto.types import MidViewDTO
        assert MidViewDTO.from_dict(None) is None
        assert MidViewDTO.from_dict({}) is None
        assert MidViewDTO.from_dict("not a dict") is None

    def test_from_dict_clamps_timing_score(self):
        from backend.services.mlto.types import MidViewDTO
        mv = MidViewDTO.from_dict({"direction": "counter", "timing_score": 250})
        assert mv.timing_score == 100  # clamp
        mv2 = MidViewDTO.from_dict({"timing_score": -10})
        assert mv2.timing_score == 0  # clamp


# ═══════════════════════════════════════════════════════════════════
# B. ThesisDTO.mid_view 向后兼容
# ═══════════════════════════════════════════════════════════════════
class TestThesisDTOMidView:
    def _make(self):
        from backend.services.mlto.types import ThesisDTO
        return ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="long",
        )

    def test_default_none_backward_compat(self):
        t = self._make()
        assert t.mid_view is None
        d = t.to_dict()
        assert d["mid_view"] is None  # 向后兼容：序列化为 None

    def test_with_mid_view(self):
        from backend.services.mlto.types import MidViewDTO
        t = self._make()
        t.mid_view = MidViewDTO(direction="align", timing_score=80)
        d = t.to_dict()
        assert d["mid_view"] is not None
        assert d["mid_view"]["direction"] == "align"
        assert d["mid_view"]["timing_score"] == 80


# ═══════════════════════════════════════════════════════════════════
# C. qual_layer._parse_result 解析 mid_view
# ═══════════════════════════════════════════════════════════════════
class TestQualLayerParseMidView:
    def test_parses_mid_view(self):
        from backend.services.mlto import qual_layer
        raw = {
            "direction": "long",
            "conviction_delta": 3,
            "thesis_summary": "...",
            "mid_view": {
                "direction": "align",
                "timing_score": 78,
                "timing_rationale": "1h RSI 回升",
                "key_levels": {"support": 60000, "resistance": 62000},
                "invalidation_for_timing": "跌破 59500",
            },
        }
        r = qual_layer._parse_result(raw)
        assert r.mid_view is not None
        assert r.mid_view["direction"] == "align"
        assert r.mid_view["timing_score"] == 78
        assert r.mid_view["key_levels"]["resistance"] == 62000

    def test_missing_mid_view_is_none(self):
        """LLM 不返回 mid_view → None，不报错。"""
        from backend.services.mlto import qual_layer
        r = qual_layer._parse_result({"direction": "long"})
        assert r.mid_view is None

    def test_non_dict_mid_view_is_none(self):
        """LLM 返回 mid_view="foo" → None。"""
        from backend.services.mlto import qual_layer
        r = qual_layer._parse_result({"direction": "long", "mid_view": "foo"})
        assert r.mid_view is None

    def test_empty_dict_mid_view_is_none(self):
        from backend.services.mlto import qual_layer
        r = qual_layer._parse_result({"direction": "long", "mid_view": {}})
        assert r.mid_view is None

    def test_clamps_timing_score(self):
        from backend.services.mlto import qual_layer
        r = qual_layer._parse_result({
            "direction": "long",
            "mid_view": {"timing_score": 999},
        })
        assert r.mid_view["timing_score"] == 100

    def test_non_dict_input_returns_default(self):
        from backend.services.mlto import qual_layer
        r = qual_layer._parse_result(None)  # type: ignore[arg-type]
        assert r.mid_view is None
        assert r.direction == "neutral"


# ═══════════════════════════════════════════════════════════════════
# D. qual_layer._build_prompt：长线含 mid_view 请求；中线不含
# ═══════════════════════════════════════════════════════════════════
class TestQualLayerPromptMidView:
    def _packet(self, tier):
        from backend.services.mlto.types import PerceptionPacket
        return PerceptionPacket(
            symbol="BTC", tier=tier, session_id="s1", ts=0.0, price=0.0,
            market_summary_sym={}, orchestrator={}, quant_brief={},
            analyst_reports={},
        )

    def _thesis(self):
        from backend.services.mlto.types import ThesisDTO
        return ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")

    def test_long_prompt_contains_mid_view_request(self, monkeypatch):
        # 强制走 fallback 路径（agent_prompt_service 可能未初始化）
        from backend.services.mlto import qual_layer
        monkeypatch.setattr(
            "backend.services.agent_prompt_service.render_agent_task",
            lambda *a, **k: (_ for _ in ()).throw(ImportError("test")),
            raising=False,
        )
        prompt = qual_layer._build_prompt(
            self._thesis(), "mem", "delta", "", self._packet("long"),
        )
        assert "mid_view" in prompt
        assert "timing_score" in prompt

    def test_mid_prompt_excludes_mid_view_request(self, monkeypatch):
        from backend.services.mlto import qual_layer
        monkeypatch.setattr(
            "backend.services.agent_prompt_service.render_agent_task",
            lambda *a, **k: (_ for _ in ()).throw(ImportError("test")),
            raising=False,
        )
        prompt = qual_layer._build_prompt(
            self._thesis(), "mem", "delta", "", self._packet("mid"),
        )
        # 中线 tier 本身就是中周期，prompt 不应再请求 mid_view
        assert "mid_view" not in prompt


# ═══════════════════════════════════════════════════════════════════
# E. thesis_store.apply_llm_update：设置 mid_view + 不覆盖历史
# ═══════════════════════════════════════════════════════════════════
class TestApplyLlmUpdateMidView:
    def _thesis(self):
        from backend.services.mlto.types import ThesisDTO
        return ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")

    def test_sets_mid_view_from_qual(self):
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import QualUpdateResult
        t = self._thesis()
        qual = QualUpdateResult(
            direction="long",
            mid_view={"direction": "align", "timing_score": 70},
        )
        thesis_store.apply_llm_update(t, qual, db=None)
        assert t.mid_view is not None
        assert t.mid_view.direction == "align"
        assert t.mid_view.timing_score == 70
        assert t.mid_view.updated_at > 0  # 由 apply_llm_update 设置

    def test_none_qual_mid_view_keeps_existing(self):
        """LLM 偶尔漏 mid_view 不应清空历史择时分析。"""
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import MidViewDTO, QualUpdateResult
        t = self._thesis()
        t.mid_view = MidViewDTO(direction="align", timing_score=80, updated_at=100.0)
        qual = QualUpdateResult(direction="long", mid_view=None)
        thesis_store.apply_llm_update(t, qual, db=None)
        # 历史保留
        assert t.mid_view is not None
        assert t.mid_view.timing_score == 80

    def test_backward_compat_no_mid_view_field(self):
        """从未接触 mid_view 的 thesis 仍能正常 apply_llm_update。"""
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import QualUpdateResult
        t = self._thesis()
        assert t.mid_view is None
        qual = QualUpdateResult(direction="long")
        thesis_store.apply_llm_update(t, qual, db=None)
        assert t.mid_view is None  # 仍为 None


# ═══════════════════════════════════════════════════════════════════
# F. thesis_store._persist / _row_to_dto 读写 mid_view_json
# ═══════════════════════════════════════════════════════════════════
class _FakeQuery:
    """最小化的 SQLAlchemy-like query mock，按 thesis_id 找行。"""
    def __init__(self, store):
        self._store = store

    def filter(self, *conds):
        # 忽略条件细节，假设按 thesis_id 过滤；取第一个条件的 right_operand
        tid = None
        for c in conds:
            rv = getattr(c, "right", None)
            rv = getattr(rv, "value", None) if rv is not None else None
            if rv is not None:
                tid = rv
                break
        self._tid = tid
        return self

    def first(self):
        if self._tid is None:
            return None
        return self._store.get(self._tid)


class _FakeDb:
    """用 dict 内存存储替代 DB；实现 add/commit/query/rollback。

    _persist 内部用 ``with AnalyticsSessionLocal() as _db``（独立短连接），
    测试须 patch backend.database.connection.AnalyticsSessionLocal 指向本类
    实例，否则 _persist 会连真实数据库。
    """
    def __init__(self):
        self.rows = {}  # thesis_id -> SimpleNamespace(row)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def add(self, row):
        self._current = row

    def commit(self):
        row = getattr(self, "_current", None)
        if row is not None and getattr(row, "thesis_id", None):
            self.rows[row.thesis_id] = row
            self._current = None

    def rollback(self):
        self._current = None

    def query(self, model):
        return _FakeQuery(self.rows)


class _FakeRow:
    """模拟 ORM 行实例。属性需在 *类* 上声明，这样 thesis_store 的
    ``hasattr(row.__class__, "mid_view_json")`` 守卫（与真实 SQLAlchemy Column
    描述符同构）才能为真。SimpleNamespace 把属性挂在实例上，通不过该守卫。"""
    thesis_id = None
    session_id = None
    symbol = None
    tier = None
    direction = None
    thesis_summary = None
    reasoning_snapshot = None
    llm_conviction = 0
    hub_composite = 0.0
    hub_adjusted = 0.0
    consistency = 0.0
    open_readiness = 0
    stable_since = None
    review_count = 0
    tranche_stage = 0
    regime_hash = ""
    invalidation_json = None
    missing_evidence_json = None
    owm_weights_json = None
    mid_view_json = None  # [阶段2] 关键：类级声明
    updated_at = None


def _make_row(thesis_id="t1"):
    """构造一个具备 mid_view_json 列的 row（模拟 ORM 实例）。"""
    r = _FakeRow()
    r.thesis_id = thesis_id
    r.session_id = "s1"
    r.symbol = "BTC"
    r.tier = "long"
    r.direction = "long"
    r.thesis_summary = "sum"
    r.reasoning_snapshot = ""
    r.llm_conviction = 50
    r.hub_composite = 0.5
    r.hub_adjusted = 0.5
    r.consistency = 0.5
    r.open_readiness = 0
    r.stable_since = None
    r.review_count = 1
    r.tranche_stage = 0
    r.regime_hash = ""
    r.invalidation_json = "{}"
    r.missing_evidence_json = "[]"
    r.owm_weights_json = "{}"
    r.mid_view_json = None
    r.updated_at = datetime.now(timezone.utc)
    return r


class TestThesisStorePersistMidView:
    def test_persist_writes_mid_view_json(self):
        from unittest.mock import patch
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import MidViewDTO, ThesisDTO
        db = _FakeDb()
        # 预置一行（模拟已存在 thesis）
        db.rows["t1"] = _make_row("t1")

        t = ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")
        t.mid_view = MidViewDTO(direction="align", timing_score=65)
        with patch("backend.database.connection.AnalyticsSessionLocal", lambda: db):
            thesis_store._persist(None, t)

        row = db.rows["t1"]
        assert row.mid_view_json is not None
        payload = row.mid_view_json
        assert payload["direction"] == "align"
        assert payload["timing_score"] == 65

    def test_persist_null_when_no_mid_view(self):
        from unittest.mock import patch
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import ThesisDTO
        db = _FakeDb()
        db.rows["t1"] = _make_row("t1")
        t = ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")
        t.mid_view = None
        with patch("backend.database.connection.AnalyticsSessionLocal", lambda: db):
            thesis_store._persist(None, t)
        assert db.rows["t1"].mid_view_json is None

    def test_row_to_dto_loads_mid_view(self):
        from backend.services.mlto import thesis_store
        row = _make_row("t1")
        row.mid_view_json = json.dumps({
            "direction": "counter", "timing_score": 40,
            "timing_rationale": "x", "key_levels": {"support": 1, "resistance": 2},
            "invalidation_for_timing": "y", "updated_at": 99.0,
        })
        dto = thesis_store._row_to_dto(row)
        assert dto.mid_view is not None
        assert dto.mid_view.direction == "counter"
        assert dto.mid_view.timing_score == 40
        assert dto.mid_view.updated_at == 99.0

    def test_row_to_dto_null_mid_view(self):
        """旧数据 mid_view_json 为 None → dto.mid_view=None。"""
        from backend.services.mlto import thesis_store
        row = _make_row("t1")
        row.mid_view_json = None
        dto = thesis_store._row_to_dto(row)
        assert dto.mid_view is None

    def test_roundtrip_persist_load(self):
        """persist → 用同一 row 重新 _row_to_dto：mid_view 往返不丢字段。"""
        from unittest.mock import patch
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import MidViewDTO, ThesisDTO
        db = _FakeDb()
        db.rows["t1"] = _make_row("t1")
        t = ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")
        t.mid_view = MidViewDTO(
            direction="align", timing_score=88,
            key_levels={"support": 60000, "resistance": 62000},
            invalidation_for_timing="跌破 59500",
        )
        with patch("backend.database.connection.AnalyticsSessionLocal", lambda: db):
            thesis_store._persist(None, t)
        dto2 = thesis_store._row_to_dto(db.rows["t1"])
        assert dto2.mid_view.direction == "align"
        assert dto2.mid_view.timing_score == 88
        assert dto2.mid_view.key_levels["support"] == 60000


# ═══════════════════════════════════════════════════════════════════
# G. quant_layer：mid_timing 信号
# ═══════════════════════════════════════════════════════════════════
class TestQuantLayerMidTiming:
    def _packet(self):
        from backend.services.mlto.types import PerceptionPacket
        return PerceptionPacket(
            symbol="BTC", tier="long", session_id="s1", ts=0.0, price=0.0,
            market_summary_sym={}, orchestrator={}, quant_brief={},
            analyst_reports={},
        )

    def _thesis(self, mid_view=None):
        from backend.services.mlto.types import ThesisDTO
        t = ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")
        t.mid_view = mid_view
        return t

    def test_produces_mid_timing_when_mid_view_exists(self):
        from backend.services.mlto import quant_layer
        from backend.services.mlto.types import MidViewDTO
        mv = MidViewDTO(timing_score=70)
        sigs = quant_layer.compute(self._packet(), self._thesis(mv), db=None)
        names = [s.name for s in sigs]
        assert "mid_timing" in names
        mt = next(s for s in sigs if s.name == "mid_timing")
        assert abs(mt.value - 0.70) < 1e-6
        assert mt.source == "framework"
        assert abs(mt.confidence - 0.8) < 1e-6

    def test_no_mid_timing_when_mid_view_none(self):
        """无 mid_view → 不产出 mid_timing（向后兼容：信号数量与现状一致）。"""
        from backend.services.mlto import quant_layer
        sigs_with = quant_layer.compute(
            self._packet(), self._thesis(mid_view=None), db=None,
        )
        names = [s.name for s in sigs_with]
        assert "mid_timing" not in names

    def test_no_mid_timing_when_timing_score_zero(self):
        """timing_score=0 视为未产出（边界）。"""
        from backend.services.mlto import quant_layer
        from backend.services.mlto.types import MidViewDTO
        mv = MidViewDTO(timing_score=0)
        sigs = quant_layer.compute(self._packet(), self._thesis(mv), db=None)
        assert "mid_timing" not in [s.name for s in sigs]


# ═══════════════════════════════════════════════════════════════════
# H. evidence_ingest：长线也 ingest mid_bias（+ mid 衰减）
# ═══════════════════════════════════════════════════════════════════
class TestEvidenceIngestMidBias:
    def _thesis(self, tier):
        from backend.services.mlto.types import ThesisDTO
        return ThesisDTO(
            thesis_id=f"t_{tier}", session_id="s1", symbol="BTC", tier=tier,
        )

    def _packet(self, tier, mid_bias=None, long_bias=None):
        from backend.services.mlto.types import PerceptionPacket
        orch = {}
        if mid_bias is not None:
            orch["mid_bias"] = mid_bias
            orch["mid_confidence"] = 0.7
        if long_bias is not None:
            orch["long_bias"] = long_bias
            orch["long_confidence"] = 0.8
        return PerceptionPacket(
            symbol="BTC", tier=tier, session_id="s1", ts=0.0, price=0.0,
            market_summary_sym={}, orchestrator=orch, quant_brief={},
            analyst_reports={},
        )

    def test_long_ingests_both_long_bias_and_mid_bias(self):
        from backend.services.mlto import evidence_ingest
        t = self._thesis("long")
        p = self._packet("long", mid_bias="bullish", long_bias="bullish")
        events = evidence_ingest.ingest_tick(p, t, db=None)
        signals = [e.signal for e in events]
        assert "long_bias" in signals
        assert "mid_bias" in signals  # [阶段2] 长线也 ingest mid_bias

    def test_long_mid_bias_uses_mid_decay_tier(self):
        """长线 thesis 的 mid_bias 事件应携带 decay_tier='mid'。"""
        from backend.services.mlto import evidence_ingest
        t = self._thesis("long")
        p = self._packet("long", mid_bias="bullish")
        events = evidence_ingest.ingest_tick(p, t, db=None)
        mid_ev = next(e for e in events if e.signal == "mid_bias")
        assert mid_ev.decay_tier == "mid"
        # 同一 long thesis 上的 long_bias 事件不带 decay_tier（走 thesis.tier）
        long_ev = next(
            (e for e in events if e.signal == "long_bias"), None,
        )
        if long_ev is not None:
            assert long_ev.decay_tier is None

    def test_mid_tier_unchanged(self):
        """中线 tier 行为不变（仍 ingest mid_bias，不带 decay_tier 覆盖）。"""
        from backend.services.mlto import evidence_ingest
        t = self._thesis("mid")
        p = self._packet("mid", mid_bias="bullish")
        events = evidence_ingest.ingest_tick(p, t, db=None)
        mid_ev = next(e for e in events if e.signal == "mid_bias")
        assert mid_ev.decay_tier is None  # 中线 thesis 走 thesis.tier=mid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
