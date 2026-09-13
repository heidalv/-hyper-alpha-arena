# -*- coding: utf-8 -*-
"""中长线 LLM 主脑改造回归（2026-09-05）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from backend.services.analysis import schemas
from backend.services.full_auto.scalp_open_gate import scalp_new_open_blocked
from backend.services.mlto.brain import (
    can_open,
    consensus_is_tradeable,
    current_bar_open_ms,
    missing_blocks_open,
    midlong_new_open_halted,
    normalize_invalidation,
    order_refresh_symbols,
    strip_soft_missing,
    thesis_expiry_s,
    thesis_fail_backoff_s,
    thesis_is_fresh,
    thesis_is_tradeable_fresh,
    thesis_needs_early_refresh,
    thesis_ttl_s,
    thesis_watch_reason,
    _force_hold_if_missing,
    _inv_price,
    _map_direction,
    _named_lock,
    _norm_base_symbol,
    _position_from_pack,
    _REFRESH_LOCKS,
    _REFRESH_LOCKS_GUARD,
)
from backend.services.mlto.types import ThesisDTO


def test_midlong_thesis_schema_registered():
    ok, errs = schemas.validate("midlong_thesis", {
        "direction": "bullish",
        "strength": 6,
        "confidence": 0.72,
        "recommend_open": True,
        "should_close": False,
        "invalidation": {"price": 2400.0, "condition": "跌破 4h 结构低点"},
        "entry_zone": {"low": 2450.0, "high": 2480.0},
        "missing_evidence": [],
        "sl_pct": 0.05,
        "tp_pct": 0.10,
        "thesis_summary": "4h/1d 共振多",
        "key_factors": ["结构", "资金费中性"],
        "summary": "建议小仓试多",
    })
    assert ok, errs
    ok2, errs2 = schemas.validate("midlong_thesis", {
        "direction": "bullish",
        "strength": 6,
        "confidence": 0.72,
        "recommend_open": True,
        "should_close": False,
        "invalidation": "跌破 4h 结构低点",
        "entry_zone": {"low": 2450.0, "high": 2480.0},
        "missing_evidence": [],
        "sl_pct": 0.05,
        "tp_pct": 0.10,
        "thesis_summary": "4h/1d 共振多",
        "key_factors": ["结构"],
        "summary": "建议小仓试多",
    })
    assert not ok2 and any("invalidation" in e for e in errs2)
    # entry_zone 非硬必填：缺了仍可通过；给了非法值时若出现在 payload 可由调用方自检
    ok3, errs3 = schemas.validate("midlong_thesis", {
        "direction": "bullish",
        "strength": 6,
        "confidence": 0.72,
        "recommend_open": True,
        "should_close": False,
        "invalidation": {"price": 2400.0, "condition": "跌破"},
        "missing_evidence": [],
        "sl_pct": 0.05,
        "tp_pct": 0.10,
        "thesis_summary": "x",
        "key_factors": ["a"],
        "summary": "y",
    })
    assert ok3, errs3
    from backend.services.analysis.schemas import _check
    assert _check("entry_zone", {"low": 0, "high": 10}) is not None
    assert _check("entry_zone", {"low": 1, "high": 2}) is None


def test_missing_evidence_forces_hold():
    out = _force_hold_if_missing({
        "recommend_open": True,
        "confidence": 0.99,
        "missing_evidence": ["现价", "K线:1h"],
    })
    assert out["recommend_open"] is False
    assert float(out["confidence"]) < 0.6
    assert missing_blocks_open(["K线:1h"])
    assert missing_blocks_open(["现价"])
    assert not missing_blocks_open(["缺K线"])
    assert not missing_blocks_open(["缺少V2的L1明细及对应K线"])
    assert not missing_blocks_open(["仅现价 2453 可引用"])
    assert not missing_blocks_open(["资金费"])


def test_map_direction():
    assert _map_direction("bullish") == "long"
    assert _map_direction("bearish") == "short"
    assert _map_direction("neutral") == "neutral"


def test_position_from_pack_reads_sym_not_symbol():
    pack = SimpleNamespace(layers={
        "positions": {"open": [
            {"acct": 14, "sym": "LINKUSDT", "side": "long", "tier": "long"},
            {"acct": 99, "sym": "UNI", "side": "short", "tier": "mid"},
        ]},
    })
    assert _norm_base_symbol("UNIUSDT") == "UNI"
    hit = _position_from_pack(pack, "LINK", account_id=14, tier="long")
    assert hit and hit["side"] == "long"
    uni = _position_from_pack(pack, "UNI", account_id=14, tier="mid")
    assert uni is None
    uni_other = _position_from_pack(pack, "UNI", account_id=99, tier="mid")
    assert uni_other and uni_other["side"] == "short"


def test_neutral_consensus_is_not_tradeable():
    neu = SimpleNamespace(accepted=True)
    inv = {"price": 100.0, "condition": "破位"}
    assert not consensus_is_tradeable(neu, {"direction": "neutral", "recommend_open": False}, False)
    assert consensus_is_tradeable(
        neu, {"direction": "bearish", "recommend_open": False, "invalidation": inv}, False,
    )
    assert not consensus_is_tradeable(SimpleNamespace(accepted=False), {"direction": "bullish"}, False)


def test_naive_expires_at_is_china_wall_clock():
    cst = timezone(timedelta(hours=8))
    now_utc = datetime.now(timezone.utc)
    past_naive = (now_utc.astimezone(cst) - timedelta(hours=1)).replace(tzinfo=None)
    future_naive = (now_utc.astimezone(cst) + timedelta(hours=1)).replace(tzinfo=None)
    past = ThesisDTO(
        thesis_id="tp", session_id="s", symbol="UNI", tier="mid",
        direction="short", expires_at=past_naive, analysis_run_id="run-past",
    )
    fut = ThesisDTO(
        thesis_id="tf", session_id="s", symbol="UNI", tier="mid",
        direction="short", expires_at=future_naive, analysis_run_id="run-fut",
    )
    assert not thesis_is_fresh(past)
    assert thesis_is_fresh(fut)


def test_can_open_requires_fresh_accepted_thesis(monkeypatch, tmp_path):
    _quiet_watch_side_channels(monkeypatch)
    _quiet_circuit_state(monkeypatch, tmp_path)
    # [2026-09-09 第十六轮] 多头治理已独立（MIDLONG_LONG_MODE 默认 learned）；
    # 本用例契约是「thesis 新鲜度/可交易性」，与 learned 准入无关 → 钉 allow_all。
    monkeypatch.setenv("MIDLONG_LONG_MODE", "allow_all")
    now = datetime.now(timezone.utc)
    inv = {"price": 1_000.0, "condition": "跌破"}  # 远低于现价，避免 invalidation_hit
    fresh = ThesisDTO(
        thesis_id="t1", session_id="s", symbol="BTC", tier="mid",
        direction="long", recommend_open=True, accepted=True,
        updated_at=now, expires_at=now + timedelta(hours=2), analysis_run_id="run-fresh",
        invalidation=inv,
    )
    assert can_open(fresh)
    stale = ThesisDTO(
        thesis_id="t2", session_id="s", symbol="BTC", tier="mid",
        direction="long", recommend_open=True, accepted=True,
        expires_at=now - timedelta(minutes=1), invalidation=inv,
    )
    assert not thesis_is_fresh(stale)
    assert not can_open(stale)
    no_dir = ThesisDTO(
        thesis_id="t3", session_id="s", symbol="BTC", tier="mid",
        direction="neutral", recommend_open=True, accepted=True,
        expires_at=now + timedelta(hours=2), analysis_run_id="run-nd",
        invalidation=inv,
    )
    assert not can_open(no_dir)
    missing = ThesisDTO(
        thesis_id="t4", session_id="s", symbol="BTC", tier="mid",
        direction="long", recommend_open=True, accepted=True,
        missing_evidence=["现价"],
        expires_at=now + timedelta(hours=2), analysis_run_id="run-miss",
        invalidation=inv,
    )
    assert not can_open(missing)
    no_inv = ThesisDTO(
        thesis_id="t5", session_id="s", symbol="BTC", tier="mid",
        direction="long", recommend_open=True, accepted=True,
        updated_at=now, expires_at=now + timedelta(hours=2), analysis_run_id="run-ni",
        invalidation={"condition": "叙事失效"},
    )
    assert not can_open(no_inv)


def test_scalp_new_open_blocked(monkeypatch):
    monkeypatch.setenv("SCALP_OPEN_DISABLED", "true")
    monkeypatch.setattr(
        "backend.config.settings.SCALP_OPEN_DISABLED", True, raising=False,
    )
    blocked, reason = scalp_new_open_blocked("open", "scalp", "short")
    assert blocked and reason == "scalp_open_disabled"
    blocked, _ = scalp_new_open_blocked("close", "scalp", "short")
    assert not blocked
    blocked, _ = scalp_new_open_blocked("open", "swing", "mid")
    assert not blocked
    blocked, _ = scalp_new_open_blocked("open", "trend_follow", "long", reduce_only=True)
    assert not blocked


def test_chart_gate_required_rejects_empty(monkeypatch):
    monkeypatch.setenv("MIDLONG_CHART_REQUIRED", "true")
    monkeypatch.setenv("MIDLONG_CHART_GATE_ENABLED", "true")
    from backend.services.full_auto import midlong_chart_gate as g
    monkeypatch.setattr(g, "_chart_required", lambda: True)
    monkeypatch.setattr(g, "_enabled", lambda: True)
    monkeypatch.setattr(g, "_latest_chart_signal", lambda _s: None)
    ok, reason, _ = g.chart_gate_check("BTC", "buy", tier="mid")
    assert ok is False
    assert "required" in reason


def test_chart_gate_missing_is_fail_open(monkeypatch):
    monkeypatch.setenv("MIDLONG_CHART_REQUIRED", "false")
    monkeypatch.setenv("MIDLONG_CHART_GATE_ENABLED", "true")
    from backend.services.full_auto import midlong_chart_gate as g
    monkeypatch.setattr(g, "_chart_required", lambda: False)
    monkeypatch.setattr(g, "_enabled", lambda: True)
    monkeypatch.setattr(g, "_latest_chart_signal", lambda _s: None)
    ok, reason, _ = g.chart_gate_check("ASTER", "buy", tier="mid")
    assert ok is True
    assert "fail-open" in reason


def test_chart_gate_vetoes_no_new_long_when_chart_exists(monkeypatch):
    monkeypatch.setenv("MIDLONG_CHART_GATE_ENABLED", "true")
    from backend.services.full_auto import midlong_chart_gate as g
    monkeypatch.setattr(g, "_chart_required", lambda: False)
    monkeypatch.setattr(g, "_enabled", lambda: True)
    # [2026-09-09 存量修复] 原用例写死 created_ms=1_700_000_000_000（2023-11-14），
    # 而 chart_gate 在 2026-09-08 加入了「信号年龄 > MIDLONG_CHART_MAX_SIGNAL_AGE_MIN
    # （默认 240min）即 fail-open 不否决」的规则 → 恒放行、断言必红。
    # 改为相对当前时间的「新鲜」信号，恢复用例本意（图审禁令否决开仓）。
    # [2026-09-11] 方向一致性默认开启：no_new_long 需信号方向看空(-1)才否决开多。
    import time as _t
    _now_ms = int(_t.time() * 1000)
    monkeypatch.setattr(g, "_latest_chart_signal", lambda _s: {
        "direction": -1,
        "strength": 5,
        "created_ms": _now_ms - 60_000,
        "payload": {"position_advice": "no_new_long"},
    })
    ok, reason, _ = g.chart_gate_check("UNI", "buy", tier="mid")
    assert ok is False
    assert "no_new_long" in reason


def test_factor_route_open_is_evidence_only_when_brain(monkeypatch):
    """[M5 2026-09-14] 语义更新：脑开启时默认 A/B（paper 并行开仓）；
    MIDLONG_MID_FACTOR_ROUTE_AB=false 才是旧行为（evidence_only 回滚档）。"""
    monkeypatch.setattr(
        "backend.config.settings.midlong_brain_enabled", lambda: True,
    )
    from backend.services.factor_engine import midlong_factor_route as fr
    monkeypatch.setattr(fr, "factor_route_decide", lambda *a, **k: {
        "action": "buy", "score": 0.9, "reason": "fake",
    })
    # 回滚档：AB=false → evidence_only（旧语义锁定）
    monkeypatch.setenv("MIDLONG_MID_FACTOR_ROUTE_AB", "false")
    dec = fr.factor_route_open(
        host=SimpleNamespace(), session=SimpleNamespace(), symbol="BTC",
    )
    assert dec["opened"] is False
    assert dec["gate"] == "brain_evidence_only"


def test_long_v2_disabled_when_brain(monkeypatch):
    monkeypatch.setenv("LONG_TREND_V2", "1")
    monkeypatch.setattr(
        "backend.config.settings.midlong_brain_enabled", lambda: True,
    )
    from backend.services.long_trend_v2 import long_v2_enabled
    assert long_v2_enabled() is False


def test_authority_brain_only_allows_mlto(monkeypatch):
    monkeypatch.setattr(
        "backend.config.settings.midlong_brain_enabled", lambda: True,
    )
    from backend.services.full_auto.midlong_executor import authority_allows_open
    monkeypatch.setattr(
        "backend.services.full_auto.midlong_executor.get_midlong_exec_authority",
        lambda *a, **k: "mlto",
    )
    assert authority_allows_open("mlto", "mlto")
    assert not authority_allows_open("mlto", "factor_route")


def test_thesis_row_to_dto_brain_cols():
    from backend.services.mlto.thesis_store import _row_to_dto
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        thesis_id="tid", session_id="sid", symbol="ETH", tier="mid",
        direction="long", thesis_summary="x", reasoning_snapshot="",
        llm_conviction=70, hub_composite=0, hub_adjusted=0, consistency=0,
        open_readiness=0, stable_since=now, review_count=1, tranche_stage=0,
        regime_hash="", invalidation_json="{}", missing_evidence_json="[]",
        owm_weights_json="{}", mid_view_json=None, sl_pct=0.05, tp_pct=0.1,
        regime_suggestion_json=None, wisdom_ids_json=None, updated_at=now,
        recommend_open=1, should_close=0, accepted=1, expires_at=now,
        analysis_run_id="run-1",
    )
    dto = _row_to_dto(row)
    assert dto.recommend_open is True
    assert dto.should_close is False
    assert dto.accepted is True
    assert dto.analysis_run_id == "run-1"


def test_can_open_halted_when_no_thesis_flag_false(monkeypatch):
    monkeypatch.setattr(
        "backend.config.settings.MIDLONG_NO_THESIS_NO_OPEN", False, raising=False,
    )
    monkeypatch.setattr(
        "backend.config.settings.midlong_brain_enabled", lambda: True,
    )
    monkeypatch.setattr(
        "backend.config.settings.midlong_new_open_halted", lambda: True,
    )
    now = datetime.now(timezone.utc)
    fresh = ThesisDTO(
        thesis_id="h1", session_id="s", symbol="BTC", tier="mid",
        direction="long", recommend_open=True, accepted=True,
        expires_at=now + timedelta(hours=2), analysis_run_id="run-h",
    )
    assert midlong_new_open_halted() is True
    assert not can_open(fresh)


def _quiet_watch_side_channels(monkeypatch):
    monkeypatch.setattr(
        "backend.services.full_auto.midlong_chart_gate._latest_chart_signal",
        lambda _s: None,
    )
    monkeypatch.setattr(
        "backend.services.analysis.ledgers.list_signals",
        lambda **_k: [],
    )


def _quiet_circuit_state(monkeypatch, tmp_path):
    """隔离熔断状态文件：can_open → check_midlong_entry 会加载真实生产状态文件
    （data/midlong_circuit_state.json），全量跑时与正在运行的实盘后端共享该文件，
    状态内容随时间变化（实测因读到实盘 ban 而误拦）。改为注入空状态。"""
    from backend.services.full_auto import midlong_circuit_gate as _cg

    monkeypatch.setattr(_cg, "_STATE_FILE", str(tmp_path / "midlong_circuit_state.json"))
    monkeypatch.setattr(_cg, "_state", {})
    monkeypatch.setattr(_cg, "_loaded", True)


def _fresh_mid(*, updated, expires=None, **kw):
    now = updated
    payload = dict(
        thesis_id="w1", session_id="s", symbol="ETH", tier="mid",
        direction="long", recommend_open=True, accepted=True,
        updated_at=updated,
        expires_at=expires or (now + timedelta(hours=3)),
        analysis_run_id="run-w",
        invalidation={"price": 90, "condition": "跌破"},
    )
    payload.update(kw)
    return ThesisDTO(**payload)


def test_watch_bar_close_refreshes_inside_ttl(monkeypatch):
    _quiet_watch_side_channels(monkeypatch)
    now = datetime(2026, 9, 5, 16, 5, tzinfo=timezone.utc)
    dto = _fresh_mid(updated=datetime(2026, 9, 5, 15, 50, tzinfo=timezone.utc))
    reason = thesis_watch_reason(dto, "ETH", "mid", now=now)
    assert reason == "bar_close:4h"
    assert current_bar_open_ms("4h", int(now.timestamp() * 1000)) == int(
        datetime(2026, 9, 5, 16, 0, tzinfo=timezone.utc).timestamp() * 1000
    )


def test_watch_same_bar_without_shock_holds(monkeypatch, tmp_path):
    _quiet_watch_side_channels(monkeypatch)
    _quiet_circuit_state(monkeypatch, tmp_path)
    # [2026-09-09 第十六轮] 多头 learned 门与 watch 契约无关 → 钉 allow_all
    # （否则真实市场数据下 up-regime chg24 不满足会误拦）。
    monkeypatch.setenv("MIDLONG_LONG_MODE", "allow_all")
    monkeypatch.setattr(
        "backend.services.mlto.brain._spot_price", lambda *_a, **_k: 100.0,
    )
    now = datetime(2026, 9, 5, 16, 20, tzinfo=timezone.utc)
    monkeypatch.setattr("backend.services.mlto.brain._utcnow", lambda: now)
    dto = _fresh_mid(
        updated=datetime(2026, 9, 5, 16, 5, tzinfo=timezone.utc),
        invalidation={
            "price": 90,
            "condition": "跌破",
            "_watch": {"price": 100.0, "bar_tf": "4h", "bar_open_ms": current_bar_open_ms(
                "4h", int(now.timestamp() * 1000),
            )},
        },
    )
    assert thesis_watch_reason(dto, "ETH", "mid", now=now) is None
    assert can_open(dto, {"ETH": {"last": 100.0}}) is True


def test_watch_price_shock_after_cooldown(monkeypatch):
    _quiet_watch_side_channels(monkeypatch)
    monkeypatch.setenv("MIDLONG_WATCH_MIN_REFRESH_S", "1800")
    monkeypatch.setattr(
        "backend.services.mlto.brain._spot_price", lambda *_a, **_k: 98.7,
    )
    now = datetime(2026, 9, 5, 16, 40, tzinfo=timezone.utc)
    dto = _fresh_mid(
        updated=datetime(2026, 9, 5, 16, 5, tzinfo=timezone.utc),
        invalidation={
            "price": 90,
            "_watch": {"price": 100.0, "bar_tf": "4h", "bar_open_ms": current_bar_open_ms(
                "4h", int(now.timestamp() * 1000),
            )},
        },
    )
    reason = thesis_watch_reason(dto, "ETH", "mid", now=now)
    assert reason and reason.startswith("price_shock")


def test_watch_shock_respects_cooldown(monkeypatch):
    _quiet_watch_side_channels(monkeypatch)
    monkeypatch.setenv("MIDLONG_WATCH_MIN_REFRESH_S", "1800")
    monkeypatch.setattr(
        "backend.services.mlto.brain._spot_price", lambda *_a, **_k: 102.0,
    )
    now = datetime(2026, 9, 5, 16, 8, tzinfo=timezone.utc)
    dto = _fresh_mid(
        updated=datetime(2026, 9, 5, 16, 5, tzinfo=timezone.utc),
        invalidation={
            "price": 90,
            "_watch": {"price": 100.0, "bar_tf": "4h", "bar_open_ms": current_bar_open_ms(
                "4h", int(now.timestamp() * 1000),
            )},
        },
    )
    assert thesis_watch_reason(dto, "ETH", "mid", now=now) is None


def test_watch_invalidation_near_bypasses_cooldown(monkeypatch):
    _quiet_watch_side_channels(monkeypatch)
    monkeypatch.setattr(
        "backend.services.mlto.brain._spot_price", lambda *_a, **_k: 100.2,
    )
    now = datetime(2026, 9, 5, 16, 8, tzinfo=timezone.utc)
    dto = _fresh_mid(
        updated=datetime(2026, 9, 5, 16, 5, tzinfo=timezone.utc),
        invalidation={"price": 100.0, "condition": "跌破"},
    )
    assert thesis_watch_reason(dto, "ETH", "mid", now=now) == "invalidation_near"


def test_can_open_blocks_chase(monkeypatch, tmp_path):
    _quiet_watch_side_channels(monkeypatch)
    _quiet_circuit_state(monkeypatch, tmp_path)
    # [2026-09-09 第十六轮] 本用例断言的是 watch 追价拦截（False 的来源），
    # 钉 allow_all 避免 learned 门在同一断言上混淆原因。
    monkeypatch.setenv("MIDLONG_LONG_MODE", "allow_all")
    monkeypatch.setattr(
        "backend.services.mlto.brain._spot_price", lambda *_a, **_k: 101.2,
    )
    now = datetime.now(timezone.utc)
    bar_ms = current_bar_open_ms("4h", int(now.timestamp() * 1000))
    updated = datetime.fromtimestamp(bar_ms / 1000, tz=timezone.utc) + timedelta(minutes=1)
    dto = _fresh_mid(
        updated=updated,
        expires=now + timedelta(hours=3),
        invalidation={
            "price": 90,
            "_watch": {"price": 100.0, "bar_tf": "4h", "bar_open_ms": bar_ms},
        },
    )
    assert can_open(dto, {"ETH": {"last": 101.2}}) is False


def test_early_refresh_on_newer_chart_conflict(monkeypatch):
    now = datetime.now(timezone.utc)
    dto = ThesisDTO(
        thesis_id="e1", session_id="s", symbol="ETH", tier="mid",
        direction="long", recommend_open=True, accepted=True,
        updated_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=2), analysis_run_id="run-e1",
    )
    monkeypatch.setattr(
        "backend.services.full_auto.midlong_chart_gate._latest_chart_signal",
        lambda _s: {
            "created_ms": int(now.timestamp() * 1000),
            "direction": "bearish",
            "payload": {"position_advice": "no_new_long"},
        },
    )
    assert thesis_needs_early_refresh(dto, "ETH") is True
    stale = ThesisDTO(
        thesis_id="e2", session_id="s", symbol="ETH", tier="mid",
        direction="long", expires_at=now - timedelta(minutes=1),
    )
    assert thesis_needs_early_refresh(stale, "ETH") is False


def test_qual_layer_prompt_allows_should_close_true():
    from backend.services.mlto import qual_layer
    src = Path(qual_layer.__file__).read_text(encoding="utf-8")
    assert '"should_close": true|false' in src
    assert '"should_close": false,' not in src


def test_schema_accepts_long_alias_and_invalidation_dict():
    payload = {
        "direction": "long",
        "strength": 6,
        "confidence": 0.72,
        "recommend_open": True,
        "should_close": False,
        "invalidation": {"price": 100, "condition": "跌破"},
        "entry_zone": {"low": 102.0, "high": 108.0},
        "missing_evidence": [],
        "sl_pct": 0.05,
        "tp_pct": 0.10,
        "thesis_summary": "试多",
        "key_factors": ["结构"],
        "summary": "试多",
    }
    ok, errs = schemas.validate("midlong_thesis", payload)
    assert ok, errs
    ok2, errs2 = schemas.validate("midlong_thesis", {**payload, "direction": "ship"})
    assert not ok2 and any("direction" in e for e in errs2)


def test_unaccepted_should_close_does_not_open():
    now = datetime.now(timezone.utc)
    dirty = ThesisDTO(
        thesis_id="c1", session_id="s", symbol="ETH", tier="long",
        direction="long", recommend_open=False, accepted=False,
        should_close=True, expires_at=now + timedelta(hours=2),
    )
    assert not can_open(dirty)


def test_thesis_is_fresh_requires_analysis_run_id():
    now = datetime.now(timezone.utc)
    shadow = ThesisDTO(
        thesis_id="old", session_id="s", symbol="BTC", tier="long",
        expires_at=now + timedelta(hours=8),
    )
    assert not thesis_is_fresh(shadow)
    brain = ThesisDTO(
        thesis_id="new", session_id="s", symbol="BTC", tier="long",
        expires_at=now + timedelta(hours=8), analysis_run_id="abc",
    )
    assert thesis_is_fresh(brain)
    expired = ThesisDTO(
        thesis_id="exp", session_id="s", symbol="BTC", tier="long",
        expires_at=now - timedelta(minutes=1), analysis_run_id="abc",
    )
    assert not thesis_is_fresh(expired)


def test_order_refresh_symbols_puts_positions_and_stale_first(monkeypatch):
    now = datetime.now(timezone.utc)
    store = {
        ("sid", "ASTER", "long"): ThesisDTO(
            thesis_id="a", session_id="sid", symbol="ASTER", tier="long",
            expires_at=now + timedelta(hours=8), analysis_run_id="r1",
            updated_at=now,
        ),
        ("sid", "ETH", "long"): ThesisDTO(
            thesis_id="e", session_id="sid", symbol="ETH", tier="long",
            updated_at=now - timedelta(hours=2),
        ),
    }

    def _get(session_id, symbol, tier, db=None):
        return store.get((session_id, symbol, tier))

    monkeypatch.setattr("backend.services.mlto.thesis_store.get", _get)
    ordered = order_refresh_symbols(
        "sid", ["ASTER", "BNB", "ETH"], "long", priority=["ETH"],
    )
    assert ordered[0] == "ETH"
    assert ordered[1] == "BNB"
    assert ordered[2] == "ASTER"


def test_refresh_lock_is_non_reentrant_across_threads():
    lock = _named_lock(_REFRESH_LOCKS, _REFRESH_LOCKS_GUARD, "ut:BTC:long")
    assert lock.acquire(blocking=False)
    try:
        assert lock.acquire(blocking=False) is False
    finally:
        lock.release()


def test_hang_timeout_no_longer_force_starts_second_loop():
    src = Path(
        Path(__file__).resolve().parents[2]
        / "services" / "full_auto_trading_service.py"
    ).read_text(encoding="utf-8")
    assert "_MIDLONG_LOOP_HANG_TIMEOUT_SECONDS = 1500" in src
    assert "禁止叠第二轮" in src
    assert "上轮疑似 hang({elapsed:.0f}s)，强制新扫描 {session_id}" not in src


def test_qaa_v3_does_not_double_run_brain_when_independent():
    src = Path(
        Path(__file__).resolve().parents[2]
        / "services" / "full_auto" / "analyst_system_v3_cycle.py"
    ).read_text(encoding="utf-8")
    assert "run_mid=not _ml_independent" in src
    assert "run_long=not _ml_independent" in src


def test_invalidation_requires_accepted_fresh_thesis():
    src = Path(
        Path(__file__).resolve().parents[2]
        / "services" / "full_auto" / "midlong_position_manager.py"
    ).read_text(encoding="utf-8")
    assert "resolve_thesis_hard_exit" in src
    assert "thesis_is_tradeable_fresh" in src
    assert "thesis_should_close" in src
    assert "thesis_invalidation" in src
    # 同向才用失效价，避免空头论题上沿误平多头
    assert 'th_dir == side' in src or "th_dir == side" in src


def test_fail_backoff_shorter_than_success_ttl():
    assert thesis_fail_backoff_s("mid") < thesis_ttl_s("mid")
    assert thesis_fail_backoff_s("long") < thesis_ttl_s("long")
    assert thesis_expiry_s("mid", accepted=True) == thesis_ttl_s("mid")
    assert thesis_expiry_s("mid", accepted=False) == thesis_fail_backoff_s("mid")


def test_soft_missing_stripped_does_not_force_hold():
    out = _force_hold_if_missing({
        "recommend_open": True,
        "confidence": 0.8,
        "missing_evidence": [
            "图审 latest_trend_chart accepted=false",
            "taker_bs 缺失",
            "E1 未触发",
            "corr_to_btc 为空",
            "缺 OI 与资金费",
            "daily_brief 已 stale",
        ],
    })
    assert out["recommend_open"] is True
    assert out["missing_evidence"] == []
    assert strip_soft_missing(["现价", "图审缺图", "相关性矩阵为空"]) == ["现价"]


def test_normalize_invalidation_nested_json_string():
    inv = normalize_invalidation('{"price": 12.5, "condition": "破位"}')
    assert _inv_price(inv) == 12.5
    inv2 = normalize_invalidation({"condition": '{"price": 9.1}'})
    assert _inv_price(inv2) == 9.1


def test_consensus_requires_inv_price():
    cres = SimpleNamespace(accepted=True)
    final = {
        "direction": "bullish",
        "invalidation": {"condition": "破了再说"},
    }
    assert consensus_is_tradeable(cres, final, False) is False
    final["invalidation"] = {"price": 100.0, "condition": "跌破"}
    assert consensus_is_tradeable(cres, final, False) is True


def test_failed_ticket_fresh_until_short_backoff_only():
    now = datetime.now(timezone.utc)
    fail = ThesisDTO(
        thesis_id="f1", session_id="s", symbol="SOL", tier="mid",
        direction="long", accepted=False, recommend_open=False,
        analysis_run_id="run-fail",
        expires_at=now + timedelta(seconds=thesis_fail_backoff_s("mid")),
        updated_at=now,
    )
    assert thesis_is_fresh(fail) is True
    assert thesis_is_tradeable_fresh(fail) is False
    expired_fail = ThesisDTO(
        thesis_id="f2", session_id="s", symbol="SOL", tier="mid",
        accepted=False, analysis_run_id="run-fail2",
        expires_at=now - timedelta(seconds=1),
    )
    assert thesis_is_fresh(expired_fail) is False


def test_midlong_thesis_quota_class_is_event():
    from backend.services.analysis.quota_guard import task_class
    assert task_class("midlong_thesis") == "event"


def test_promote_open_if_price_in_entry_zone():
    from backend.services.mlto.brain import promote_open_if_in_zone
    base = {
        "direction": "bullish",
        "recommend_open": False,
        "invalidation": {"price": 90.0, "condition": "破"},
        "entry_zone": {"low": 98.0, "high": 102.0},
        "thesis_summary": "等回踩",
    }
    out = promote_open_if_in_zone(base, last_price=100.0, has_position=False)
    assert out["recommend_open"] is True
    assert out.get("_promoted_open") == "entry_zone_touched"
    hold = promote_open_if_in_zone(base, last_price=110.0, has_position=False)
    assert hold["recommend_open"] is False
    with_pos = promote_open_if_in_zone(base, last_price=100.0, has_position=True)
    assert with_pos["recommend_open"] is False


def test_merge_outputs_bool_and_or():
    from backend.services.analysis.model_gateway import merge_outputs
    merged = merge_outputs(
        {"recommend_open": True, "should_close": False, "direction": "bullish", "strength": 5},
        {"recommend_open": False, "should_close": True, "direction": "bullish", "strength": 5},
    )
    assert merged["recommend_open"] is False  # AND
    assert merged["should_close"] is True  # OR
    both = merge_outputs(
        {"recommend_open": True, "should_close": False},
        {"recommend_open": True, "should_close": False},
    )
    assert both["recommend_open"] is True


def test_can_open_blocks_midlong_short_no_bias(monkeypatch):
    """空头无下行证据应在 can_open 层拦截，避免 execute_false 刷屏。

    [2026-09-09] 显式声明前提：mid 空头模式已由 conditional 改为默认 off
    （实测 27 笔 short 信号在任何出场参数下费后均为负，见
    backend/tests/unit/test_midlong_location_gate.py::test_short_mode_default_off）。
    本用例测的是 conditional 分支契约，不写死会随生产配置而红。
    """
    monkeypatch.setenv("MIDLONG_OPEN_SHORT_ENABLED", "false")
    monkeypatch.setenv("MIDLONG_SHORT_MODE", "conditional")
    import importlib as _il

    from backend.services.full_auto import midlong_circuit_gate as _cg
    _il.reload(_cg)

    from backend.services.mlto.brain import can_open_block_reason

    _quiet_watch_side_channels(monkeypatch)
    monkeypatch.setattr(
        "backend.services.mlto.brain._spot_price", lambda *_a, **_k: 1.0,
    )
    now = datetime.now(timezone.utc)
    bar_ms = current_bar_open_ms("4h", int(now.timestamp() * 1000))
    updated = datetime.fromtimestamp(bar_ms / 1000, tz=timezone.utc) + timedelta(minutes=1)
    dto = _fresh_mid(
        updated=updated,
        expires=now + timedelta(hours=3),
        direction="short",
        invalidation={
            "price": 1.2,
            "_watch": {"price": 1.0, "bar_tf": "4h", "bar_open_ms": bar_ms},
        },
    )
    ms = {"ASTER": {"current_price": 1.0, "price_change_24h_pct": 0.01}}
    reason = can_open_block_reason(dto, ms, account_id=14)
    assert reason == "midlong_short_no_bias"


def test_bootstrap_cache_age_does_not_stick_stale():
    """扫描缓存 >300s 不再把有价币打成 data_stale（VIRTUAL 粘旗根因）。"""
    import time
    from backend.services.full_auto.market_summary_helpers import (
        MarketSummaryContext,
        bootstrap_market_summary,
    )

    ctx = MarketSummaryContext(
        market_scan_cache={
            "VIRTUAL": {"current_price": 0.72, "data_reliable": True},
        },
        market_scan_cache_ts=time.time() - 900,
    )
    out = bootstrap_market_summary(["VIRTUAL"], ctx)
    assert out["VIRTUAL"]["current_price"] == 0.72
    assert out["VIRTUAL"].get("data_stale") is False
    assert out["VIRTUAL"].get("data_reliable") is True
    assert float(out["VIRTUAL"].get("scan_cache_age_sec") or 0) >= 800
