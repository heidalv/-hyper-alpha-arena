from backend.database.models import ArbitrageProfileDB, FullAutoSession
from backend.services.rebate_arb.arbitrage_paper_account_service import (
    EXCHANGES,
    SYSTEM_PRESETS,
    ArbitragePaperAccountService,
)


def test_normalize_balance_payload_zeroes_missing_exchanges():
    svc = ArbitragePaperAccountService()
    clean = svc._normalize_balance_payload({"asterdex": 300})
    assert clean["asterdex"] == 300
    assert clean["hyperliquid"] == 0
    assert clean["binance"] == 0
    assert clean["reserve"] == 0
    assert sum(clean.values()) == 300


def test_single_asterdex_preset_is_valid():
    preset = SYSTEM_PRESETS["single_asterdex_s8"]
    assert preset["exchange_ratios"]["asterdex"] == 1.0
    assert "S8" in preset["strategy_limits"]
    preset = SYSTEM_PRESETS["small_300u_standard"]
    ratios = preset["exchange_ratios"]

    assert "asterdex" in ratios
    assert "hyperliquid" in ratios
    assert "binance" in ratios
    assert "reserve" in ratios
    assert round(sum(ratios.values()), 6) == 1.0
    assert "reserve" in EXCHANGES


def test_arbitrage_profile_keeps_legacy_and_dedicated_ids_separate():
    profile = ArbitrageProfileDB(
        account_id=1,
        mode="paper",
        paper_account_mode="dedicated_arbitrage_paper",
        paper_account_id=None,
        arbitrage_paper_account_id=7,
    )

    assert profile.paper_account_id is None
    assert profile.arbitrage_paper_account_id == 7
    assert profile.paper_account_mode == "dedicated_arbitrage_paper"


def test_trade_record_from_snapshot_active_short():
    svc = ArbitragePaperAccountService()
    snap = {
        "position_id": "pos_aster_1",
        "symbol": "ASTER/USDT",
        "strategy_type": "S8",
        "source_exchange": "asterdex",
        "side": "sell",
        "leverage": 10,
        "margin_usd": 58.32,
        "side_a_size": 583.2,
        "status": "active",
        "entry_time": 1718000000,
        "accumulated_points": 39.2,
        "current_pnl": 2.63,
        "rh_metrics": {"estimated_rh": 39.8},
        "hold_duration_hours": 2.0,
    }
    rec = svc._trade_record_from_snapshot(snap)
    assert rec["status"] == "持仓中"
    assert rec["side"] == "空"
    assert rec["points_earned"] == 39.2
    assert rec["estimated_round_rh"] == 39.8


def test_build_trade_records_without_ledger_uses_paper_positions():
    """活跃仓在 monitor 中可见但 ledger 为空时，仍应返回一条交易记录。"""
    from unittest.mock import MagicMock

    svc = ArbitragePaperAccountService()
    db = MagicMock()
    empty = MagicMock()
    empty.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    empty.filter.return_value.order_by.return_value.all.return_value = []
    empty.filter.return_value.all.return_value = []
    db.query.return_value = empty

    paper_positions = [{
        "position_id": "pos_aster_1",
        "symbol": "ASTER/USDT",
        "strategy_type": "S8",
        "source_exchange": "asterdex",
        "side": "sell",
        "leverage": 10,
        "margin_usd": 58.32,
        "side_a_size": 583.2,
        "status": "active",
        "entry_time": 1718000000,
        "accumulated_points": 39.2,
        "current_pnl": 2.63,
    }]
    records = svc.build_trade_records(db, account_id=1, paper_positions=paper_positions)
    assert len(records) == 1
    assert records[0]["position_id"] == "pos_aster_1"
    assert records[0]["points_earned"] == 39.2
    assert records[0]["status"] == "持仓中"


