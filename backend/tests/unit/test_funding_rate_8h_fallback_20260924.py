# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R29] 资金费 `rate_8h` 兜底的**单位归一防回归**测试。

背景（R28 实测）：权威口径 `funding_universe.rate_8h = rate × (8/hrs)`，
`FUNDING_INTERVAL_HOURS` 里 hyperliquid=1.0、其余=8.0。
但 3 个消费方各自带 `except: return float(rate)` 的直通兜底
（`anomaly_agent.py:305`、`context_pack.py:594`、`e5_2_funding_shock.py:63`）——
一旦 import 失败，**hyperliquid 的 1h 费率会被当 8h 用（高估 8×）且无日志**，
而下游阈值是绝对值判据（如 `abs(r8) >= 0.0005`），会直接改变结论。
R29 已把兜底改为同样归一。本测试锁住这一点，防止回归。

注：`arbitrage/opportunity_scanner.py:199/206` 的 `return float(rate)` 属**无关函数**，
不在本测试范围内（勿误改）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

SITES = {
    "anomaly_agent": "backend/services/agents/anomaly_agent.py",
    "context_pack": "backend/services/analysis/context_pack.py",
    "e5_2_funding_shock": "backend/services/strategies/event/e5_2_funding_shock.py",
}


@pytest.mark.parametrize("name,rel", list(SITES.items()))
def test_fallback_block_normalizes_to_8h(name, rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    i = src.find("def rate_8h")
    assert i >= 0, f"{name}: 未找到 def rate_8h"
    block = src[i:i + 500]
    assert "8.0 / _hrs" in block, f"{name}: 兜底未做 8h 归一（会高估 hyperliquid 8×）"
    assert '"hyperliquid": 1.0' in block, f"{name}: 兜底缺少周期表"
    # 不得出现"只直通"的旧写法
    assert "return float(rate)\n" not in block.replace(
        "return float(rate) * (8.0 / _hrs) if _hrs > 0 else float(rate)\n", ""
    ), f"{name}: 仍存在直通兜底"


def test_canonical_rate_8h_contract():
    """权威函数：hyperliquid 1h → ×8；其余 8h → ×1。"""
    from backend.services.events.funding_universe import (
        FUNDING_INTERVAL_HOURS,
        rate_8h,
    )

    assert FUNDING_INTERVAL_HOURS["hyperliquid"] == 1.0
    assert rate_8h("hyperliquid", 0.0001) == pytest.approx(0.0008)
    assert rate_8h("binance", 0.0001) == pytest.approx(0.0001)
    assert rate_8h("", 0.0001) == pytest.approx(0.0001)  # 未知场所按 8h


def test_inline_rule_matches_canonical():
    """兜底内联规则与权威函数对同一输入必须一致。"""
    from backend.services.events.funding_universe import rate_8h

    def inline(ex: str, rate: float) -> float:
        hrs = {"hyperliquid": 1.0}.get(str(ex or "").lower(), 8.0)
        return float(rate) * (8.0 / hrs) if hrs > 0 else float(rate)

    for ex in ("hyperliquid", "binance", "bybit", "okx", "gateio", "asterdex", "unknown", ""):
        for r in (0.0001, -0.00025, 0.0, 0.00048):
            assert inline(ex, r) == pytest.approx(rate_8h(ex, r)), (ex, r)
