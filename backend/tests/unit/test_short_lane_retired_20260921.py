# -*- coding: utf-8 -*-
"""[2026-09-27 重写] 方向对称契约 —— 取代原「短线车道已退休」焊死测试。

## 沿革与为什么重写

原测试（轮160 2026-09-21）把「短线车道退役」钉死为契约：`TIER_SHORT_BUDGET=0`
必须成立、「永久关闭」字样必须存在。用户 2026-09-27 明确指令撤销一切「不做空」封锁：

> 「交易是周期性的。趋势做多可以，趋势转空还一直做多等于自杀。」
> 「我之前就命令禁止这个行为、让解开，不许有这个限制。」

焊死「永久关闭」= 把一次 regime 样本偏差下的结论写进不可撤销的底层。本文件改为锁定
**方向对称**契约：

1. 报告车道结构不变（short/scalp 并入日内车道，轮63 重构，与方向无关）；
2. 空头总开关默认**开启**：`short_lane_enabled()` 默认 True、`short_min_pwin()` 默认
   0.60（实验门槛），999 哨兵只作显式回滚；
3. mid/long 空头在 down regime 必须**可开**（regime_gated 策略），不再有部署级禁令；
4. 短线车道（tier=short）注册表条目不得声称「永久关闭」（可经晋升流程恢复）。
"""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config import lane_semantics as LS  # noqa: E402


def test_only_two_lanes_enter_the_report():
    assert LS.REPORT_LANES == (LS.LANE_INTRADAY, LS.LANE_TREND), \
        "进周期报告的车道只允许日内与长期趋势两条"
    assert LS.LANE_RESEARCH in LS.ALL_LANES and LS.LANE_RESEARCH not in LS.REPORT_LANES


def test_short_and_scalp_map_into_the_intraday_lane():
    """short / scalp 不是独立车道 —— 它们并入日内（存量数据仍可读）。

    这是**报告口径**的重构（轮63），与「能否开空」无关：日内车道做空
    由 midlong_circuit_gate 的 regime 门治理。
    """
    for key in ("short", "scalp", "mid", "midlong", "swing"):
        assert LS.get_spec(key).lane == LS.LANE_INTRADAY, f"{key} 应归入日内车道"


def test_short_direction_is_enabled_by_default():
    """[2026-09-27 用户指令] 做空信号闸默认开启，不再默认全关。"""
    from backend.services.scalp import short_lane_policy as SLP

    for k in ("SCALP_SHORT_ENABLED", "SCALP_SHORT_MIN_PWIN"):
        os.environ.pop(k, None)
    importlib.reload(SLP)
    assert SLP.short_lane_enabled() is True
    assert SLP.short_min_pwin() == 0.60


def test_midlong_short_opens_in_down_regime(monkeypatch):
    """mid/long 空头：总开关 true + regime_gated 时，down regime 必须可开。

    这是「趋势转空还能做空」的核心契约——单边只多会结构性裸奔。
    """
    monkeypatch.setenv("MIDLONG_CIRCUIT_ENABLED", "true")
    monkeypatch.setenv("MIDLONG_OPEN_SHORT_ENABLED", "true")
    monkeypatch.setenv("MIDLONG_SHORT_MODE", "regime_gated")
    monkeypatch.setenv("MIDLONG_DOWN_SHORT_MODE", "allowed")
    monkeypatch.setenv("MIDLONG_CHOP_MODE", "long_only")
    monkeypatch.setenv("MIDLONG_LONG_MODE", "regime_only")
    monkeypatch.setenv("MIDLONG_LEARNED_PAPER_PROBE", "false")
    mod = importlib.import_module("backend.services.full_auto.midlong_circuit_gate")
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_STATE_FILE", str(ROOT / "data" / "_test_circuit_state.json"))
    monkeypatch.setattr(mod, "_state", {})
    monkeypatch.setattr(mod, "_loaded", True)
    monkeypatch.setattr(mod, "_daily_regime", lambda sym: "down")
    ok, reason = mod.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert ok, f"down regime 空头必须可开: {reason}"


def test_no_permanent_closure_claim_in_lane_registry():
    """注册表不得再把短线车道钉为「永久关闭」——退役可逆（晋升流程）。"""
    src = (ROOT / "backend" / "services" / "lane_registry.py").read_text(encoding="utf-8")
    assert "永久关闭" not in src, (
        "lane_registry 不得声称任何车道永久关闭；退役必须可经晋升判定恢复"
    )


def test_cleanup_script_exists_and_documents_why():
    p = ROOT / "scripts/_fix160_archive_short_lane.py"
    assert p.exists(), "短线遗留清理脚本丢失"
    txt = p.read_text(encoding="utf-8")
    assert "SCALP_OPEN_DISABLED" in txt
