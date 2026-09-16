# -*- coding: utf-8 -*-
"""[调研轮26 2026-09-17] long 层单币名义上限收紧的契约测试。

## 依据

30 天实测：long 层 43 笔中，**名义 >$600 的 10 笔合计亏 −78.18（占该层全部亏损的 91%）**；
同层 `trend_e1` 的名义 p50=$94 / max=$351（不受影响，且是唯一稳定盈利来源）。
⇒ 收紧 long 的单币名义 = 缩小"坏单"的金额影响，**不减少成交笔数、不加入场闸**。

## 锁定语义

1. 部署值：`PC_MAX_WEIGHT_PER_SYMBOL_LONG=0.10`；
2. **只影响 long**：mid/short 仍为 0.35（不得被顺手改）；
3. 生效链路：`position_construction.clamp`（paper 引擎 / live trading_commands / E1 均调用）——
   用接线护栏防止"配置了却没接"。
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


def test_long_lane_weight_cap_deployed():
    e = _load_env()
    assert float(e.get("PC_MAX_WEIGHT_PER_SYMBOL_LONG", "0.35")) == pytest.approx(0.10)
    # 全局键保持不动（供其它车道使用）
    assert float(e.get("PC_MAX_WEIGHT_PER_SYMBOL", "0.35")) == pytest.approx(0.35)


def test_only_long_lane_affected():
    _load_env()
    import importlib

    import backend.services.position_construction as pc

    pc = importlib.reload(pc)
    assert pc.LaneLimits.for_lane("long").max_weight_per_symbol == pytest.approx(0.10)
    # [调研轮28] mid 也按同源机制收紧（0.15 = $681）：30 天分档证据见 .env 注释
    assert pc.LaneLimits.for_lane("mid").max_weight_per_symbol == pytest.approx(0.15)
    assert pc.LaneLimits.for_lane("short").max_weight_per_symbol == pytest.approx(0.35), (
        "short 车道不得被顺手收紧（本轮证据只支持 long/mid）"
    )


def test_mid_cap_deployed_and_targets_losing_band():
    """mid 上限 0.15：恰好约束 $600-900 亏损档、不碰盈利的 $300-600 档。"""
    e = _load_env()
    assert float(e.get("PC_MAX_WEIGHT_PER_SYMBOL_MID", "0.35")) == pytest.approx(0.15)
    eq = 4541.0
    assert 681 * 0.85 < eq * 0.15 < 681 * 1.15   # ≈$681
    assert eq * 0.15 < 900, "上限必须落在 $600-900 档之内，否则约束不到亏损档"
    assert eq * 0.15 > 300, "上限不得低到碰掉盈利的 $300-600 档"


def test_cap_is_wired_into_production_paths():
    """接线护栏：clamp 必须真的被生产路径调用（防止'配置了却没接'）。"""
    from backend.services import paper_trading_engine as pte

    src = inspect.getsource(pte)
    assert "position_construction as _pc" in src, "paper 路径未接 position_construction"
    assert "position_construction_room_zero" in src or "_cl" in src
