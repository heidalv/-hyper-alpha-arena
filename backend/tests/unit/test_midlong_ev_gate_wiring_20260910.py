# -*- coding: utf-8 -*-
"""[2026-09-10 §53] MidLongEvGate 接线与可观测性契约测试。

背景（§53.1 实证，`_audit_ml/Z87`/`Z88`）：
  - EV 闸自上线以来 **563 次评估全部走「未校准影子放行」**，拦截 **0** 笔；
  - 根因是**口径分叉**：mid 提案 `trade_nature='swing'` 在 `evaluate_midlong_open`
    里被 `normalize_v5_nature` 归一为 `trend_follow`（第 226 行）**先于** EV 闸调用
    （第 324 行），于是 mid 层永远用 **trend 校准器**（45 天仅 19~30 样本、桶占用不足
    ⇒ `is_calibrated=False`）→ 闸结构性无法生效；而**真正校准好的 swing 校准器**
    （n=79、base=0.278）从不被 EV 闸咨询；
  - 原实现只在"该拦但被影子放行"时打 INFO，若未校准且恰好 EV 达标 → **完全静默**。

本测试锁住：
  1. 归一化发生在 EV 闸之前（源码护栏；防止有人"顺手"改掉而无人知晓）；
  2. `_nature_prefix` 映射（swing→SWING_EV / trend_follow|position→TREND_EV）；
  3. **影子放行现在可见**（限流 WARNING），且**裁决不变**（仍放行）；
  4. 校准生效的**状态变化**被显式告知一次（防无感行为突变）；
  5. 已校准 + EV 不足 → 仍然拦截（原有硬拦行为不被削弱）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from backend.services.calibration import confidence_calibrator as cc  # noqa: E402
from backend.services.decision_core import midlong_ev_gate as evg  # noqa: E402
from backend.services.decision_core import pipeline as pl  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_gate_state():
    """单例计数器在测试间必须干净（否则限流断言会互相干扰）。"""
    evg.midlong_ev_gate._shadow_warn.clear()
    evg.midlong_ev_gate._enforcing_notified.clear()
    yield
    evg.midlong_ev_gate._shadow_warn.clear()
    evg.midlong_ev_gate._enforcing_notified.clear()


class _StubCal:
    def __init__(self, p_win: float, source: str):
        self._p = p_win
        self._s = source

    def estimate_p_win(self, symbol, score, direction="neutral"):
        return cc.CalibrationResult(p_win=self._p, source=self._s, n_samples=99)


def _patch_cal(monkeypatch, p_win: float, source: str):
    monkeypatch.setattr(cc, "get_calibrator_for_nature",
                        lambda nat: _StubCal(p_win, source))


def _enable_mid_enforce(monkeypatch) -> None:
    """[P7 执行 2026-09-10] swing 赛道（=mid 车道）的强制开关：默认 false，测试里显式打开。"""
    from backend.config import settings as _s

    monkeypatch.setattr(_s, "MIDLONG_EV_ENFORCE_MID", True, raising=False)


# ── ① 接线：归一化先于 EV 闸 ──

def test_pipeline_normalizes_nature_before_ev_gate():
    src = inspect.getsource(pl.evaluate_midlong_open)
    i_norm = src.index("nature = norm_nature")
    i_ev = src.index("midlong_ev_gate.evaluate(")
    assert i_norm < i_ev, (
        "evaluate_midlong_open 里 nature 归一必须发生在 EV 闸调用之前——"
        "此顺序决定了 mid 层用哪个校准器（§53.1）"
    )
    assert "normalize_v5_nature" in src[:i_ev]


def test_nature_normalization_collapses_midlong_to_trend_follow():
    from backend.services.decision_core.unified_gate import normalize_v5_nature
    assert normalize_v5_nature("swing") == "trend_follow"
    assert normalize_v5_nature("position") == "trend_follow"
    assert normalize_v5_nature("mid") == "trend_follow"
    assert normalize_v5_nature("long") == "trend_follow"
    assert normalize_v5_nature("intraday") == "intraday"
    assert normalize_v5_nature("weird-nature") == "nature_ambiguous"


# ── ② 前缀映射（决定读哪套校准器与门槛）──

def test_nature_prefix_mapping():
    assert evg._nature_prefix("swing") == "SWING_EV"
    assert evg._nature_prefix("trend_follow") == "TREND_EV"
    assert evg._nature_prefix("position") == "TREND_EV"
    assert evg._nature_prefix("") == "SWING_EV"  # 缺省按 swing 语义


# ── ③ 影子放行必须可见（裁决不变）──

def test_shadow_allow_is_visible_and_still_allows(monkeypatch, caplog):
    _patch_cal(monkeypatch, 0.45, "cold_linear")  # 未校准
    with caplog.at_level("WARNING"):
        d = evg.midlong_ev_gate.evaluate(
            nature="trend_follow", symbol="ETH", score=60.0, direction="long",
            tp_pct=0.10, sl_pct=0.05, notional_usd=2000.0,
        )
    assert d.allowed is True, "影子模式必须仍然放行（本次只加日志）"
    assert d.breakdown.get("shadow_cold_start") is True
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= 30]
    assert any("影子放行" in m for m in msgs), f"影子放行未在 WARNING 留痕: {msgs}"


def test_shadow_warning_is_rate_limited(monkeypatch, caplog):
    _patch_cal(monkeypatch, 0.45, "cold_linear")
    with caplog.at_level("WARNING"):
        for _ in range(5):
            evg.midlong_ev_gate.evaluate(
                nature="position", symbol="BTC", score=60.0, direction="long",
                tp_pct=0.10, sl_pct=0.05, notional_usd=2000.0,
            )
    hits = [m for m in (r.getMessage() for r in caplog.records) if "影子放行" in m]
    assert len(hits) == 1, f"限流失效（5 次调用应只报 1 次）: {hits}"
    assert evg.midlong_ev_gate._shadow_warn["position"] == 5


# ── ④ 校准生效的状态变化必须显式告知一次 ──

def test_enforcing_transition_warned_once(monkeypatch, caplog):
    _patch_cal(monkeypatch, 0.30, "calibrated")  # 已校准且 EV 必然不足
    # [P7 执行 2026-09-10] swing 赛道（=mid 车道）的**强制**改由显式开关控制：
    # 默认 false 时即使校准生效也只影子记录（避免接线副作用直接关闭 mid 车道，
    # 见 test_ev_gate_calib_nature_20260910 的安全护栏用例）。本用例验证的是
    # "开始硬拦"的状态变化提示，故显式打开开关。
    _enable_mid_enforce(monkeypatch)
    with caplog.at_level("WARNING"):
        d1 = evg.midlong_ev_gate.evaluate(
            nature="swing", symbol="ETH", score=60.0, direction="long",
            tp_pct=0.05, sl_pct=0.045, notional_usd=2000.0,
        )
        d2 = evg.midlong_ev_gate.evaluate(
            nature="swing", symbol="BTC", score=60.0, direction="long",
            tp_pct=0.05, sl_pct=0.045, notional_usd=2000.0,
        )
    assert d1.allowed is False and d2.allowed is False, "已校准 + EV 不足 + 开关开启必须拦截"
    warn = [r.getMessage() for r in caplog.records
            if r.levelno >= 30 and "开始硬拦" in (r.getMessage() or "")]
    assert len(warn) == 1, f"状态变化提示应为一次: {warn}"
