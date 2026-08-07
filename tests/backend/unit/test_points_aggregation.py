"""积分汇总工具单元测试。"""

from types import SimpleNamespace

from backend.services.rebate_arb.points_aggregation import (
    CAP_STATE_POSITION_ID,
    aggregate_points_and_pnl,
    is_trade_performance_log,
    point_usd_rate,
    points_to_usd,
)


def test_cap_state_row_excluded_from_pnl():
    cap = SimpleNamespace(
        position_id=CAP_STATE_POSITION_ID,
        strategy_type="CAP",
        total_points=0.0,
        total_pnl=300.0,
    )
    trade = SimpleNamespace(
        position_id="pos-1",
        strategy_type="S8",
        total_points=10.0,
        total_pnl=-2.5,
        source_exchange=None,
    )
    active = [
        SimpleNamespace(
            position_id="pos-2",
            source_exchange="asterdex",
            strategy_type="S8",
            accumulated_points=5.0,
            current_pnl=-0.5,
            paper_mode=True,
        )
    ]
    pos_lookup = [
        SimpleNamespace(position_id="pos-1", source_exchange="asterdex"),
    ]

    ex_stats, _, total_pts, total_pnl = aggregate_points_and_pnl(
        active, [cap, trade], pos_lookup=pos_lookup
    )

    assert is_trade_performance_log(cap) is False
    assert total_pts == 15.0  # 5 active + 10 closed
    assert total_pnl == -3.0  # -0.5 + -2.5，不含 CAP 的 300
    assert ex_stats["asterdex"]["points_earned"] == 15.0


def test_dedupe_performance_logs_by_position():
    from backend.services.rebate_arb.points_aggregation import dedupe_performance_logs

    rows = [
        SimpleNamespace(id=1, position_id="p1", strategy_type="S8", total_points=40.0, total_pnl=-1.0),
        SimpleNamespace(id=2, position_id="p1", strategy_type="S8", total_points=44.0, total_pnl=-6.0),
    ]
    out = dedupe_performance_logs(rows)
    assert len(out) == 1
    assert out[0].total_points == 44.0


def test_point_usd_rate_stage6_default():
    assert point_usd_rate() == 0.005
    assert points_to_usd(100) == 0.5
