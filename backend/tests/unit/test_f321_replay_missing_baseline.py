# -*- coding: utf-8 -*-
"""[F321 2026-09-17] 回放的波动基准缺失必须降级，不得 KeyError。

现场（本轮）：做「在位宇宙 + 高可达候选」的宇宙对照时，`replay_portfolio` 在
`portfolio_replay.py:278` 抛 `KeyError: 'UNI'`：

    sigma = (max(0.0, vol_cur / vol_baseline[s] - 1.0)
             if vol_baseline[s] > 0 else 0.0)      # ← 字典直取

`vol_baseline` 只含**车道注册表锚定过**的标的（当前 5 个），于是任何未锚定的标的
（UNI/BTC/ETH/AVAX…）都会让整个回放崩掉 ⇒ **宇宙对照/换标的实验根本做不了**。

这与 F302 修的是**同一类缺陷**：把"当前宇宙"硬编码进通用工具。
F302 修的是采集覆盖（`market_data_symbol_config`），这里是回放引擎。

契约：
  1. 未锚定基准的标的 ⇒ σ 降级为 0（不臆造基准），**且回放继续**；
  2. 缺哪些标的必须**显式返回**（`missing_vol_baseline`），不能静默——
     σ=0 时 `vol_pause` 闸永不触发，调用方必须知道这件事；
  3. 有基准的标的读数不受影响（不改变既有数值）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402


def _fake_data(syms, n=400):
    """造一段最小可回放数据（盘口 + 成交），不依赖 DB。"""
    out = {}
    for s in syms:
        ots = (np.arange(n) * 15000).astype(np.int64) + 1_700_000_000_000
        mid = 100.0 + np.cumsum(np.random.default_rng(0).normal(0, 0.01, n))
        out[s] = {
            "ots": ots,
            "bb": mid - 0.005, "ba": mid + 0.005,
            "tts": ots.copy(),
            "lo": mid - 0.01, "hi": mid + 0.01,
            "sv": np.abs(np.random.default_rng(1).normal(10, 2, n)),
            "bv": np.abs(np.random.default_rng(2).normal(10, 2, n)),
            "tmk": ots + 1000,
        }
    return out


def test_missing_baseline_does_not_raise():
    """**核心契约**：未锚定基准的标的不得让回放崩溃。"""
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
    from backend.services.market_maker.portfolio_replay import replay_portfolio

    syms = ["ZEC", "UNI"]          # UNI 故意不给基准
    data = _fake_data(syms)
    r = replay_portfolio(
        syms, venue="asterdex", equity=300.0,
        params=QuoteParams(w_base_bp=30.0), limits=LaneRiskLimits(),
        fill_notional=300.0, data=data,
        vol_baseline={"ZEC": 8.55},        # 只有 ZEC 有
    )
    assert isinstance(r, dict)
    assert r.get("ok") is not False


def test_missing_baseline_is_reported():
    """缺基准必须显式返回——σ=0 时 vol_pause 闸永不触发，调用方必须知道。"""
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
    from backend.services.market_maker.portfolio_replay import replay_portfolio

    syms = ["ZEC", "UNI", "BTC"]
    data = _fake_data(syms)
    r = replay_portfolio(
        syms, venue="asterdex", equity=300.0,
        params=QuoteParams(w_base_bp=30.0), limits=LaneRiskLimits(),
        fill_notional=300.0, data=data, vol_baseline={"ZEC": 8.55},
    )
    miss = r.get("missing_vol_baseline")
    assert miss is not None, "必须返回 missing_vol_baseline 字段"
    assert set(miss) == {"UNI", "BTC"}, miss


def test_no_missing_baseline_when_all_anchored():
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
    from backend.services.market_maker.portfolio_replay import replay_portfolio

    syms = ["ZEC", "UNI"]
    data = _fake_data(syms)
    r = replay_portfolio(
        syms, venue="asterdex", equity=300.0,
        params=QuoteParams(w_base_bp=30.0), limits=LaneRiskLimits(),
        fill_notional=300.0, data=data,
        vol_baseline={"ZEC": 8.55, "UNI": 2.0},
    )
    assert r.get("missing_vol_baseline") == []


def test_empty_baseline_dict_does_not_raise():
    """显式传空字典 ⇒ 全部降级为 σ=0，**且不回退到现算基准**。

    这是 F108c 陷阱的入口：`if vol_baseline` 把 `{}` 当 falsy ⇒ 静默现算基准
    ⇒ σ 跟着所载窗口走，与实盘（注册表锚定）不可比。
    传 `{}` 的语义必须是"我没有基准"，不是"请帮我现算"。
    """
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
    from backend.services.market_maker.portfolio_replay import replay_portfolio

    syms = ["ZEC", "UNI"]
    data = _fake_data(syms)
    r = replay_portfolio(
        syms, venue="asterdex", equity=300.0,
        params=QuoteParams(w_base_bp=30.0), limits=LaneRiskLimits(),
        fill_notional=300.0, data=data, vol_baseline={},
    )
    assert set(r.get("missing_vol_baseline") or []) == {"ZEC", "UNI"}
    assert r.get("vol_baseline_computed") is False, "显式传入（含空字典）不得回退现算"


def test_none_baseline_falls_back_and_says_so():
    """传 None（未提供）才允许现算，并且必须标记出来。"""
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
    from backend.services.market_maker.portfolio_replay import replay_portfolio

    syms = ["ZEC", "UNI"]
    data = _fake_data(syms)
    r = replay_portfolio(
        syms, venue="asterdex", equity=300.0,
        params=QuoteParams(w_base_bp=30.0), limits=LaneRiskLimits(),
        fill_notional=300.0, data=data, vol_baseline=None,
    )
    assert r.get("vol_baseline_computed") is True
    # 现算后每个标的都有基准（波动>0），所以不应报缺失
    assert (r.get("missing_vol_baseline") or []) == []


def test_zero_baseline_treated_as_missing_not_anchored():
    """基准为 0 等同于"未锚定"（既有语义：`vol_baseline_bp<=0` ⇒ σ=0）。"""
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
    from backend.services.market_maker.portfolio_replay import replay_portfolio

    syms = ["ZEC", "UNI"]
    data = _fake_data(syms)
    r = replay_portfolio(
        syms, venue="asterdex", equity=300.0,
        params=QuoteParams(w_base_bp=30.0), limits=LaneRiskLimits(),
        fill_notional=300.0, data=data, vol_baseline={"ZEC": 0.0, "UNI": 2.0},
    )
    assert r.get("missing_vol_baseline") == ["ZEC"]


def test_source_has_no_dict_direct_index_on_vol_baseline():
    """结构性断言：源码里不得再出现 `vol_baseline[s]` 的字典直取。"""
    import inspect

    from backend.services.market_maker import portfolio_replay as pr

    src = inspect.getsource(pr)
    assert "vol_baseline[s]" not in src, (
        "不得再对 vol_baseline 做字典直取——未锚定的标的会 KeyError，"
        "使宇宙对照/换标的实验无法进行（F321 现场）")
    assert "vol_baseline.get(s)" in src
