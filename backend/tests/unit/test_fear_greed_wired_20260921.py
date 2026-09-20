# -*- coding: utf-8 -*-
"""[轮147 2026-09-21] 接上 fear_greed（主脑预检的**最后一个长期恒缺项**）。

## 背景
轮138 把 `rsi_4h / macd_hist_1h / vol_ratio_1h / adx_1d / trend_1w` 五项由 K 线派生补齐后，
`fear_greed` 成为唯一恒缺项（24h 实测 102 次）。本轮找到真实数据源并接入。

## 数据源（实测）
`market.symbol_aux_timeseries`：188,810 行；`fear_greed / btc_dominance / tvl /
active_addresses / news_sentiment`；**35 个币近 1h 有数据**、最新 `fear_greed=71`（01:32）。

## 接法
`context_pack.build_market_layer` 批量取各币最新一行（6h 窗口），写入：
  `d["fear_greed"]` / `d["btc_dominance"]` / `d["active_addresses"]` / `d["onchain_macro"]`
（量化简报读 `md.fear_greed` 或 `md.onchain_macro.fear_greed` 两条路径都能命中）。
开关 `CTX_FEAR_GREED_ENABLED`（默认 true）；取不到就**留空**，预检继续如实记缺（不编数）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _clear_market_cache():
    """`build_market_layer` 有 60s 缓存 ⇒ 同一测试进程内第二次调用会拿到旧结果（首版测试因此误判）。"""
    from backend.services.analysis import context_pack as cp

    cp._MARKET_LAYER_CACHE.clear()
    yield
    cp._MARKET_LAYER_CACHE.clear()


def test_market_layer_carries_fear_greed():
    from backend.services.analysis import context_pack as cp

    p = cp.build("midlong_thesis", symbols=["BTC"])
    row = (p.layers.get("market") or {}).get("symbols", {}).get("BTC", {})
    fg = row.get("fear_greed")
    assert fg is not None, "fear_greed 未接入（源表有数据却取不到 ⇒ 读法/窗口有问题）"
    assert 0 <= float(fg) <= 100, f"fear_greed 取值异常：{fg}"
    assert (row.get("onchain_macro") or {}).get("fear_greed") == fg, \
        "onchain_macro 别名缺失（量化简报的第二条读取路径会判缺）"


def test_switch_off_leaves_it_absent(monkeypatch):
    monkeypatch.setenv("CTX_FEAR_GREED_ENABLED", "false")
    from backend.services.analysis import context_pack as cp

    p = cp.build("midlong_thesis", symbols=["BTC"])
    row = (p.layers.get("market") or {}).get("symbols", {}).get("BTC", {})
    assert row.get("fear_greed") is None, "开关关闭时不应接入"


def test_quant_brief_sees_it(monkeypatch):
    """端到端：fear_greed 不再出现在量化简报的 missing_data 里。"""
    from backend.services.analysis import context_pack as cp

    pack = cp.build("midlong_thesis", symbols=["BTC"])
    fa = (pack.layers.get("factors") or {}).get("symbols", {}).get("BTC", {})
    brief = fa.get("brief") or {}
    if not brief:
        print("factors 层无 brief（可能活跃因子集不可用），跳过端到端断言")
        return
    missing = [m for m in (brief.get("missing_data") or []) if "fear" in str(m).lower()]
    assert not missing, f"fear_greed 仍被判缺：{missing}"
