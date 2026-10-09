# -*- coding: utf-8 -*-
"""[工作流②-1] 目标波动率影子版 + 杠杆×止损风险预算 护栏测试（2026-10-04）。

## 实测背景
· `VOL_TARGET_ANNUAL` 零消费者（仅 `analysis/context_pack.py:34` 白名单）；
· `trend_core.py:103-104` 的 `TREND_WEIGHTING=vol_target/TREND_VOL_TARGET=0.35` **未驱动仓位**：
  400 条已平仓实测 `Spearman(σ̂, 名义敞口)=+0.421`（应为负），名义敞口中位数恒定 $51；
· 用 **1d** 收盘算 σ̂ 会得到 2988% 这类噪声 ⇒ 必须用 4h + 截断；
· 实测持仓 `leverage=10`，`sl=8%` ⇒ 占用 **80% 保证金**（超 50% 预算）。

## 实测结果（120 条）
σ̂(4h,截断) 中位数 53.9%、范围 **15%~119%**；scale 中位数 **0.65**、范围 0.30~1.00；
`sl=8% @5x → 40%`（ok）、`@10x → 80%`（warn，超预算）。

## 本测试守护的不变量
1. **影子默认关闭**（`VOLTARGET_SHADOW=false` ⇒ 直接返回 disabled，不计算不入账）
2. σ̂ 必须**截断**（floor/cap），且数据不足**返回 None**（不许猜、不许用日线噪声）
3. σ̂ 缺失 ⇒ scale 返回 None（调用方回退）
4. 风险预算：`sl×lev` 超阈值必须 `over_budget=True`
5. 影子**绝不改变下单**（模块内不得出现下单调用）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC = (ROOT / "backend/services/vol_target_shadow.py").read_text(encoding="utf-8")


def test_shadow_defaults_off():
    assert 'VOLTARGET_SHADOW", "false"' in SRC
    assert '"enabled": False' in SRC and "VOLTARGET_SHADOW=false" in SRC


def test_sigma_is_winsorized_and_insufficient_data_returns_none():
    assert "VOLTARGET_SIGMA_FLOOR" in SRC and "VOLTARGET_SIGMA_CAP" in SRC
    assert "return max(floor, min(cap, sigma))" in SRC
    assert "return None" in SRC and "MIN_BARS" in SRC
    # 不得再依赖日线（1d）作为默认口径
    assert 'period: str = "4h"' in SRC


def test_scale_none_when_sigma_missing():
    seg = SRC.split("def vol_target_scale(")[1].split("def risk_budget_check(")[0]
    assert "if not sigma or sigma <= 0:" in seg and "return None" in seg


def test_risk_budget_flags_over_budget():
    from backend.services.vol_target_shadow import risk_budget_check

    r5 = risk_budget_check(sl_pct=0.08, leverage=5)
    r10 = risk_budget_check(sl_pct=0.08, leverage=10)
    assert abs(r5["margin_at_risk"] - 0.4) < 1e-9 and r5["over_budget"] is False
    assert abs(r10["margin_at_risk"] - 0.8) < 1e-9 and r10["over_budget"] is True


def test_shadow_module_cannot_place_orders():
    """影子模块禁止出现任何下单/改仓调用（防未来被误用）。"""
    for forbidden in ("place_order(", "open_position(", "close_position(", "set_initial_balance("):
        assert forbidden not in SRC, f"影子模块不得调用 {forbidden}"


def test_vol_target_scale_clips():
    from backend.services.vol_target_shadow import vol_target_scale

    assert vol_target_scale(0.35, target=0.35) == 1.0        # 波动等于目标 → 满仓
    assert abs(vol_target_scale(1.0, target=0.35) - 0.35) < 1e-9
    assert vol_target_scale(10.0, target=0.35) == 0.3        # 极高波 → 落到下限
    assert vol_target_scale(None) is None
