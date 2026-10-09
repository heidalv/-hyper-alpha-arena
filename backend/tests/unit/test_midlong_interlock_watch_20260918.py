# -*- coding: utf-8 -*-
"""[2026-09-18 根因修复·F] 中线「两闸互斥」互锁探测器的契约测试。

事故背景（`docs/中线开仓冻结根因取证_20260918.md`）：日线 up ⇒ 空头被
`midlong_short_regime_block` 全拒；盘中 ranging + 全市场 24h 分位 80–98% ⇒
多头被追高天花板硬否决 ⇒ mid 几乎无单可开，而**系统不会告诉你这是互锁**。

本文件锁住探测器的三条性质：
1. 只有**两侧都出现过**才算互锁（单侧不算）；
2. 方向推断不出的拦截**不计入**（保持"方向不可知"，绝不猜）；
3. 告警**带节流**（同标的 30 分钟内只告警一次），且窗口外的旧记录不算。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto import midlong_interlock_watch as W  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MIDLONG_INTERLOCK_ALERT", raising=False)
    monkeypatch.delenv("MIDLONG_INTERLOCK_WINDOW_SEC", raising=False)
    monkeypatch.delenv("MIDLONG_INTERLOCK_ALERT_THROTTLE_SEC", raising=False)
    W.reset()
    yield
    W.reset()


def test_single_side_is_not_interlock():
    assert W.note_block("ASTER", "location_gate_veto: 24h区间分位95%≥70% 高位追多", now=1000.0) is None
    assert W.interlocked_symbols(now=1000.0) == ()
    assert W.format_summary() is None


def test_both_sides_within_window_alerts_once():
    # 空头侧先被拦（日线 up 禁空）
    assert W.note_block("ASTER", "midlong_short_regime_block: 日线 regime=up（非下行）",
                        now=1000.0) is None
    # 多头侧随后被拦（追高天花板）⇒ 互锁
    # ⚠️ 这里用**日志里的真实文案**：天花板那条原本不含方向词，探测器识别不出 ⇒
    #    已同时修 `_dir_from_reason`（补"追高天花板"/"追高"）与闸门文案（补"高位追多"）。
    msg = W.note_block("ASTER", "location_gate_veto: 24h区间分位95%≥追高天花板70% 硬否决"
                                 "（高位追多；paper 不追顶；<70% 仍缩仓放行收集样本）",
                       now=1100.0)
    assert msg and "两方向同时被否" in msg and "ASTER" in msg
    assert W.interlocked_symbols(now=1100.0) == ("ASTER",)
    # ① 节流：30 分钟内再来一次不再告警（互锁仍新鲜：short 在 200s 前）
    assert W.note_block("ASTER", "location_gate_veto: …追高天花板…", now=1200.0) is None
    # ② 刷新 short 侧后仍在节流窗内 ⇒ 不告警（互锁新鲜 100s、距上次告警 300s < 1800s）
    W.note_block("ASTER", "midlong_short_regime_block", now=1300.0)
    assert W.note_block("ASTER", "location_gate_veto: …追高天花板…", now=1400.0) is None
    # ③ 两个窗口都满足才再告警：互锁新鲜（short@3000 与 long@3100 差 100s ≤ 900s）
    #    且距上次告警 2000s ≥ 1800s
    W.note_block("ASTER", "midlong_short_regime_block", now=3000.0)
    again = W.note_block("ASTER", "location_gate_veto: …追高天花板…", now=3100.0)
    assert again and "两方向同时被否" in again
    # ④ 反向验证：**互锁窗口过期**时即便过了节流也不告警
    #    （我第一版把"节流窗 1800s"与"互锁窗 900s"混为一谈，误判成 bug）
    W.reset()
    W.note_block("ASTER", "midlong_short_regime_block", now=1000.0)
    assert W.note_block("ASTER", "location_gate_veto: …追高天花板…", now=3000.0) is None


def test_ceiling_message_is_direction_classifiable():
    """**本次事故的原文案必须可判方向** —— 否则互锁探测器对真正的病因失明。"""
    old_text = "location_gate_veto: 24h区间分位95%≥追高天花板70% 硬否决"
    assert W.classify_direction(old_text) == "long", "追高天花板（无方向词）必须能判为 long"
    src = (ROOT / "backend" / "services" / "full_auto"
           / "midlong_location_gate.py").read_text(encoding="utf-8")
    assert "高位追多；paper 不追顶" in src, "闸门文案应带上方向词（便于人读与审计）"


def test_window_expiry_breaks_interlock():
    W.note_block("UNI", "midlong_short_regime_block: 日线 regime=up", now=1000.0)
    # 另一侧在窗口（900s）之外 ⇒ 不算互锁
    assert W.note_block("UNI", "location_gate_veto: 追多", now=1000.0 + 1000.0) is None
    assert W.interlocked_symbols(now=1000.0) == ()


def test_unknown_direction_is_not_counted():
    """推断不出方向的拦截不得参与互锁（与执行器同源的"绝不猜"纪律）。"""
    assert W.classify_direction("some_random_blocker: 无方向信息") == ""
    assert W.note_block("ZEC", "some_random_blocker: 无方向信息", now=1.0) is None
    assert W.snapshot() == {}


def test_direction_classifier_matches_executor_rules():
    assert W.classify_direction("midlong_short_regime_block: 日线 regime=up") == "short"
    assert W.classify_direction("location_gate_veto: 24h区间分位95% 高位追多") == "long"
    assert W.classify_direction("midlong_long_regime_block: 多头受限") == "long"


def test_alert_can_be_disabled(monkeypatch):
    monkeypatch.setenv("MIDLONG_INTERLOCK_ALERT", "false")
    W.note_block("BNB", "midlong_short_regime_block", now=1.0)
    assert W.note_block("BNB", "location_gate_veto 追多", now=2.0) is None


def test_summary_lists_multiple_symbols():
    for s in ("A", "B", "C"):
        W.note_block(s, "midlong_short_regime_block", now=1.0)
        W.note_block(s, "location_gate_veto 追多", now=2.0)
    txt = W.format_summary(now=2.0)
    assert txt and "3 个标的" in txt and "A" in txt and "C" in txt


def test_executor_wires_the_watch():
    """执行器必须真的调用探测器（否则模块再对也不会告警）。"""
    src = (ROOT / "backend" / "services" / "full_auto"
           / "midlong_executor.py").read_text(encoding="utf-8")
    i = src.index("def _record_fail")
    seg = src[i:i + 1400]
    assert "note_block" in seg, "_record_fail 未接入互锁探测"
    assert "logger.warning(_il)" in seg, "告警文本未落日志"

