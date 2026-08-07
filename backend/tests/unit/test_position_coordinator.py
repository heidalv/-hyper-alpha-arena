# backend/tests/unit/test_position_coordinator.py
"""多周期仓位协调器(PositionCoordinator)单元测试。

覆盖核心规则:
1. 同 tier(trade_nature)反向开仓 → 拦截
2. 跨 tier 反向开仓(scalp long + trend short)→ 放行(合法对冲)
3. 统一杠杆 = max(所有现有子仓位杠杆, 新请求杠杆)
4. 净暴露 = sum(signed size)(long 正,short 负)

用 MagicMock 模拟 SQLAlchemy 查询链;不依赖真实 DB。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.services.position_coordinator import (
    CoordinationResult,
    PositionCoordinator,
)


def _make_pos(side: str, size: float, leverage: float, trade_nature: str):
    """构造一个模拟的 PaperPosition 行(显式属性,getattr 可读)。"""
    p = MagicMock()
    p.side = side
    p.size = size
    p.leverage = leverage
    p.trade_nature = trade_nature
    p.status = "open"
    return p


def _make_db(existing_positions: list) -> MagicMock:
    """构造一个 db mock,其 query(...).filter(...)...all() 返回给定列表。"""
    db = MagicMock()
    q = db.query.return_value
    q.filter.return_value.filter.return_value.filter.return_value.all.return_value = (
        existing_positions
    )
    return db


# ── 规则 1:同 tier 反向 → 拦截 ──────────────────────────────────────


def test_same_tier_opposite_direction_blocked():
    """同 trade_nature 已有 long,新开 short → 拦截。"""
    existing = [_make_pos("long", size=1.0, leverage=10, trade_nature="scalp")]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="short", order_side="sell",
        leverage=8, trade_nature="scalp",
    )
    assert res.allowed is False
    assert "same-tier direction conflict" in res.reason
    assert "scalp" in res.reason


def test_same_tier_same_direction_allowed():
    """同 trade_nature 同向(long + long)→ 放行(加仓语义)。"""
    existing = [_make_pos("long", size=1.0, leverage=10, trade_nature="scalp")]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="long", order_side="buy",
        leverage=8, trade_nature="scalp",
    )
    assert res.allowed is True


# ── 规则 2:跨 tier 反向 → 放行(对冲)──────────────────────────────


def test_cross_tier_opposite_direction_allowed():
    """scalp long + 新开 trend short → 放行(合法对冲)。"""
    existing = [_make_pos("long", size=1.0, leverage=10, trade_nature="scalp")]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="short", order_side="sell",
        leverage=12, trade_nature="trend_follow",
    )
    assert res.allowed is True
    # 摘要应包含现有 scalp long
    assert len(res.existing_sub_positions) == 1
    assert res.existing_sub_positions[0]["trade_nature"] == "scalp"


def test_cross_tier_opposite_three_way_allowed():
    """scalp long + trend long + 新开 swing short → 放行(无 swing 仓)。"""
    existing = [
        _make_pos("long", size=1.0, leverage=10, trade_nature="scalp"),
        _make_pos("long", size=2.0, leverage=12, trade_nature="trend_follow"),
    ]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="short", order_side="sell",
        leverage=8, trade_nature="swing",
    )
    assert res.allowed is True


def test_tier_to_nature_fallback_resolves_conflict():
    """未显式传 trade_nature,但 tier='short' → 推断 scalp → 与既有 scalp long 冲突。"""
    existing = [_make_pos("long", size=1.0, leverage=10, trade_nature="scalp")]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="short", order_side="sell",
        leverage=8, tier="short",  # short → scalp
    )
    assert res.allowed is False
    assert "scalp" in res.reason


# ── 规则 3:统一杠杆 = max ──────────────────────────────────────────


def test_unified_leverage_is_max_of_all():
    """现有 10x、12x,新请求 8x → 统一 12x(max)。"""
    existing = [
        _make_pos("long", size=1.0, leverage=10, trade_nature="scalp"),
        _make_pos("short", size=0.5, leverage=12, trade_nature="trend_follow"),
    ]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="long", order_side="buy",
        leverage=8, trade_nature="swing",
    )
    assert res.allowed is True
    assert res.unified_leverage == pytest.approx(12.0)


def test_unified_leverage_takes_new_request_when_highest():
    """现有 8x,新请求 20x → 统一 20x(max)。"""
    existing = [_make_pos("long", size=1.0, leverage=8, trade_nature="scalp")]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="long", order_side="buy",
        leverage=20, trade_nature="scalp",
    )
    assert res.allowed is True
    assert res.unified_leverage == pytest.approx(20.0)


def test_unified_leverage_first_position():
    """无既有仓位 → 统一杠杆 = 请求杠杆。"""
    db = _make_db([])
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="long", order_side="buy",
        leverage=15, trade_nature="scalp",
    )
    assert res.allowed is True
    assert res.unified_leverage == pytest.approx(15.0)


# ── 规则 4:净暴露 ──────────────────────────────────────────────────


def test_net_exposure_long_minus_short():
    """long 1.0 + short 0.5 → 净 0.5。"""
    existing = [
        _make_pos("long", size=1.0, leverage=10, trade_nature="scalp"),
        _make_pos("short", size=0.5, leverage=12, trade_nature="trend_follow"),
    ]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="long", order_side="buy",
        leverage=8, trade_nature="swing",
    )
    assert res.allowed is True
    assert res.net_exposure == pytest.approx(0.5)


def test_net_exposure_fully_hedged_zero():
    """等量对冲 → 净暴露 0。"""
    existing = [
        _make_pos("long", size=2.0, leverage=10, trade_nature="scalp"),
        _make_pos("short", size=2.0, leverage=10, trade_nature="trend_follow"),
    ]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="long", order_side="buy",
        leverage=10, trade_nature="swing",
    )
    assert res.allowed is True
    assert res.net_exposure == pytest.approx(0.0)


def test_net_exposure_short_dominates_negative():
    """short 3.0 + long 1.0 → 净 -2.0。"""
    existing = [
        _make_pos("short", size=3.0, leverage=10, trade_nature="trend_follow"),
        _make_pos("long", size=1.0, leverage=10, trade_nature="scalp"),
    ]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="long", order_side="buy",
        leverage=10, trade_nature="swing",
    )
    assert res.allowed is True
    assert res.net_exposure == pytest.approx(-2.0)


# ── 鲁棒性 ──────────────────────────────────────────────────────────


def test_query_failure_does_not_block():
    """查询抛异常 → 协调器回退放行(不成为单点故障)。"""
    db = MagicMock()
    db.query.side_effect = RuntimeError("db down")
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="long", order_side="buy",
        leverage=10, trade_nature="scalp",
    )
    assert res.allowed is True
    assert "coordinator_query_failed" in res.reason
    assert res.unified_leverage == pytest.approx(10.0)


def test_summarize_includes_all_fields():
    """摘要包含 trade_nature/side/size/leverage。"""
    existing = [_make_pos("long", size=1.5, leverage=11, trade_nature="swing")]
    db = _make_db(existing)
    coord = PositionCoordinator()

    res = coord.coordinate_open(
        db=db, account_id=1, symbol="BTC",
        side="short", order_side="sell",
        leverage=10, trade_nature="scalp",
    )
    assert res.allowed is True
    assert res.existing_sub_positions == [
        {"trade_nature": "swing", "side": "long", "size": 1.5, "leverage": 11.0},
    ]


def test_coordination_result_defaults():
    """CoordinationResult 默认值:list 类型不共享(Mutable 默认陷阱)。"""
    r1 = CoordinationResult(allowed=True)
    r2 = CoordinationResult(allowed=False)
    r1.existing_sub_positions.append({"x": 1})
    assert r2.existing_sub_positions == []  # 不被污染
