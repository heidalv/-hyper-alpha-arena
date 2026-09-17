# -*- coding: utf-8 -*-
"""[调研轮39 2026-09-17] 空单规模乘子（做空第 2 刀）契约测试。

## 依据

mid 空单 n=38 净 −68.60、均 **−1.81**、胜率 **26%**（多单 −0.58 / 47%）；
空单逆行 **+0.98%** vs 顺行 **0.22%**（4.5 倍）⇒ 同等规模下空单更吃亏；
空头分位曲线（位置闸拦下 420 行样本）**无正区间** ⇒ 除"来源/位置"两刀外再缩规模。

## 锁定语义

1. 部署值 `MIDLONG_SHORT_SIZE_MULT=0.5`；
2. 应用点：`decision_core.pipeline` 收口（`adjustments.size_multiplier` × 本值），**仅 action=sell**；
3. 回滚位：>=1 或 0 ⇒ 不缩（条件必须是 `0 < v < 1` 才生效）；
4. 接线护栏：源码里必须同时出现本键与 `short_size_mult` 记账字段。

> 说明：本文件锁"部署值 + 接线 + 回滚条件"；**行为级证据**来自生产日志
> `[ShortRiskShape] … 空单规模 ×0.50`（下一次空单提案时打印）。
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env():
    from dotenv import load_dotenv

    load_dotenv(str(ROOT / ".env"), override=False)
    return os.environ


def test_deployed_value():
    e = _load_env()
    assert float(e.get("MIDLONG_SHORT_SIZE_MULT", "1")) == pytest.approx(0.5)


def test_wired_into_pipeline_choke_point():
    from backend.services.decision_core import pipeline as pl

    src = inspect.getsource(pl)
    assert "MIDLONG_SHORT_SIZE_MULT" in src, "空单规模乘子未接线"
    assert 'adjustments["short_size_mult"]' in src, "缩规模未记账（不可观测）"
    assert '[ShortRiskShape]' in src, "缩规模无日志（不可验证）"
    i = src.index("MIDLONG_SHORT_SIZE_MULT")
    window = src[max(0, i - 700):i + 900]
    assert 'action in ("sell",)' in window, "必须只作用于做空"
    assert "0.0 < _sm_cfg < 1.0" in window, "回滚条件必须是 0<v<1 才生效（>=1/0 = 不缩）"


def test_rollback_semantics_documented_in_env():
    """回滚位必须在 .env 注释里写明（注释在本键**之前**），避免"改了不知道怎么办"。"""
    txt = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
    i = txt.find("MIDLONG_SHORT_SIZE_MULT")
    assert i >= 0
    ctx = txt[max(0, i - 700):i + 200]
    assert ("回滚" in ctx) or ("不缩" in ctx), ctx[-260:]
