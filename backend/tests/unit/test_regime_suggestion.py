# -*- coding: utf-8 -*-
"""
test_regime_suggestion — v6 阶段 2（S2-7）regime 参数建议通道单元测试

覆盖:
1. parse_regime_suggestion 宽容解析（缺字段/坏类型/非法枚举回退）
2. validate_regime_suggestion 规则校验（clamp / 冲突以规则为准 / None 默认）
3. apply_regime_params + consume_sl_multiplier 执行合并与消费
4. qual_layer._parse_result 链路（LLM raw → 校验后 applied）
5. thesis_store 透传（qual.regime_suggestion → thesis）
6. orchestrator._llm_stops 消费 sl_multiplier（物理界限内应用）

运行：.venv\\Scripts\\python.exe -m pytest backend\\tests\\unit\\test_regime_suggestion.py -q
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from backend.services.mlto.regime_suggestion import (
    RegimeSuggestion,
    apply_regime_params,
    consume_sl_multiplier,
    parse_regime_suggestion,
    validate_regime_suggestion,
)


# ═══════════════════════════════════════════════════════════════════
# 1. 宽容解析
# ═══════════════════════════════════════════════════════════════════

class TestParse:
    def test_full_block(self):
        s = parse_regime_suggestion({
            "regime": "trend", "sl_multiplier": 1.5, "tp_trigger": 3.0,
            "trailing": True, "addon_rhythm": "aggressive",
            "rationale": "趋势明确",
        })
        assert s is not None
        assert s.regime == "trend"
        assert s.sl_multiplier == 1.5
        assert s.tp_trigger == 3.0
        assert s.trailing is True
        assert s.addon_rhythm == "aggressive"

    def test_empty_returns_none(self):
        assert parse_regime_suggestion(None) is None
        assert parse_regime_suggestion({}) is None
        assert parse_regime_suggestion("garbage") is None

    def test_bad_values_fall_back(self):
        s = parse_regime_suggestion({
            "regime": "weird", "sl_multiplier": "abc",
            "tp_trigger": None, "trailing": "yes",
            "addon_rhythm": "crazy",
        })
        assert s.regime == "unknown"
        assert s.sl_multiplier == 1.0
        assert s.tp_trigger == 2.0
        assert s.trailing is True
        assert s.addon_rhythm == "none"


# ═══════════════════════════════════════════════════════════════════
# 2. 规则校验
# ═══════════════════════════════════════════════════════════════════

class TestValidate:
    def test_none_gives_rule_default(self):
        v = validate_regime_suggestion(None, {})
        assert v["applied"]["source"] == "rule_default"
        assert v["applied"]["regime"] in ("trend", "ranging", "extreme", "unknown")
        assert v["applied"]["sl_multiplier"] == 1.0
        assert not v["conflicts"]

    def test_clamp_out_of_range(self):
        s = RegimeSuggestion(regime="trend", sl_multiplier=9.9, tp_trigger=0.1)
        v = validate_regime_suggestion(s, {})
        assert v["applied"]["sl_multiplier"] == 3.0
        assert v["applied"]["tp_trigger"] == 1.0
        assert v["adjusted"]["sl_multiplier"] == 9.9
        assert len(v["rejected"]) == 2

    def test_regime_conflict_rule_wins(self):
        """LLM 判 trend，规则判 extreme（24h±>12%）→ 以规则为准 + conflict。"""
        s = RegimeSuggestion(regime="trend", sl_multiplier=2.0)
        ms = {"price_change_24h_pct": 15.0, "price_change_1h_pct": 6.0}
        v = validate_regime_suggestion(s, ms)
        assert v["applied"]["regime"] == "extreme"
        assert v["applied"]["source"] == "llm_validated"
        assert any("以规则为准" in c for c in v["conflicts"])

    def test_regime_agreement_no_conflict(self):
        s = RegimeSuggestion(regime="trend", sl_multiplier=1.0)
        ms = {"price_change_24h_pct": 5.0, "price_change_1h_pct": 1.0}
        v = validate_regime_suggestion(s, ms)
        assert v["applied"]["regime"] == "trend"
        assert not v["conflicts"]


# ═══════════════════════════════════════════════════════════════════
# 3. 执行合并与消费
# ═══════════════════════════════════════════════════════════════════

class TestApplyConsume:
    def test_apply_merges_into_ms(self):
        ms = {"price": 100.0}
        v = validate_regime_suggestion(
            RegimeSuggestion(regime="trend", sl_multiplier=1.5), ms)
        out = apply_regime_params(ms, v)
        assert out["regime_suggestion"]["sl_multiplier"] == 1.5
        assert out["regime_suggestion"]["source"] == "llm_validated"
        assert ms.get("regime_suggestion") is None  # 原 dict 不被修改

    def test_apply_no_validated_keeps_ms(self):
        out = apply_regime_params({"a": 1}, None)
        assert out == {"a": 1}

    def test_consume_multiplier(self):
        ms = {"regime_suggestion": {"sl_multiplier": 2.0}}
        assert consume_sl_multiplier(ms, 0.05) == pytest.approx(0.10)

    def test_consume_clamps_physical_bounds(self):
        ms = {"regime_suggestion": {"sl_multiplier": 3.0}}
        assert consume_sl_multiplier(ms, 0.15, max_sl=0.20) == pytest.approx(0.20)
        assert consume_sl_multiplier(ms, 0.002, min_sl=0.01) == pytest.approx(0.01)

    def test_consume_no_suggestion_unchanged(self):
        assert consume_sl_multiplier({}, 0.05) == pytest.approx(0.05)
        assert consume_sl_multiplier({"regime_suggestion": "junk"}, 0.05) == pytest.approx(0.05)


# ═══════════════════════════════════════════════════════════════════
# 4. qual_layer 解析链路
# ═══════════════════════════════════════════════════════════════════

class TestQualLayerChain:
    def test_parse_result_extracts_suggestion(self):
        from backend.services.mlto import qual_layer
        raw = {
            "direction": "long",
            "regime_suggestion": {
                "regime": "trend", "sl_multiplier": 1.5,
                "tp_trigger": 3.0, "trailing": True,
                "addon_rhythm": "conservative", "rationale": "r",
            },
        }
        ms = {"price_change_24h_pct": 5.0, "price_change_1h_pct": 1.0}
        r = qual_layer._parse_result(raw, ms)
        assert r.regime_suggestion is not None
        assert r.regime_suggestion["regime"] == "trend"
        assert r.regime_suggestion["sl_multiplier"] == 1.5
        assert r.regime_suggestion["source"] == "llm_validated"

    def test_parse_result_conflict_resolved_by_rules(self):
        from backend.services.mlto import qual_layer
        raw = {"regime_suggestion": {"regime": "trend", "sl_multiplier": 2.0}}
        ms = {"price_change_24h_pct": 15.0, "price_change_1h_pct": 6.0}
        r = qual_layer._parse_result(raw, ms)
        assert r.regime_suggestion["regime"] == "extreme"  # 规则为准

    def test_parse_result_missing_is_none(self):
        from backend.services.mlto import qual_layer
        assert qual_layer._parse_result({"direction": "long"}).regime_suggestion is None
        assert qual_layer._parse_result(None).regime_suggestion is None

    def test_parse_result_bad_block_is_none(self):
        from backend.services.mlto import qual_layer
        r = qual_layer._parse_result({"regime_suggestion": {"sl_multiplier": "x"}}, {})
        # regime 非法回退 unknown，但块仍有效（source=llm_validated）
        assert r.regime_suggestion is not None
        assert r.regime_suggestion["regime"] in ("trend", "ranging", "extreme", "unknown")


# ═══════════════════════════════════════════════════════════════════
# 5. thesis_store 透传
# ═══════════════════════════════════════════════════════════════════

class _FakeDb:
    """最小化 SQLAlchemy-like session mock。

    _persist 内部用 ``with AnalyticsSessionLocal() as _db``（独立短连接），
    测试须 patch backend.database.connection.AnalyticsSessionLocal 指向本类
    实例，否则 _persist 会连真实数据库。
    """
    def __init__(self):
        self.rows = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query(self, model):
        return _FakeQuery(self.rows)

    def add(self, obj):
        self._current = obj

    def commit(self):
        row = getattr(self, "_current", None)
        if row is not None and getattr(row, "thesis_id", None):
            self.rows[row.thesis_id] = row
            self._current = None

    def rollback(self):
        self._current = None


class _FakeQuery:
    """按 thesis_id 找行（与 test_midview_thesis 同构）。"""
    def __init__(self, rows):
        self._rows = rows
        self._tid = None

    def filter(self, *conds):
        for c in conds:
            rv = getattr(c, "right", None)
            rv = getattr(rv, "value", None) if rv is not None else None
            if rv is not None:
                self._tid = rv
                break
        return self

    def first(self):
        if self._tid is None:
            return None
        return self._rows.get(self._tid)



class _FakeRowLite:
    """模拟 ORM 行：属性须在 *类* 上声明（hasattr(row.__class__, ...) 守卫为真）。"""
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
    mid_view_json = None
    regime_suggestion_json = None  # [v6 S2-7] 类级声明
    updated_at = None

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _make_rs_row(thesis_id="t1"):
    return _FakeRowLite(
        thesis_id=thesis_id, session_id="s1", symbol="BTC", tier="long",
        direction="long", thesis_summary="sum", llm_conviction=50,
        hub_composite=0.5, hub_adjusted=0.5, consistency=0.5,
        invalidation_json="{}", missing_evidence_json="[]", owm_weights_json="{}",
    )


class TestThesisStore:
    def _thesis(self):
        from backend.services.mlto.types import ThesisDTO
        return ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="mid")

    def test_qual_rs_updates_thesis(self):
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import QualUpdateResult
        t = self._thesis()
        q = QualUpdateResult(regime_suggestion={"regime": "trend", "sl_multiplier": 1.5})
        thesis_store.apply_llm_update(t, q)
        assert t.regime_suggestion == {"regime": "trend", "sl_multiplier": 1.5}

    def test_qual_missing_keeps_history(self):
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import QualUpdateResult
        t = self._thesis()
        t.regime_suggestion = {"regime": "ranging", "sl_multiplier": 0.8}
        thesis_store.apply_llm_update(t, QualUpdateResult())
        assert t.regime_suggestion == {"regime": "ranging", "sl_multiplier": 0.8}


# ═══════════════════════════════════════════════════════════════════
# 5b. thesis_store._persist / _row_to_dto 读写 regime_suggestion_json
# ═══════════════════════════════════════════════════════════════════

class TestThesisStorePersistRegimeSuggestion:
    def test_persist_writes_regime_suggestion_json(self):
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import ThesisDTO
        db = _FakeDb()
        db.rows["t1"] = _make_rs_row("t1")

        t = ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")
        t.regime_suggestion = {
            "regime": "trend", "sl_multiplier": 1.5, "tp_trigger": 3.0,
            "trailing": True, "addon_rhythm": "aggressive", "source": "llm_validated",
        }
        with patch("backend.database.connection.AnalyticsSessionLocal", lambda: db):
            thesis_store._persist(None, t)

        row = db.rows["t1"]
        assert row.regime_suggestion_json is not None
        payload = row.regime_suggestion_json
        assert payload["regime"] == "trend"
        assert payload["sl_multiplier"] == 1.5
        assert payload["source"] == "llm_validated"

    def test_persist_null_when_no_suggestion(self):
        from backend.services.mlto import thesis_store
        from backend.services.mlto.types import ThesisDTO
        db = _FakeDb()
        db.rows["t1"] = _make_rs_row("t1")
        t = ThesisDTO(thesis_id="t1", session_id="s1", symbol="BTC", tier="long")
        t.regime_suggestion = None
        with patch("backend.database.connection.AnalyticsSessionLocal", lambda: db):
            thesis_store._persist(None, t)
        assert db.rows["t1"].regime_suggestion_json is None

    def test_row_to_dto_loads_regime_suggestion(self):
        from backend.services.mlto import thesis_store
        row = _make_rs_row("t1")
        row.regime_suggestion_json = json.dumps({
            "regime": "extreme", "sl_multiplier": 0.8,
            "tp_trigger": 2.0, "trailing": False, "source": "llm_validated",
        })
        dto = thesis_store._row_to_dto(row)
        assert dto.regime_suggestion is not None
        assert dto.regime_suggestion["regime"] == "extreme"
        assert dto.regime_suggestion["sl_multiplier"] == 0.8

    def test_row_to_dto_missing_keeps_none(self):
        from backend.services.mlto import thesis_store
        dto = thesis_store._row_to_dto(_make_rs_row("t1"))
        assert dto.regime_suggestion is None

    def test_row_to_dto_bad_json_keeps_none(self):
        from backend.services.mlto import thesis_store
        row = _make_rs_row("t1")
        row.regime_suggestion_json = "not-json"
        dto = thesis_store._row_to_dto(row)
        assert dto.regime_suggestion is None

    def test_row_to_dto_old_schema_without_column(self):
        """旧库无 regime_suggestion_json 列：getattr 容错，不抛异常。"""
        from backend.services.mlto import thesis_store
        row = _make_rs_row("t1")
        row.regime_suggestion_json = None  # 先实例化，模拟"列存在但为 NULL"
        del row.__dict__["regime_suggestion_json"]  # 再删 → 模拟旧库无此列
        dto = thesis_store._row_to_dto(row)
        assert dto.regime_suggestion is None



# ═══════════════════════════════════════════════════════════════════
# 6. orchestrator._llm_stops 消费
# ═══════════════════════════════════════════════════════════════════

class TestOrchestratorConsume:
    def _packet(self, atr_1d_pct=0.02, price=100.0):
        from backend.services.mlto.types import PerceptionPacket
        return PerceptionPacket(
            symbol="BTC", tier="mid", session_id="s1", ts=0.0, price=price,
            market_summary_sym={"atr_1d_pct": atr_1d_pct, "price": price},
            orchestrator={}, quant_brief={}, analyst_reports={},
        )

    def _thesis(self, sl=0.04, tp=0.12, rs=None):
        from backend.services.mlto.types import ThesisDTO
        return ThesisDTO(
            thesis_id="t1", session_id="s1", symbol="BTC", tier="mid",
            sl_pct=sl, tp_pct=tp, regime_suggestion=rs,
        )

    def test_sl_multiplier_applied(self):
        from backend.services.mlto.orchestrator import _llm_stops
        rs = {"regime": "trend", "sl_multiplier": 2.0, "source": "llm_validated"}
        sl, tp = _llm_stops(self._thesis(rs=rs), self._packet(), "buy")
        # ATR floor: 0.02×1.5=0.03 < 0.04 → sl=0.04 → ×2.0 → 0.08；TP=max(0.12, 0.16)=0.16
        assert sl == pytest.approx(0.08)
        assert tp == pytest.approx(0.16)

    def test_no_suggestion_unchanged(self):
        from backend.services.mlto.orchestrator import _llm_stops
        sl, tp = _llm_stops(self._thesis(), self._packet(), "buy")
        assert sl == pytest.approx(0.04)
        assert tp == pytest.approx(0.12)

    def test_multiplier_clamped_by_physical_bounds(self):
        from backend.services.mlto.orchestrator import _llm_stops
        rs = {"regime": "trend", "sl_multiplier": 3.0, "source": "llm_validated"}
        sl, _ = _llm_stops(self._thesis(sl=0.10, rs=rs), self._packet(), "buy")
        assert sl <= 0.20  # 物理上限
