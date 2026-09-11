# -*- coding: utf-8 -*-
"""[2026-09-10 §58] L1 入场闸「阈值 vs 文案」一致性 + 漏斗按日切片契约测试。

背景（§58 实证）：
  1. **判决用可配阈值、文案用硬编码状态**：`_l1_up()` 读 `LONG_V2_L1_UP_SCORE`（UI 可调 2..5），
     而文案里的 `state` 来自 `trend_layer.classify()` 的**硬编码 ±3** ⇒ 阈值≠3 时产出
     自相矛盾的审计文本（例：阈值=4、score=3 时写 `L1=up(score=3)，非 up 禁开`）。
  2. **长窗口混读**：`L1=sideways` 是 08-17~09-05 的头号拦截（v2 车道启用期），
     09-05 后该车道关闭、该原因归零；把 36 天与近 5 天混在一起会得出错误结论
     ⇒ `summarize_decision_funnel` 增加按日切片。
  3. **双条件静默否决**：`LONG_TREND_V2=1` 但脑模式启用时 v2 闸不生效，此前无任何日志。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import backend.services.long_trend_v2 as lv2  # noqa: E402
from backend.services.mlto import midlong_direction_audit as mda  # noqa: E402


def _stub_cls(score: float, state: str):
    def _f(symbol):
        return pd.DataFrame({"close": [1.0]}), {"state": state, "score": score, "close": 1.0}
    return _f


@pytest.fixture()
def v2_on(monkeypatch):
    monkeypatch.setattr(lv2, "long_v2_enabled", lambda: True)
    monkeypatch.setenv("LONG_TREND_V2", "1")


# ── ① 阈值与文案一致 ──

def test_threshold_2_allows_score_2_with_consistent_text(v2_on, monkeypatch):
    monkeypatch.setenv("LONG_V2_L1_UP_SCORE", "2")
    monkeypatch.setattr(lv2, "_get_l1_classification", _stub_cls(2.0, "sideways"))
    ok, why = lv2.entry_gate("BTC", "buy")
    assert ok is True, why
    assert "≥ 阈值2" in why and "非 up" not in why, why
    assert "L1=sideways" in why, "文案必须如实反映 state（不得写死 up）"


def test_threshold_4_rejects_score_3_without_contradiction(v2_on, monkeypatch):
    monkeypatch.setenv("LONG_V2_L1_UP_SCORE", "4")
    monkeypatch.setattr(lv2, "_get_l1_classification", _stub_cls(3.0, "up"))
    ok, why = lv2.entry_gate("BTC", "buy")
    assert ok is False
    assert "< 阈值4" in why, why
    assert "未达 L1 入场线" in why, why
    assert "非 up" not in why, f"文案自相矛盾（state=up 却说非 up）: {why}"


def test_threshold_4_allows_score_4(v2_on, monkeypatch):
    monkeypatch.setenv("LONG_V2_L1_UP_SCORE", "4")
    monkeypatch.setattr(lv2, "_get_l1_classification", _stub_cls(4.0, "up"))
    ok, why = lv2.entry_gate("BTC", "buy")
    assert ok is True and "≥ 阈值4" in why, why


def test_audit_prefix_stays_aggregatable(v2_on, monkeypatch):
    """漏斗按 `(` 与 `:` 做前缀聚合 ⇒ 新文案必须仍以 `L1=...` 开头。"""
    monkeypatch.setenv("LONG_V2_L1_UP_SCORE", "3")
    monkeypatch.setattr(lv2, "_get_l1_classification", _stub_cls(1.0, "sideways"))
    ok, why = lv2.entry_gate("BTC", "buy")
    assert not ok
    prefix = why.split("(", 1)[0].split(":", 1)[0].strip()
    assert prefix == "long_trend_v2 L1=sideways", prefix


# ── ② 双条件静默否决 → 至少留痕 ──

def test_v2_requested_but_brain_mode_logs_warning(monkeypatch, caplog):
    import logging
    from backend.config import settings

    monkeypatch.setenv("LONG_TREND_V2", "1")
    monkeypatch.setattr(settings, "MIDLONG_BRAIN_MODE", "llm", raising=False)
    monkeypatch.setattr(lv2, "_V2_IGNORED_WARNED", False, raising=False)
    with caplog.at_level(logging.WARNING):
        assert lv2.long_v2_enabled() is False
    msgs = [r.getMessage() for r in caplog.records]
    assert any("v2 入场闸**不生效**" in m for m in msgs), msgs


def test_v2_disabled_by_env_no_warning(monkeypatch, caplog):
    import logging
    from backend.config import settings

    monkeypatch.setenv("LONG_TREND_V2", "0")
    monkeypatch.setattr(settings, "MIDLONG_BRAIN_MODE", "llm", raising=False)
    monkeypatch.setattr(lv2, "_V2_IGNORED_WARNED", False, raising=False)
    with caplog.at_level(logging.WARNING):
        assert lv2.long_v2_enabled() is False
    assert not [r for r in caplog.records if "不生效" in (r.getMessage() or "")]


# ── ③ 漏斗按日切片 ──

def _w(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_funnel_by_day_slices_periods(tmp_path, monkeypatch):
    live = tmp_path / "audit.jsonl"
    now = time.time()
    day = 86400.0
    _w(live, [
        # 昨天：L1 拦截为主
        {"epoch": now - day - 60, "outcome": "skip", "stage": "trend", "symbol": "BTC",
         "reason": "L1=sideways(score=1) < 阈值3，非 up 禁开"},
        {"epoch": now - day - 30, "outcome": "skip", "stage": "trend", "symbol": "ETH",
         "reason": "L1=sideways(score=2) < 阈值3，非 up 禁开"},
        {"epoch": now - day - 10, "outcome": "opened", "stage": "exec", "symbol": "BTC",
         "reason": "filled"},
        # 今天：通用原因为主
        {"epoch": now - 3600, "outcome": "skip", "stage": "exec", "symbol": "UNI",
         "reason": "evaluate_and_execute_returned_false"},
    ])
    monkeypatch.setenv("MIDLONG_DIRECTION_AUDIT_PATH", str(live))
    s = mda.summarize_decision_funnel(lookback_hours=24 * 3, by_day_days=3)
    days = {d["date"]: d for d in s["by_day"]}
    assert len(days) == 2, s["by_day"]
    tops = [d["top_skip_reasons"][0]["reason"] for d in days.values()]
    assert "L1=sideways" in tops and "evaluate_and_execute_returned_false" in tops, tops
    assert sum(d["opened"] for d in days.values()) == 1
    assert sum(d["n"] for d in days.values()) == 4


def test_funnel_by_day_absent_by_default(tmp_path, monkeypatch):
    live = tmp_path / "audit.jsonl"
    _w(live, [{"epoch": time.time(), "outcome": "skip", "stage": "exec", "symbol": "BTC",
               "reason": "x"}])
    monkeypatch.setenv("MIDLONG_DIRECTION_AUDIT_PATH", str(live))
    s = mda.summarize_decision_funnel(lookback_hours=24)
    assert "by_day" not in s, "默认不应改变返回结构（向后兼容）"
