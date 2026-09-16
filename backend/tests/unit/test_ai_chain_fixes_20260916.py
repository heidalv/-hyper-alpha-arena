# -*- coding: utf-8 -*-
"""[2026-09-16 调研轮7] AI 决策链修复契约测试。

覆盖（全部来自 `reports/_调研_AI决策链_20260916.md` 的证据）：
  * D0 平台选币看板调度器跟随消费方总闸（AUTO_COIN_ENABLED=false 时不再空烧 LLM）；
  * D1 论题主脑输出上限不得再被放大（128000 → 4096，p99 302s / max 598s 的根因）；
  * D2 失败退避不得再被放大（退避即 TTL，一次失败锁死 symbol:tier 2h/4h）；
  * S  `llm_arbitrate_conflict` 的 `timeout_s=` 形参名错误（必然 TypeError→静默恒拒单）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config import settings as S  # noqa: E402
from backend.services import source_attribution as sa  # noqa: E402
from backend.services.llm_config_service import call_llm_api_sync  # noqa: E402


# ───────────────────────── D0：平台选币调度器总闸 ─────────────────────────

def test_platform_scheduler_follows_consumer_switch(monkeypatch):
    monkeypatch.delenv("COIN_SELECT_PLATFORM_SCHEDULER_ENABLED", raising=False)
    monkeypatch.setenv("AUTO_COIN_ENABLED", "false")
    assert S._coin_select_platform_scheduler_default() is False, "无消费方时不得继续生成候选"
    monkeypatch.setenv("AUTO_COIN_ENABLED", "true")
    assert S._coin_select_platform_scheduler_default() is True


def test_platform_scheduler_explicit_override(monkeypatch):
    monkeypatch.setenv("AUTO_COIN_ENABLED", "false")
    monkeypatch.setenv("COIN_SELECT_PLATFORM_SCHEDULER_ENABLED", "true")
    assert S._coin_select_platform_scheduler_default() is True   # 显式开（如只要网页看板）
    monkeypatch.setenv("COIN_SELECT_PLATFORM_SCHEDULER_ENABLED", "off")
    monkeypatch.setenv("AUTO_COIN_ENABLED", "true")
    assert S._coin_select_platform_scheduler_default() is False  # 显式关优先


# ───────────────────────── S：仲裁调用形参名 ─────────────────────────

def test_llm_api_sync_signature_has_timeout_not_timeout_s():
    """`timeout_s=` 无 **kwargs 兜底 ⇒ 必然 TypeError（曾把仲裁变成恒拒单）。"""
    params = inspect.signature(call_llm_api_sync).parameters
    assert "timeout" in params
    assert "timeout_s" not in params
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def test_llm_arbitrate_passes_timeout_kwarg(monkeypatch):
    import backend.services.llm_config_service as lcs

    captured = {}

    monkeypatch.setattr(lcs, "get_llm_config_for_account", lambda *a, **k: object())

    def _fake(cfg, messages=None, **kw):
        captured.update(kw)
        return {"content": '{"allow": true, "reason": "ok"}'}

    monkeypatch.setattr(lcs, "call_llm_api_sync", _fake)
    monkeypatch.setenv("FUSION_ARBITRATE_LLM", "true")
    out = sa.llm_arbitrate_conflict(
        symbol="BTC", direction="long", thesis_dir="short",
        thesis_conf=0.6, pwin=0.5, factor_score=70, account_id=1,
    )
    assert out is True, "仲裁应正常返回 True（而不是因 TypeError 静默 None）"
    assert captured.get("timeout") == 8
    assert "timeout_s" not in captured


# ───────────────────────── D1/D2：配置不得再被放大（回归闸）─────────────────────────

def _env_map() -> dict:
    out = {}
    for ln in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def test_env_llm_caps_not_re_inflated():
    """D1：论题输出上限 128000 曾让单次调用 p99=302s/max=598s（累计 128.4 墙钟小时）。"""
    env = _env_map()
    assert int(env.get("MIDLONG_BRAIN_MAX_OUTPUT_TOKENS", "4096")) <= 8192, env.get(
        "MIDLONG_BRAIN_MAX_OUTPUT_TOKENS"
    )


def test_env_thesis_backoff_not_re_inflated():
    """D2：退避即 TTL —— 放大到 7200/14400 会把 symbol:tier 锁 2h/4h（51.4% 论题失败）。"""
    env = _env_map()
    assert int(env.get("MIDLONG_THESIS_FAIL_BACKOFF_MID_S", "1200")) <= 3600, env.get(
        "MIDLONG_THESIS_FAIL_BACKOFF_MID_S"
    )
    assert int(env.get("MIDLONG_THESIS_FAIL_BACKOFF_LONG_S", "2400")) <= 7200, env.get(
        "MIDLONG_THESIS_FAIL_BACKOFF_LONG_S"
    )
