# -*- coding: utf-8 -*-
"""[调研轮19 2026-09-17] **回踩入场**契约测试（非门禁：超时必市价兜底，不丢单）。

数据依据：近 7 天 35 笔开仓（15m K 线）入场后 1h 内 80% 出现回踩（均值 +1.11%），
入场后前 4h MFE +0.24% vs MAE −1.68% ⇒ 市价成交=买局部高点；
挂 entry×(1−0.3%) 限价 74% 成交、平均改善 0.30%。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.full_auto import pullback_entry as pb  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    pb.reset()
    for k in ("MIDLONG_PULLBACK_ENTRY_ENABLED", "MIDLONG_PULLBACK_ENTRY_PCT",
              "MIDLONG_PULLBACK_ENTRY_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)
    yield
    pb.reset()


def test_first_sighting_waits():
    wait, why = pb.evaluate(key="s:DOT:mid:buy", side="buy", price=100.0, now=1000.0)
    assert wait is True and "登记回踩" in why
    assert "s:DOT:mid:buy" in pb.pending_snapshot()


def test_target_hit_proceeds_to_fill():
    pb.evaluate(key="k", side="buy", price=100.0, now=1000.0)
    wait, why = pb.evaluate(key="k", side="buy", price=99.6, now=1010.0)
    assert wait is False and "回踩到位" in why
    assert pb.pending_snapshot() == {}, "成交后必须清除挂起"


def test_timeout_falls_back_to_market():
    """最关键的一条：超时必须走原成交路径（市价兜底），**不许把单丢掉**。"""
    pb.evaluate(key="k", side="buy", price=100.0, now=1000.0)
    wait, why = pb.evaluate(key="k", side="buy", price=100.8, now=1000.0 + 1800)
    assert wait is False and "市价兜底" in why
    assert pb.pending_snapshot() == {}


def test_short_symmetric():
    wait, _ = pb.evaluate(key="k", side="sell", price=100.0, now=1000.0)
    assert wait is True
    wait2, why2 = pb.evaluate(key="k", side="sell", price=100.4, now=1010.0)
    assert wait2 is False and "回踩到位" in why2


def test_disabled_switch_is_old_behavior():
    wait, why = pb.evaluate(key="k", side="buy", price=100.0, now=1000.0, enabled=False)
    assert wait is False and why == "pullback_off"


def test_zero_pct_is_off():
    wait, why = pb.evaluate(key="k", side="buy", price=100.0, now=1000.0, pct=0.0)
    assert wait is False and why == "pullback_off"


def test_bad_price_does_not_block():
    for bad in (0, -1, None, "abc"):
        wait, why = pb.evaluate(key="k", side="buy", price=bad, now=1000.0)
        assert wait is False and why == "bad_price", (bad, wait, why)


def test_keys_are_isolated():
    pb.evaluate(key="a:DOT:mid:buy", side="buy", price=100.0, now=1000.0)
    wait, why = pb.evaluate(key="a:SOL:mid:buy", side="buy", price=100.0, now=1000.0)
    assert wait is True and "登记回踩" in why, "不同 symbol/层 不得互相影响"


def test_unrecognized_env_falls_back_to_default(monkeypatch, caplog):
    monkeypatch.setenv("MIDLONG_PULLBACK_ENTRY_ENABLED", "ture")
    cfg = pb.pullback_config()
    assert cfg["enabled"] is True, "无法识别的值必须按默认（不静默关闭）"


def test_wired_into_entry_path():
    from backend.services.full_auto import midlong_helpers as mh

    src = inspect.getsource(mh)
    assert "pullback_entry" in src and "pullback_wait" in src, "回踩入场未接线"
    idx = src.index("pullback_entry import evaluate")
    window = src[idx:idx + 2200]
    assert "return False" in window, "等待时应跳过本轮成交（信号保留）"
    assert "range_mid" in window, "区间中位未接入（自适应目标价）"


# ── [调研轮19 v2] 按区间位置自适应目标价（提高开仓准确率）──────────────
def test_good_side_uses_base_offset():
    """多头在 24h 中位之下（有利半区）⇒ 用基础 0.3% 档。"""
    wait, why = pb.evaluate(key="k", side="buy", price=99.0, range_mid=100.0, now=1000.0)
    assert wait is True
    assert "不利半区" not in why
    tgt = pb.pending_snapshot()["k"]["target"]
    assert tgt == pytest.approx(99.0 * 0.997)


def test_adverse_side_targets_range_mid():
    """多头在 24h 中位之上（不利半区，实测胜率 26%）⇒ 目标改成区间中位。"""
    wait, why = pb.evaluate(key="k", side="buy", price=101.0, range_mid=100.0, now=1000.0)
    assert wait is True and "不利半区" in why, why
    tgt = pb.pending_snapshot()["k"]["target"]
    assert tgt == pytest.approx(100.0, rel=1e-6), "目标应落在区间中位"


def test_adverse_side_target_is_capped():
    """中位离得太远时按 MAX_PCT（默认 2%）夹住，避免永远等不到。"""
    wait, _ = pb.evaluate(key="k", side="buy", price=110.0, range_mid=100.0, now=1000.0)
    assert wait is True
    tgt = pb.pending_snapshot()["k"]["target"]
    assert tgt == pytest.approx(110.0 * 0.98)   # 夹在 2%


def test_no_range_mid_falls_back_to_base():
    wait, why = pb.evaluate(key="k", side="buy", price=100.0, range_mid=None, now=1000.0)
    assert wait is True and "不利半区" not in why
    assert pb.pending_snapshot()["k"]["target"] == pytest.approx(100.0 * 0.997)


def test_short_adverse_side_is_below_mid():
    """空头在中位之下（不利半区）⇒ 等反弹到中位再成交。"""
    wait, why = pb.evaluate(key="k", side="sell", price=99.0, range_mid=100.0, now=1000.0)
    assert wait is True and "不利半区" in why, why
    assert pb.pending_snapshot()["k"]["target"] == pytest.approx(100.0, rel=1e-6)


def test_timeout_still_falls_back_with_adaptive_target():
    """自适应目标也必须保留超时市价兜底（不丢单）。"""
    pb.evaluate(key="k", side="buy", price=101.0, range_mid=100.0, now=1000.0)
    wait, why = pb.evaluate(key="k", side="buy", price=101.5, now=1000.0 + 1800)
    assert wait is False and "市价兜底" in why
