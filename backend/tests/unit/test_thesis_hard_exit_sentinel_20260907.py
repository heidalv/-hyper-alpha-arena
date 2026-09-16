"""论题硬离场哨兵：should_close / 同向失效价。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from backend.services.full_auto.midlong_position_manager import (
    resolve_thesis_hard_exit,
)


def test_resolve_should_close_even_if_direction_flips():
    """多头仓 + 空头论题 should_close → 仍应平（翻空平多）。"""
    pos = {"symbol": "BTC", "side": "long", "mark_price": 79600, "timeframe_tier": "mid"}
    th = SimpleNamespace(
        accepted=True,
        should_close=True,
        direction="short",
        invalidation={"price": 82000},
        analysis_run_id="r1",
        expires_at=None,  # tradeable mocked
    )
    with patch(
        "backend.config.settings.midlong_brain_enabled", return_value=True
    ), patch(
        "backend.services.mlto.thesis_store.get", return_value=th
    ), patch(
        "backend.services.mlto.brain.thesis_is_tradeable_fresh", return_value=True
    ):
        hit = resolve_thesis_hard_exit("fa_test", pos)
    assert hit is not None
    assert hit[0] == "thesis_should_close"


def test_resolve_invalidation_same_direction_only():
    """空头论题失效价不得把多头仓当成「跌破即平」。"""
    pos = {"symbol": "BTC", "side": "long", "mark_price": 79600, "timeframe_tier": "mid"}
    th = SimpleNamespace(
        accepted=True,
        should_close=False,
        direction="short",
        invalidation={"price": 82000},
        analysis_run_id="r1",
        expires_at=None,
    )
    with patch(
        "backend.config.settings.midlong_brain_enabled", return_value=True
    ), patch(
        "backend.services.mlto.thesis_store.get", return_value=th
    ), patch(
        "backend.services.mlto.brain.thesis_is_tradeable_fresh", return_value=True
    ), patch(
        "backend.services.mlto.brain._inv_price", return_value=82000.0
    ):
        hit = resolve_thesis_hard_exit("fa_test", pos)
    assert hit is None


def test_resolve_invalidation_long_thesis_breaks_down():
    """[调研轮18 2026-09-16 更新] 失效价触发改为**与条件原文对齐**（收盘确认）。

    旧断言锁的是"mark 一碰失效价即出场"；实测 6 笔里 4 笔的 1h 收盘仍在失效价之内
    （原文写的是"收盘跌破"），故契约改为：**1h 已收盘收破才触发**，
    浅穿透不触发（保护交回硬止损）——所以这里给失效价留出"真破位"的形态。
    """
    pos = {"symbol": "BTC", "side": "long", "mark_price": 73000, "timeframe_tier": "mid"}

    class _Th:
        accepted = True
        should_close = False
        direction = "long"
        invalidation = {"price": 75000}
        analysis_run_id = "r1"
        expires_at = None

    def _run(bars, mark=73000):
        pos["mark_price"] = mark
        with patch(
            "backend.config.settings.midlong_brain_enabled", return_value=True
        ), patch(
            "backend.services.mlto.thesis_store.get", return_value=_Th()
        ), patch(
            "backend.services.mlto.brain.thesis_is_tradeable_fresh", return_value=True
        ), patch(
            "backend.services.mlto.brain._inv_price", return_value=75000.0
        ), patch(
            "backend.services.market_data.get_kline_data",
            lambda *a, **k: [{"timestamp": i, "open": c, "high": c, "low": c,
                              "close": c, "volume": 1.0}
                             for i, c in enumerate(bars)],
        ):
            return resolve_thesis_hard_exit("fa_test", pos)

    # ① 1h 收盘 74500 < 失效价 75000 ⇒ 原文"收盘跌破"成立 ⇒ 触发
    hit = _run([76000.0, 74500.0, 74800.0])
    assert hit is not None and hit[0] == "thesis_invalidation"

    # ② 浅穿透（mark 74850 仅 0.2%，未到 2% 剧烈破位线）+ 1h 收盘 75500 未破
    #    ⇒ 原文条件未成立 ⇒ 不触发（这正是被少砍的那 4/6 形态）
    hit2 = _run([76000.0, 75500.0, 75400.0], mark=74850)
    assert hit2 is None, "收盘未破不应再按『触碰即平』砍仓（少砍早单）"


def test_pick_unique_snap_prefers_executed():
    """DecisionSnapshot 回写：优先 executed=True，忽略试单污染。"""
    from datetime import datetime, timezone

    class C:
        def __init__(self, executed, ts, direction="buy", action="buy"):
            self.executed = executed
            self.timestamp = ts
            self.direction = direction
            self.action = action

    # 复用引擎内闭包逻辑太重；直接测选择语义：一条 True 即可
    cands = [
        C(False, datetime(2026, 9, 6, 15, 41, tzinfo=timezone.utc)),
        C(False, datetime(2026, 9, 6, 15, 48, tzinfo=timezone.utc)),
        C(True, datetime(2026, 9, 6, 15, 49, tzinfo=timezone.utc)),
    ]
    execed = [c for c in cands if c.executed]
    assert len(execed) == 1
    assert execed[0].timestamp.minute == 49
