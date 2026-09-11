# -*- coding: utf-8 -*-
"""[§87 契约 2026-09-11 / 决策 P28-A] 止损触发判定 `classify()` 的**单位契约**。

为什么单独立约：本审计第一版把 DB 里的 `trough_pnl_pct`（**分数**，−0.018 = −1.8%）
直接当成百分数与 `SL 距离(%)` 比较 ⇒ 全部判成"未触及"（假结论）。
单位错一次，结论就反一次 —— 用测试把语义钉死。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def z254():
    spec = importlib.util.spec_from_file_location(
        "z254", ROOT / "_audit_ml/Z254_long_sl_chain_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_untouched_when_mae_shallower_than_sl(z254):
    """MAE −1.41% vs SL 6.52% ⇒ 未触及（long 层实测形态）。"""
    assert z254.classify(-1.41, 6.52, False) == "未触及"


def test_touched_but_not_executed(z254):
    """MAE −6.8% vs SL 6.52%（在 0.5pt 缓冲内）⇒ 触及未执行。"""
    assert z254.classify(-6.8, 6.52, False) == "触及未执行"


def test_beyond_sl_not_executed(z254):
    """MAE 明显深于 SL（超出缓冲）⇒ 超出SL未执行（执行链缺陷的直接证据）。"""
    assert z254.classify(-9.0, 6.52, False) == "超出SL未执行"


def test_normal_sl_exit(z254):
    """走了 sl 通道 ⇒ 正常，不再判触发情况。"""
    assert z254.classify(-9.0, 6.52, True) == "正常(sl)"


def test_no_sl_declared(z254):
    """没有 SL 距离 ⇒ 无SL（不硬判未触及）。"""
    assert z254.classify(-1.0, 0.0, False) == "无SL"


def test_unit_contract_fraction_vs_percent(z254):
    """**单位契约**：入参必须是百分数；把分数直接传进来会得到相反结论（第一版的错）。"""
    assert z254.classify(-0.0141, 6.52, False) == "未触及"      # 传分数 → "未触及"（假）
    assert z254.classify(-1.41, 6.52, False) == "未触及"        # 传百分数 → 同样未触及（本例巧合同结论）
    # 但深于 SL 的情形会分叉：分数 0.09 其实代表 −9%
    assert z254.classify(-0.09, 6.52, False) == "未触及"        # 分数误传 ⇒ 假"未触及"
    assert z254.classify(-9.0, 6.52, False) == "超出SL未执行"    # 正确单位 ⇒ 真缺陷
