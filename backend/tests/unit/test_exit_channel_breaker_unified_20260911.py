# -*- coding: utf-8 -*-
"""[§78 / 决策 P19-B + P20] 出场通道熔断：共享闸 + 两条真实路径接线 + 重启重建。

分工（`_audit_ml/Z211/Z212` 实测）：
  * `UnifiedExitExecutor` 只被 **Master 路径**调用（`master_execution` / `hold_timeout_trend_review`），
    其"软退出免疫"仅覆盖 `ai_reverse`/`master_running*` 5 个通道；
  * 当前出血通道（`thesis_*`/`midlong`/`profit_drawdown_*`）走 **MLTO 路径**
    （`midlong_position_manager._exec_close`，5 个调用点）。
  ⇒ P19-B 必须**同时**接这两处，才算"统一出口全接"。

本测试锁定：
  1. 共享闸：保护性通道永不抑制、叙事通道按熔断表抑制、开关可回滚、异常 fail-open 可见；
  2. MLTO 收口点 `_exec_close` 与 Master 路径 `should_block` **都**接线；
  3. 重启重建（P20）在 `.env` 里已打开，且阈值锚定真实生效值（15/0.40）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import source_attribution as sa  # noqa: E402
from backend.services.exit import channel_breaker_gate as g  # noqa: E402


def _shadow_only(monkeypatch, keys: set[str], *, age_days: float = 0.0):
    """把给定通道设为 shadow。默认**证据新鲜**（`age_days=0`）。

    [§84 / P27-A] 抑制现在还要过"证据新鲜度"闸：过期 ⇒ 只记录不抑制。
    因此这里的替身必须同时给出 `exit_channel_evidence_fresh` 的返回值，
    否则测的就变成"无时间戳 ⇒ 不抑制"路径（那是另一个专门的用例）。
    """
    monkeypatch.setattr(
        sa.attribution, "exit_channel_shadow",
        lambda r, t: f"{t or '?'}|{sa.normalize_reason(r)}" in keys,
        raising=False,
    )
    limit = 7.0
    monkeypatch.setattr(
        sa.attribution, "exit_channel_evidence_fresh",
        lambda r, t: ((age_days <= limit), age_days, limit),
        raising=False,
    )


# ── 1. 共享闸本身 ──
def test_channel_of_parsing():
    assert g.channel_of("trend_broken: 多周期共振反向") == "trend_broken"
    assert g.channel_of("[no_progress] hold=75.6h") == "no_progress"
    assert g.channel_of("sl") == "sl"
    assert g.channel_of("") == ""


def test_protected_channels_never_suppressed(monkeypatch):
    _protected = ("sl", "tp", "liquidation", "emergency_drawdown", "hardfact_gate",
                  "profit_drawdown_full", "breakeven_tp", "trailing_stop",
                  "max_hold_timeout", "dust_cleanup",
                  # [§82/P24-A] 硬语义通道（论题已死 / 撤币）纳入白名单
                  "thesis_invalidation", "symbol_removed")
    _shadow_only(monkeypatch, {f"mid|{c}" for c in _protected})
    for ch in _protected:
        sup, why = g.should_suppress(f"{ch}: x", "mid")
        assert sup is False, f"保护性通道被抑制了: {ch} ({why})"


def test_p24a_hard_semantics_channels_are_protected():
    """[§82/P24-A] 白名单必须**恰好**含这两条硬语义通道（防回退）。"""
    for ch in ("thesis_invalidation", "symbol_removed"):
        assert g.is_protected(g.channel_of(f"{ch}: x")) is True
    # 未纳入的同类通道仍参与熔断（逐项记账，不得顺手扩白名单）
    for ch in ("thesis_should_close", "swing_invalidation", "exit_policy", "no_progress"):
        assert g.is_protected(g.channel_of(f"{ch}: x")) is False, f"{ch} 被顺手加白了（超出 P24-A 决策范围）"


def test_narrative_channel_suppressed_when_shadowed(monkeypatch):
    _shadow_only(monkeypatch, {"mid|thesis_should_close", "mid|midlong", "long|swing_invalidation"})
    for ch, tier in (("thesis_should_close", "mid"), ("midlong", "mid"),
                     ("swing_invalidation", "long")):
        sup, why = g.should_suppress(f"{ch}: x", tier)
        assert sup is True and why == f"{tier}|{ch}", (ch, tier, why)


# ── 1′. [§84 / 决策 P27-A] 证据新鲜度约束（缺陷 #69） ──
def test_stale_evidence_is_not_suppressed(monkeypatch, caplog):
    """**核心契约**：shadow 但证据过期 ⇒ **只记录不抑制** + WARNING（带 age/阈值）。

    依据：抑制发生在记账之前 ⇒ 被抑制通道不再产生样本 ⇒ 胜率永久冻结（自锁）。
    实测 `mid|trend_broken` 最新样本 14.1 天、`mid|midlong` 8.0 天却仍会抑制。
    """
    _shadow_only(monkeypatch, {"mid|trend_broken"}, age_days=14.1)
    with caplog.at_level(logging.WARNING):
        sup, why = g.should_suppress("trend_broken: x", "mid")
    assert sup is False, "证据过期仍然抑制了（缺陷 #69 复发）"
    assert why.startswith("stale_evidence:mid|trend_broken"), why
    assert "证据过期" in caplog.text and "14.1" in caplog.text, \
        "过期降级必须可见（WARNING 且带 age）"


def test_fresh_evidence_still_suppresses(monkeypatch):
    """新鲜证据（0.5 天）照常抑制 —— 约束只拦过期，不削弱熔断本身。"""
    _shadow_only(monkeypatch, {"mid|trend_broken"}, age_days=0.5)
    sup, why = g.should_suppress("trend_broken: x", "mid")
    assert sup is True and why == "mid|trend_broken"


def test_missing_timestamp_is_treated_as_stale(monkeypatch):
    """无时间戳（旧状态文件）⇒ 无从证明新鲜 ⇒ 不抑制（fail-open，与 P17 同款）。"""
    monkeypatch.setattr(sa.attribution, "exit_channel_shadow", lambda r, t: True, raising=False)
    monkeypatch.setattr(sa.attribution, "exit_channel_evidence_fresh",
                        lambda r, t: (False, None, 7.0), raising=False)
    sup, why = g.should_suppress("midlong: x", "mid")
    assert sup is False and "stale_evidence" in why and "无时间戳" in why


def test_switch_off_and_tier_skip(monkeypatch):
    _shadow_only(monkeypatch, {"mid|midlong", "short|midlong"})
    import backend.config.settings as S

    monkeypatch.setattr(S, "EXIT_CHANNEL_BREAKER_UNIFIED", False, raising=False)
    assert g.should_suppress("midlong: x", "mid")[0] is False
    monkeypatch.setattr(S, "EXIT_CHANNEL_BREAKER_UNIFIED", True, raising=False)
    assert g.should_suppress("midlong: x", "intraday")[0] is False
    # scalp 是 short tier 的口语别名 ⇒ 查 short 键
    sup, why = g.should_suppress("midlong: x", "scalp")
    assert sup is True and why == "short|midlong", (sup, why)


def test_error_fails_open_with_warning(monkeypatch, caplog):
    def _boom(r, t):  # noqa: ANN001
        raise RuntimeError("attribution down")

    monkeypatch.setattr(sa.attribution, "exit_channel_shadow", _boom, raising=False)
    with caplog.at_level(logging.WARNING):
        sup, why = g.should_suppress("midlong: x", "mid")
    assert sup is False and why.startswith("error:")
    assert any("判定异常" in r.getMessage() for r in caplog.records), "fail-open 不可静默"


# ── 2. 两条路径都接线 ──
def test_master_path_is_wired(monkeypatch):
    from backend.services.unified_exit_executor import ExitExecuteRequest, UnifiedExitExecutor

    _shadow_only(monkeypatch, {"mid|thesis_should_close"})
    req = ExitExecuteRequest(
        db=None, account_id=14, symbol="ETH", action="close",
        pos={"id": 1, "symbol": "ETH", "side": "long", "timeframe_tier": "mid"},
        exit_channel="thesis_should_close", reason="thesis_should_close: 论题判死",
    )
    res = UnifiedExitExecutor().should_block(req)
    assert res.blocked is True and res.event_type == "exit_channel_broken", res


def test_mlto_choke_point_is_wired(monkeypatch):
    """`midlong_position_manager._exec_close` 必须接线（当前出血通道的真实出口）。"""
    import inspect

    from backend.services.full_auto import midlong_position_manager as mpm

    src = inspect.getsource(mpm._exec_close)
    assert "channel_breaker_gate" in src, "_exec_close 未接通道熔断（P19-B 的 MLTO 半边缺失）"
    assert "should_suppress" in src
    assert "exit_channel_broken" in src, "抑制必须留痕（事件/日志）"


def test_mlto_close_is_suppressed_end_to_end(monkeypatch):
    """行为验证：被熔断的通道在 `_exec_close` 上不得真正平仓。"""
    from backend.services.full_auto import midlong_position_manager as mpm

    called: list = []
    monkeypatch.setattr(
        "backend.services.paper_trading_engine.paper_engine",
        type("PE", (), {"close_position": staticmethod(lambda *a, **k: called.append(1) or {"pnl": 0})})(),
        raising=False,
    )
    _shadow_only(monkeypatch, {"mid|midlong"})

    class _Host:
        def append_event(self, *a, **k):
            return None

    res = mpm._exec_close(
        None, account_id=14,
        position={"id": 1, "symbol": "ETH", "side": "long", "trade_nature": "swing",
                  "timeframe_tier": "mid", "strategy_id": None},
        reason="midlong: 综合离场", host=_Host(), session=None,
    )
    assert res is None, "被熔断的通道仍然执行了平仓"
    assert called == [], "paper_engine.close_position 不应被调用"


def test_env_flags_and_thresholds():
    """P19/P20 落地值 + 真实生效阈值。"""
    import os

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    assert os.environ.get("EXIT_CHANNEL_BREAKER_UNIFIED", "").lower() == "true"
    assert os.environ.get("EXIT_CHANNEL_REBUILD_ON_LOAD", "").lower() == "true"
    assert os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N") == "15"
    from backend.config.env_registry import KNOWN_FLAGS

    for k in ("EXIT_CHANNEL_BREAKER_UNIFIED", "EXIT_CHANNEL_REBUILD_ON_LOAD"):
        assert k in KNOWN_FLAGS
