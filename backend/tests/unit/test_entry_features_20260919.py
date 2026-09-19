# -*- coding: utf-8 -*-
"""轮112 入场特征留档回归测试（2026-09-19）。

## 为什么要有这个模块

轮110/111 两次想给中线做"入场质量门槛"，都被**特征缺失**挡住：
`open_metadata` 没有 ATR/波动率；regime / 冷却间隔 / 置信度散在别的表，
且只有 39%（61/158）的仓位连得上 thesis。

本模块把入场那一刻的尺度落到 `open_metadata.entry_features`，纯观测、失败不影响开仓。

## 顺带的价值（实测立刻可见）

    BTC  ATR(1h)=0.504%  → 止损 1.5% 覆盖 2.98×
    XRP  ATR(1h)=1.135%  → 1.32×
    UNI  ATR(1h)=2.403%  → **0.62×**（止损落在**一小时**噪音带之内）

但注意：UNI 恰恰是近 30 天最赚的币（8 笔 +99.59）—— 所以"止损落在噪音带内"
这个假设**没有被数据支持**（见报告 §十），本模块的价值是让下一轮归因不必再事后重算。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.analysis import entry_features as EF  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# ① 只有真实取到值才写键（不编造）
# ══════════════════════════════════════════════════════════════════════

def test_snapshot_uses_only_available_values(monkeypatch):
    monkeypatch.setattr(EF, "_atr_pct", lambda s, tf, **k: {"1h": 0.5, "4h": 1.2}.get(tf))
    snap = EF.entry_feature_snapshot("BTC", tier="mid", sl_pct=0.015)
    assert snap["atr_1h_pct"] == 0.5 and snap["atr_4h_pct"] == 1.2
    assert "atr_1d_pct" not in snap, "取不到就不写键（缺项就是缺项）"


def test_noise_cover_ratio_math(monkeypatch):
    monkeypatch.setattr(EF, "_atr_pct", lambda s, tf, **k: 0.5 if tf == "1h" else None)
    snap = EF.entry_feature_snapshot("BTC", sl_pct=0.015)      # 小数形态 0.015 = 1.5%
    assert snap["sl_pct"] == 1.5
    assert snap["noise_cover_x"] == pytest.approx(3.0)


def test_sl_pct_accepts_percent_form(monkeypatch):
    """有的调用方传 1.5（已是百分数），有的传 0.015 —— 两者都要归一成 1.5。"""
    monkeypatch.setattr(EF, "_atr_pct", lambda s, tf, **k: 0.5 if tf == "1h" else None)
    assert EF.entry_feature_snapshot("BTC", sl_pct=1.5)["sl_pct"] == 1.5
    assert EF.entry_feature_snapshot("BTC", sl_pct=0.015)["sl_pct"] == 1.5


def test_regime_and_price_from_market_summary(monkeypatch):
    monkeypatch.setattr(EF, "_atr_pct", lambda s, tf, **k: None)
    snap = EF.entry_feature_snapshot(
        "BTC", market_summary={"BTC": {"regime": "up", "current_price": 81150.0}})
    assert snap["regime"] == "up" and snap["price"] == 81150.0


def test_atr_failure_is_swallowed(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("kline down")
    monkeypatch.setattr(EF, "_atr_pct", _boom)
    snap = EF.entry_feature_snapshot("BTC", sl_pct=0.015)
    assert snap["symbol"] == "BTC" and "atr_1h_pct" not in snap


# ══════════════════════════════════════════════════════════════════════
# ② 接线：fail-open，且不影响开仓
# ══════════════════════════════════════════════════════════════════════

def test_wired_into_midlong_extra_kwargs():
    src = open(os.path.join(_ROOT, "backend/services/full_auto/midlong_helpers.py"),
               encoding="utf-8").read()
    i = src.index("入场时特征留档")
    block = src[i: i + 900]
    assert 'entry_feature_snapshot' in block
    assert '_extra_kwargs["entry_features"]' in block
    assert "except Exception as _ef_err" in block, "留档异常必须吞掉，不得影响开仓"


def test_module_is_read_only_by_contract():
    """模块 docstring 必须写明"纯观测"；且源码里不得出现写库/下单调用。"""
    src = open(os.path.join(_ROOT, "backend/services/analysis/entry_features.py"),
               encoding="utf-8").read()
    assert "纯观测" in src
    for forbidden in ("place_order", "close_position", "db.commit", "db.add(", "UPDATE "):
        assert forbidden not in src, forbidden


# ══════════════════════════════════════════════════════════════════════
# ③ 现场取数（需要 kline 服务；取不到就跳过，不误报）
# ══════════════════════════════════════════════════════════════════════

def test_live_snapshot_has_atr_and_gap():
    snap = EF.entry_feature_snapshot("BTC", tier="mid", sl_pct=0.015)
    if "atr_1h_pct" not in snap:
        pytest.skip("kline 服务不可用（非失败）")
    assert snap["atr_1h_pct"] > 0
    assert snap["noise_cover_x"] == pytest.approx(snap["sl_pct"] / snap["atr_1h_pct"], rel=1e-3)
