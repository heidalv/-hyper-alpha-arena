"""[2026-09-24 用户指令] 长线分档止盈执行化 + 存量仓管理与入场闸解耦 —— 单测。

背景（用户实盘观测）：长线"从来没有止盈"、四个长线仓峰值 +6.4%~+14.2% 全程无锁定、回吐 ≈$100。
两个根因：
  1. 车道声明 tp_stages=[8,15,25]% 只声明不执行（exit_policy.py:22 原文）⇒ 本测试第一部分；
  2. `manage_long_position` 与 midlong 循环的 V2 管理块都被**入场闸** LONG_TREND_V2(=0) 判死
     ⇒ 存量仓从未被管理 ⇒ 本测试第二部分。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.long_tier_manager import decide_long  # noqa: E402
from backend.services.long_trend_v2 import _manage_always_enabled  # noqa: E402


def _base(**kw):
    args = dict(
        l1_state="up", close=100.0, stop=80.0, new_high=False, r_multiple=0.0,
        in_position=True, cur_sl=95.0, peak_r=1.0, hold_days=1.0,
        drawdown_pct=0.0, pyr_batch=99, entry_price=100.0, peak_pnl_pct=0.0,
    )
    args.update(kw)
    return decide_long(**args)


def test_stage1_fires_at_8pct(monkeypatch):
    monkeypatch.setenv("LONG_STAGED_TP_EXEC", "true")
    d = _base(close=108.5, peak_pnl_pct=0.085, cur_sl=100.0)
    assert d["action"] == "reduce", d
    assert d["stage"] == 1 and abs(d["ratio"] - 0.5) < 1e-9
    assert d["new_sl"] == 100.0, "触发后应把 SL 推保本"
    assert "staged_tp1" in d["reason"]


def test_stage2_after_stage1(monkeypatch):
    monkeypatch.setenv("LONG_STAGED_TP_EXEC", "true")
    d = _base(close=115.5, peak_pnl_pct=0.155, staged_tp_done=[1])
    assert d["action"] == "reduce" and d["stage"] == 2
    assert abs(d["ratio"] - 0.5) < 1e-9


def test_stage3_clears_rest(monkeypatch):
    monkeypatch.setenv("LONG_STAGED_TP_EXEC", "true")
    d = _base(close=125.5, peak_pnl_pct=0.255, staged_tp_done=[1, 2])
    assert d["action"] == "reduce" and d["stage"] == 3
    assert abs(d["ratio"] - 1.0) < 1e-9, "第三档应清掉剩余（原仓 25%）"


def test_idempotent_when_all_stages_done(monkeypatch):
    monkeypatch.setenv("LONG_STAGED_TP_EXEC", "true")
    d = _base(close=130.0, peak_pnl_pct=0.30, staged_tp_done=[1, 2, 3])
    assert d["action"] != "reduce", f"三档已触发不应再减仓: {d}"


def test_below_stage1_no_action(monkeypatch):
    monkeypatch.setenv("LONG_STAGED_TP_EXEC", "true")
    d = _base(close=107.9, peak_pnl_pct=0.079)
    assert d["action"] != "reduce" or d.get("stage") is None, d


def test_switch_off_disables(monkeypatch):
    monkeypatch.setenv("LONG_STAGED_TP_EXEC", "false")
    d = _base(close=130.0, peak_pnl_pct=0.30)
    assert d.get("stage") is None, "开关关闭时不得触发分档止盈"


def test_risk_exit_takes_precedence(monkeypatch):
    """结构破坏 / Chandelier 打穿必须优先于分档止盈。"""
    monkeypatch.setenv("LONG_STAGED_TP_EXEC", "true")
    d1 = _base(l1_state="down", close=130.0, peak_pnl_pct=0.30)
    assert d1["action"] == "close" and "结构破坏" in d1["reason"]
    d2 = _base(close=130.0, stop=131.0, peak_pnl_pct=0.30)
    assert d2["action"] == "close" and "Chandelier" in d2["reason"]


def test_manage_always_enabled_default_true(monkeypatch):
    monkeypatch.delenv("LONG_V2_MANAGE_ALWAYS", raising=False)
    assert _manage_always_enabled() is True, "默认必须为 true（存量仓管理不受入场闸影响）"
    monkeypatch.setenv("LONG_V2_MANAGE_ALWAYS", "false")
    assert _manage_always_enabled() is False


def test_lock_profit_frac_default_third_and_switchable(monkeypatch):
    """[第15轮] 锁利比例开关 `LONG_LOCK_PEAK_FRAC`：默认 1/3（现状），设 0.5 = V4 候选，非法值回退。

    第 7 轮变体对照：已平仓 10 笔口径 V4(1/2) −$3.49 vs V0(1/3) −$6.73；因样本不足**未启用**，
    只把候选做成开关（默认仍是 1/3 ⇒ 行为不变）。
    """
    from backend.services.long_tier_manager import lock_profit_frac

    monkeypatch.delenv("LONG_LOCK_PEAK_FRAC", raising=False)
    assert abs(lock_profit_frac() - 1.0 / 3.0) < 1e-6
    monkeypatch.setenv("LONG_LOCK_PEAK_FRAC", "0.5")
    assert abs(lock_profit_frac() - 0.5) < 1e-9
    monkeypatch.setenv("LONG_LOCK_PEAK_FRAC", "abc")
    assert abs(lock_profit_frac() - 1.0 / 3.0) < 1e-6, "非法值必须回退默认"
    monkeypatch.setenv("LONG_LOCK_PEAK_FRAC", "0")
    assert abs(lock_profit_frac() - 1.0 / 3.0) < 1e-6, "0/越界必须回退默认"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