def test_build_trade_records_from_ledger_position_details():
    """已平仓、monitor 无活跃仓时，从 ledger.position_details 生成记录。"""
    from unittest.mock import MagicMock

    svc = ArbitragePaperAccountService()
    db = MagicMock()
    empty = MagicMock()
    empty.filter.return_value.order_by.return_value.limit.return_value.all.side_effect = Exception("no table")
    empty.filter.return_value.order_by.return_value.all.side_effect = Exception("no table")
    empty.filter.return_value.all.return_value = []
    db.query.return_value = empty

    ledger = [{
        "id": 231,
        "exchange": "asterdex",
        "action": "paper_margin_release",
        "amount_usd": 58.32,
        "related_position_id": "rebate_S8_47d4c036",
        "metadata": {"position_id": "rebate_S8_47d4c036"},
        "position_details": {
            "position_id": "rebate_S8_47d4c036",
            "symbol": "ASTER/USDT",
            "strategy_type": "S8",
            "source_exchange": "asterdex",
            "side": "sell",
            "leverage": 10,
            "margin_usd": 58.32,
            "side_a_size": 583.2,
            "entry_time": 1781188807.87,
            "close_time": 1781196054.61,
            "hold_hours": 2.013,
            "rh_earned": 39.8,
            "estimated_round_rh": 39.8,
            "total_pnl": -5.98,
        },
    }, {
        "id": 230,
        "action": "paper_pnl",
        "related_position_id": "rebate_S8_47d4c036",
        "amount_usd": -5.98,
    }]
    records = svc.build_trade_records(
        db, account_id=3, paper_positions=[], ledger_entries=ledger
    )
    assert len(records) == 1
    assert records[0]["status"] == "已平仓"
    assert records[0]["points_earned"] == 39.8
    assert records[0]["realized_pnl"] == -5.98
    assert records[0]["side"] == "空"


def test_normalize_clears_stale_frozen_with_zero_losses():
    """连亏已清零且冷却过期时，不应继续 frozen。"""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from backend.database.models import TraderMentalState

    from backend.services.position_memory_manager import PositionMemoryManager

    svc = PositionMemoryManager()
    db = MagicMock()
    mental = TraderMentalState(
        account_id=5,
        state="frozen",
        consecutive_losses=0,
        consecutive_wins=1,
        daily_pnl=100.0,
        cooldown_until=datetime.now(timezone.utc) - timedelta(minutes=5),
        state_reason="cautious→frozen (loss_streak_4)",
    )
    bal = MagicMock()
    bal.total_equity = 1000.0
    db.query.return_value.filter.return_value.first.return_value = bal

    svc._normalize_mental_state(db, mental, persist=False)
    assert mental.state in ("normal", "cooldown")
    assert mental.state != "frozen"


def test_format_block_reason_not_loss_streak_when_zero():
    from backend.services.position_memory_manager import PositionMemoryManager
    from backend.database.models import TraderMentalState

    svc = PositionMemoryManager()
    mental = TraderMentalState(
        account_id=1,
        state="frozen",
        consecutive_losses=0,
        state_reason="cautious→frozen (daily_loss_5pct)",
    )
    reason = svc._format_block_reason(mental, daily_dd=0.09, cooldown_remaining=5.0)
    assert "连续亏损 0" not in reason
    assert "当日亏损" in reason or "冻结" in reason or "保护" in reason


def test_full_auto_session_can_store_dedicated_arbitrage_paper_without_ai_paper():
    session = FullAutoSession(
        session_id="fa_test",
        account_id=1,
        trading_mode="paper",
        paper_account_mode="dedicated_arbitrage_paper",
        paper_account_id=None,
        arbitrage_paper_account_id=9,
        symbols=["BTC"],
        risk_level="moderate",
        risk_mode="ai_dynamic",
        arb_enabled=True,
        status="running",
    )

    assert session.paper_account_id is None
    assert session.arbitrage_paper_account_id == 9
    assert session.paper_account_mode == "dedicated_arbitrage_paper"
