# -*- coding: utf-8 -*-
"""[2026-09-28 用户指令「稍微跌一点就全崩了」] mid 追踪止损的**耐跌性**回归锁。

## 现场（当日实测，账户 14）
- AAVE #4834：峰值 ROI +1.63% → `exit_policy:trailing_callback` 平在 **−1.14%**（亏 −12.49）
- ATOM #4835：峰值 ROI +1.37% → 同通道平在 **−1.38%**（亏 −15.85）
- 原因：mid 出场策略 `trailing_activation_pct=1.0 + trailing_callback_pct=2.5` ⇒
  **任何峰值 <2.5% 的赢单，只要回调超过 (峰值−2.5%)，必然被砍成亏损**。
- 修复：`EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT=2.5`（≥ 首档止盈 2.5%）⇒
  追踪只在真正盈利后启动，此后回调触发也最多回到保本附近；盈利兑现交给
  `tp_stages 2.5/4.0/6.0`，止损仍是 3% 硬线 + min_roi 时间兜底。

## 本文件锁什么
① 新默认下，峰值 1.6% 的仓再回调**不会**被 trailing 砍成亏损（旧参数下会）；
② 峰值越过激活线后回调仍按 callback 保护（语义保留）；
③ 开关回滚（删掉 env 行）⇒ 恢复 1.0 的旧行为（测试用 monkeypatch 验证旧行为可复现）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.exit import exit_policy as EP


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT", raising=False)
    monkeypatch.delenv("EXIT_POLICY_MID_TRAILING_CALLBACK_PCT", raising=False)
    yield


def _snap(cur, entry, peak, side="long", sl_price=None):
    return SimpleNamespace(
        current=cur, entry=entry, side=side,
        peak_roi_pct=peak, structural_stop_price=None,
        sl_price=sl_price, tp_price=None,
        elapsed_sec=3600, roi_pct=None,
        computed_roi_pct=lambda: (cur - entry) / entry * 100,
    )


def test_new_param_survives_small_dip_without_loss_exit(monkeypatch):
    """峰值 1.6% 的赢单回调到 −1.1%：新参数下**不得** trailing 平仓（保住持仓）。"""
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT", "2.5")
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_CALLBACK_PCT", "2.5")
    pol = EP.ExitPolicy.for_lane("mid")
    # entry=100, 曾到 101.6（峰值+1.6%），现价 98.9（−1.1%）且无硬 SL（距离允许）
    v = EP.evaluate(pol, _snap(cur=98.9, entry=100.0, peak=1.6))
    assert v.action != "close" or "trailing" not in str(v.reason), \
        f"小回调仍被 trailing 砍掉: action={v.action} reason={v.reason}"


def test_old_param_would_have_cut_it(monkeypatch):
    """同一快照在旧参数（activation 1.0）下确实会被 trailing 砍 —— 证明本测试真的咬得住。"""
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT", "1.0")
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_CALLBACK_PCT", "2.5")
    pol = EP.ExitPolicy.for_lane("mid")
    v = EP.evaluate(pol, _snap(cur=98.9, entry=100.0, peak=1.6))
    assert v.action == "close" and "trailing" in str(v.reason), \
        f"旧参数应复现 loss-exit（回归锁失效）: {v.action}/{v.reason}"


def test_trailing_still_protects_after_real_profit(monkeypatch):
    """峰值 4.5%（越过激活线）后回调：仍按 callback 保护（语义保留，不因新参数变成裸奔）。"""
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT", "2.5")
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_CALLBACK_PCT", "2.5")
    pol = EP.ExitPolicy.for_lane("mid")
    # entry=100 峰值 104.5 → 回调到 101.5（峰值−3% > callback 2.5%）→ 应触发 trailing
    v = EP.evaluate(pol, _snap(cur=101.5, entry=100.0, peak=4.5))
    assert v.action == "close" and "trailing" in str(v.reason), \
        f"真正盈利后的保护消失了: {v.action}/{v.reason}"


def test_env_value_flows_into_policy(monkeypatch):
    monkeypatch.setenv("EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT", "2.5")
    pol = EP.ExitPolicy.for_lane("mid")
    assert pol.trailing_activation_pct == pytest.approx(2.5)
    monkeypatch.delenv("EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT")
    pol2 = EP.ExitPolicy.for_lane("mid")
    assert pol2.trailing_activation_pct == pytest.approx(1.0)   # 车道默认（回滚位）
