# -*- coding: utf-8 -*-
"""轮111 测量记录（2026-09-19）—— 已被轮114 撤回接线，保留数据与算式。

## 状态

轮111 依据下表把"中线同币冷却"实现为 `midlong_executor` 里的一道新闸
（`MIDLONG_MID_REENTRY_COOLDOWN_SEC`）。**轮114 已撤回该接线**：它重复了既有的
`reentry_cooldown.reopen_blocked()`（tier 隔离 + 连亏倍率 + close_reason 感知），
且位置更靠前、会挡住既有模块更具体的审计原因。
现在窗口由既有配置 `TIER_MID_COOLDOWN_SEC=7200` 承担（归属断言见
`test_cooldown_ownership_20260919.py`）。本文件只保留**测量本身**，因为它是
"2h" 这个数值的唯一出处，且算式可复算。

## 依据（同一口径：opened_at 近 30 天、已平仓、entry/close 有效 = **158 笔**）

    全样本                均值 −0.119%   胜率 0.468
    0–2h 重开档  n=40     均值 **−0.389%**  胜率 **0.375**   ← 显著最差
    2–6h         n=29     −0.237%   0.517
    6–12h        n=22     +0.051%   0.455
    12–24h       n=13     −0.126%   0.462
    24h+         n=44     −0.080%   0.455
    首次(无前次)  n=10     **+0.774%**  胜率 **0.800**   ← 最好
    去掉 0–2h 档后 n=118  均值 **−0.027%**  胜率 0.500

⇒ ≈30 天避免亏损 $62.8 + 省手续费 $23.8 ≈ **+$87/30 天**，并少 40 笔换手。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_measurement_is_still_the_basis_of_the_window():
    """2h 这个数值必须只由本表推出 —— 数值改了要回来改这里。"""
    from backend.services.reentry_cooldown import _get_cooldown_sec
    assert _get_cooldown_sec("mid") == 7200, "窗口与本表 0–2h 档不一致"


def test_arithmetic_selfcheck():
    """算式自证：与 reports 同一口径（158 笔，去掉 0–2h 档）。"""
    n_all, mean_all = 158, -0.119
    n_cold, mean_cold = 40, -0.389
    n_rest = n_all - n_cold
    mean_rest = (n_all * mean_all - n_cold * mean_cold) / n_rest
    assert n_rest == 118
    assert mean_rest == pytest.approx(-0.027, abs=0.002), mean_rest
    assert mean_rest > mean_all
    # 经济量级：少亏 + 省手续费
    ntl = 433.3
    saved = n_cold * (mean_rest - mean_cold) / 100.0 * ntl
    assert saved == pytest.approx(62.8, abs=1.0), saved
    assert saved + n_cold * 0.594 == pytest.approx(86.6, abs=1.5)


def test_first_entry_bucket_is_never_blocked():
    """首次开仓是实测最好一档（+0.774%/胜率 0.800）⇒ 冷却不得拦"无前次"。

    既有 `reentry_cooldown.reopen_blocked` 的两条通道（内存 `_state` + DB 耐久）
    都以"存在**已平仓**记录"为前提：内存无记录时回落的 `_durable_reopen_blocked`
    只查 `status == "closed"` 的行 ⇒ 从未平过仓的币种永远不会被冷却拦下。
    此处钉住该前提，防止将来被改成"按 symbol 无条件冷却"。
    """
    rc = open(os.path.join(_ROOT, "backend/services/reentry_cooldown.py"), encoding="utf-8").read()
    assert "if not data:" in rc and "_durable_reopen_blocked(" in rc, "内存无记录必须回落 DB 耐久通道"
    i = rc.index("def _durable_reopen_blocked(")
    seg = rc[i:i + 4000]
    assert 'PaperPosition.status == "closed"' in seg, "耐久通道必须以「已平仓」为前提"
