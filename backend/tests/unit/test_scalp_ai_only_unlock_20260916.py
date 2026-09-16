# -*- coding: utf-8 -*-
"""[调研轮9] 短线车道「窄口径解封 AI 受管标的」契约测试。

背景：`SCALP_OPEN_DISABLED=true`（2026-09-05 因旧因子 scalp 无边际而整体停开）导致
**AI 选币结果一条也开不出来**（auto-coin 只能走短线车道）——这是用户看到的
「AI 选币选了不下单」的最后一层。轮7 审计建议**窄口径解封**：只放行 AI 受管标的。

三闸语义（本测试即契约）：
  1. 旧因子路径 nature=scalp —— **永远拦截**（不受任何新开关影响）；
  2. AI 受管标的（会话 AI 池命中）—— 放行（`SCALP_AI_ONLY_OPEN=true` 时唯一放行对象）；
  3. 非 AI 的 intraday —— 仅在「日内波段总闸 INTRADAY_LLM_ENABLED 开 **且** 非 AI-only 模式」放行；
  4. 平仓/减仓永远放行；mid/long 新开不受本闸影响。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.services.full_auto import scalp_open_gate as g  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "SCALP_OPEN_DISABLED", True, raising=False)
    monkeypatch.setattr(settings, "INTRADAY_LLM_ENABLED", False, raising=False)
    monkeypatch.setenv("SCALP_AI_ONLY_OPEN", "true")
    g._AI_SYM_CACHE.clear()
    yield
    g._AI_SYM_CACHE.clear()


def _ai(monkeypatch, symbols):
    monkeypatch.setattr(g, "is_ai_managed_symbol", lambda s, sid=None: str(s).upper() in symbols)


def test_legacy_factor_scalp_always_blocked(monkeypatch):
    _ai(monkeypatch, {"FET"})
    blocked, reason = g.scalp_new_open_blocked("open", "scalp", "short", symbol="FET", session_id="fa_x")
    assert blocked and reason == "scalp_open_disabled", (blocked, reason)


def test_ai_managed_symbol_allowed(monkeypatch):
    _ai(monkeypatch, {"FET"})
    blocked, reason = g.scalp_new_open_blocked("open", "intraday", "short", symbol="FET", session_id="fa_x")
    assert not blocked, f"AI 受管标的必须放行（reason={reason}）"
    assert reason == "ai_managed_open_allowed"


def test_non_ai_blocked_in_ai_only_mode(monkeypatch):
    _ai(monkeypatch, set())
    blocked, reason = g.scalp_new_open_blocked("open", "intraday", "short", symbol="DOGE", session_id="fa_x")
    assert blocked and reason == "scalp_ai_only_open_non_ai_symbol", (blocked, reason)


def test_non_ai_intraday_allowed_when_lane_on_and_not_ai_only(monkeypatch):
    _ai(monkeypatch, set())
    monkeypatch.setenv("SCALP_AI_ONLY_OPEN", "false")
    monkeypatch.setattr(settings, "INTRADAY_LLM_ENABLED", True, raising=False)
    blocked, _reason = g.scalp_new_open_blocked("open", "intraday", "short", symbol="DOGE", session_id="fa_x")
    assert not blocked, "非 AI-only 模式下，日内波段总闸开启应放行非 AI 的 intraday"


def test_close_and_reduce_always_pass(monkeypatch):
    _ai(monkeypatch, set())
    assert g.scalp_new_open_blocked("close", "scalp", "short") == (False, "")
    assert g.scalp_new_open_blocked("reduce", "scalp", "short") == (False, "")
    assert g.scalp_new_open_blocked("open", "scalp", "short", reduce_only=True) == (False, "")


def test_mid_long_unaffected(monkeypatch):
    _ai(monkeypatch, set())
    assert g.scalp_new_open_blocked("open", "swing", "mid") == (False, "")
    assert g.scalp_new_open_blocked("open", "trend_follow", "long") == (False, "")


def test_gate_open_when_global_switch_off(monkeypatch):
    monkeypatch.setattr(settings, "SCALP_OPEN_DISABLED", False, raising=False)
    assert g.scalp_new_open_blocked("open", "scalp", "short") == (False, "")


def test_ai_only_default_is_on():
    import os
    os.environ.pop("SCALP_AI_ONLY_OPEN", None)
    try:
        assert g.ai_only_open_enabled() is True
    finally:
        os.environ["SCALP_AI_ONLY_OPEN"] = "true"


def test_ai_managed_lookup_fail_closed(monkeypatch):
    """判定异常/查不到 → 视为非 AI（fail-closed，宁可不放行）。"""
    g._AI_SYM_CACHE.clear()
    assert g.is_ai_managed_symbol("", "fa_x") is False
    assert g.is_ai_managed_symbol(None, "fa_x") is False
