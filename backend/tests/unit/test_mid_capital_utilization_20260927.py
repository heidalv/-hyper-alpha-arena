# -*- coding: utf-8 -*-
"""[2026-09-27 R4 用户指令] 日内(mid)车道**资金使用率**回归锁。

## 用户口径（原话）
> 日内交易一直在亏损……并且日内交易的资金使用率太低了，杠杆固定的，但是可以提高保证金，
> 这样不至于一直在空转，不要怕亏损，现在是模拟交易，还是在攒交易数据，你这收的这么紧，完全是错误的

## 实测（改动前，账户 14 = 小资金 paper）
- 权益 `total_equity = 4643.31`，冻结保证金 `394.51` ⇒ **保证金使用率 8.5%**（≈91.5% 空转）；
- mid 车道单笔名义额 09-26/09-27 仅 **$90~135**；09-21 缩仓前是 $340（均）。

## 机制（代码实证，`full_auto/proposal_execution.py:340-414`）
六层同维度缩仓把 `size_multiplier` 压到 ~0.0014 ⇒ 估算名义 = `base × 0.0014`
（`base = equity × MIDLONG_RISK_PCT / sl_pct`）⇒ 名义过小 ⇒ 走 `[SizeFloor] PROBE-CLAMP`
被**抬到 `MIDLONG_MIN_PROBE_NOTIONAL_USD` 地板**。所以"实际每一笔多大"由**地板**决定，
再由 `PC_MAX_WEIGHT_PER_SYMBOL_MID` 封顶。

## 本文件锁什么
1. 四个"提高保证金"的键必须是**放大后**的部署值（防止又被静默回退成 60/0.15/0.08/0.01）；
2. **杠杆一个字都没动**（用户明确"杠杆固定的"）：币种档位表与 env 覆盖值都不许被本改动波及。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]


def _env() -> dict:
    load_dotenv(str(ROOT / ".env"), override=True)
    return dict(os.environ)


def test_probe_notional_floor_raised():
    """缩仓链地板名义：60 → 400（这是实际决定 mid 单笔大小的那个键）。"""
    v = float(_env().get("MIDLONG_MIN_PROBE_NOTIONAL_USD", "0"))
    assert v >= 400, f"mid 最小试探名义 {v} 太低 ⇒ 又会退化成 $60 空转单"


def test_mid_weight_cap_raised():
    """mid 单币名义上限：0.15 → 0.30 权益（上限不抬会把地板抬起来的名义再压回去）。"""
    v = float(_env().get("PC_MAX_WEIGHT_PER_SYMBOL_MID", "0"))
    assert v >= 0.30, f"mid 单币权重上限 {v} 太低"


def test_mid_tier_margin_and_risk_budget_raised():
    """mid 保证金意图 0.08 → 0.16；风险预算基准 0.01 → 0.02。"""
    e = _env()
    assert float(e.get("MIDLONG_TIER_MARGIN_PCT_MID", "0")) >= 0.16
    assert float(e.get("MIDLONG_RISK_PCT", "0")) >= 0.02


def test_leverage_is_untouched():
    """用户明确"杠杆固定的" ⇒ 本改动不得提高任何币种杠杆档位。"""
    from backend.services.leverage_authority import DEFAULT_SYMBOL_LEVERAGE, symbol_leverage

    # 内置档位表就是"固定杠杆"的真源，值必须保持 BTC/ETH 5x、其余 4x
    assert DEFAULT_SYMBOL_LEVERAGE.get("BTC") == pytest.approx(5.0)
    assert DEFAULT_SYMBOL_LEVERAGE.get("ETH") == pytest.approx(5.0)
    assert DEFAULT_SYMBOL_LEVERAGE.get("SOL") == pytest.approx(4.0)
    assert symbol_leverage("BTC") <= 5.0, "BTC 杠杆被本改动抬高了"
    assert symbol_leverage("UNI") <= 5.0, "非主流币杠杆被本改动抬高了"


def test_expected_notional_arithmetic():
    """按代码口径算一遍预期：地板 $400 ⇒ 单笔名义 ≥$400、保证金 ≈$100（4x）。"""
    e = _env()
    floor = float(e.get("MIDLONG_MIN_PROBE_NOTIONAL_USD", "0"))
    lev = 4.0   # 非 BTC/ETH 的币种档位；BTC/ETH 为 5x
    assert floor >= 400
    margin = floor / lev
    assert margin >= 100, f"单笔保证金预期 {margin:.0f} 仍偏低"
