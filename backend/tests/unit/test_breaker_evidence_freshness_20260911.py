# -*- coding: utf-8 -*-
"""[§84 契约 2026-09-11 / 决策 P27-A / 缺陷 #69] 熔断"证据新鲜度"的读写与回填。

三件事必须锁住：
  1. `record_close()` 给该通道写 `last_ts`（否则新鲜度尺子无源）；
  2. `exit_channel_evidence_fresh()` 的口径：新鲜/过期/无时间戳/约束关闭（0）；
  3. DB 回填（P21）也能写时间戳，且 `merge_into_state` 取**较新**者（DB 为准）。
"""
from __future__ import annotations

import json
import time

import pytest

import backend.services.source_attribution as sa
from backend.services.exit import breaker_backfill as bf


def _fresh(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    monkeypatch.setenv("EXIT_CHANNEL_REBUILD_ON_LOAD", "false")
    return sa.SourceAttribution(), state


def test_record_close_writes_last_ts(tmp_path, monkeypatch):
    a, state = _fresh(tmp_path, monkeypatch)
    a.tag_position(1, source="mlto", nature="swing", symbol="BTC")
    before = time.time()
    a.record_close(1, pnl=-1.0, fee=0.0, close_reason="trend_broken", tier="mid",
                   symbol="BTC", nature="swing")
    ts = (a._breaker.get("mid|trend_broken") or {}).get("last_ts")
    assert ts is not None and ts >= before - 1, "record_close 未写 last_ts"


def test_fresh_evidence_passes(tmp_path, monkeypatch):
    a, _ = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("BREAKER_EVIDENCE_STALE_DAYS", "7")
    a.tag_position(2, source="mlto", nature="swing", symbol="BTC")
    a.record_close(2, pnl=-1.0, fee=0.0, close_reason="trend_broken", tier="mid",
                   symbol="BTC", nature="swing")
    fresh, age, limit = a.exit_channel_evidence_fresh("trend_broken", "mid")
    assert fresh is True and age is not None and age < 0.01 and limit == 7.0


def test_stale_evidence_blocked(tmp_path, monkeypatch):
    """把 last_ts 手工改成 14 天前 ⇒ 必须判为不新鲜（= 线上 mid|trend_broken 现状）。"""
    a, state = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("BREAKER_EVIDENCE_STALE_DAYS", "7")
    a._breaker["mid|trend_broken"] = {"n": 65, "wins": 13, "recent": [0] * 30,
                                      "last_ts": time.time() - 14.1 * 86400}
    a._breaker_shadow["mid|trend_broken"] = True
    fresh, age, limit = a.exit_channel_evidence_fresh("trend_broken", "mid")
    assert fresh is False and age == pytest.approx(14.1, abs=0.1) and limit == 7.0


def test_missing_timestamp_is_not_fresh(tmp_path, monkeypatch):
    """旧状态文件没有 last_ts ⇒ 不从"未知"推断"新鲜"（fail-open，不抑制）。"""
    a, _ = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("BREAKER_EVIDENCE_STALE_DAYS", "7")
    a._breaker["mid|midlong"] = {"n": 17, "wins": 5, "recent": [0] * 17}
    fresh, age, limit = a.exit_channel_evidence_fresh("midlong", "mid")
    assert fresh is False and age is None and limit == 7.0


def test_zero_limit_disables_constraint(tmp_path, monkeypatch):
    """`BREAKER_EVIDENCE_STALE_DAYS=0` ⇒ 约束关闭（回到旧行为：永远"新鲜"）。"""
    a, _ = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("BREAKER_EVIDENCE_STALE_DAYS", "0")
    a._breaker["mid|midlong"] = {"n": 17, "wins": 5, "recent": [0] * 17}
    fresh, age, limit = a.exit_channel_evidence_fresh("midlong", "mid")
    assert fresh is True and limit == 0.0


def test_boundary_exactly_at_limit_is_fresh(tmp_path, monkeypatch):
    """边界：恰好 7 天 ⇒ 仍算新鲜（`<=` 口径，与 Z241 的纯规则一致）。"""
    a, _ = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("BREAKER_EVIDENCE_STALE_DAYS", "7")
    a._breaker["mid|midlong"] = {"n": 17, "wins": 5, "recent": [0] * 17,
                                 "last_ts": time.time() - 7 * 86400 + 5}
    fresh, _age, _limit = a.exit_channel_evidence_fresh("midlong", "mid")
    assert fresh is True


# ── 回填侧（P21 × P27-A）────────────────────────────────────────────────────

def test_backfill_series_carries_last_ts():
    rows = [("trend_broken", "mid", False, 1000.0),
            ("trend_broken", "mid", True, 2000.0),
            ("trend_broken", "mid", False, 1500.0)]
    ser = bf.series_from_rows(rows)
    st = ser["mid|trend_broken"]
    assert st["n"] == 3 and st["last_ts"] == 2000.0, "回填未取最新时间戳"


def test_backfill_merge_keeps_newer_timestamp():
    breaker = {"mid|trend_broken": {"n": 10, "wins": 2, "recent": [0] * 5, "last_ts": 500.0}}
    series = {"mid|trend_broken": {"n": 12, "wins": 3, "recent": [0] * 6, "last_ts": 900.0}}
    new, _stats = bf.merge_into_state(breaker, series)
    assert new["mid|trend_broken"]["last_ts"] == 900.0
    # 旧时间戳不倒退
    series2 = {"mid|trend_broken": {"n": 12, "wins": 3, "recent": [0] * 6, "last_ts": 100.0}}
    new2, _ = bf.merge_into_state(new, series2)
    assert new2["mid|trend_broken"]["last_ts"] == 900.0
    # 计数只增不减（既有硬规则不变）
    assert new2["mid|trend_broken"]["n"] == 12


def test_backfill_row_without_ts_is_supported():
    """3 元组（旧调用）仍可用 ⇒ 不写 last_ts（向后兼容）。"""
    ser = bf.series_from_rows([("midlong", "mid", False)])
    assert "last_ts" not in ser["mid|midlong"]
