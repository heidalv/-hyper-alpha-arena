"""
SubPositionManager 单元测试（v3 整改已对齐）

覆盖现行 API：
  review_open(db, account_id, symbol, side, trade_nature, notional_usd, tp_pct, total_equity)
    → (bool, reason)
  review_reduce(db, position_id, reduce_pct, is_stop_loss)
    → (bool, reason)

依赖 conftest.py 中的 db_session fixture（内存 SQLite + 真 PaperPosition 表）。
"""
import pytest
from datetime import datetime, timedelta, timezone


def _make_position(
    db_session,
    *,
    account_id: int = 1,
    symbol: str = "BTC",
    side: str = "long",
    trade_nature: str = "swing",
    size: float = 0.01,
    entry_price: float = 84000.0,
    mark_price: float = 84000.0,
    margin: float = 100.0,
    unrealized_pnl: float = 0.0,
    reduce_count: int = 0,
    last_reduce_at=None,
    status: str = "open",
):
    from backend.database.models import PaperPosition
    pos = PaperPosition(
        account_id=account_id,
        symbol=symbol,
        side=side,
        size=size,
        entry_price=entry_price,
        mark_price=mark_price,
        leverage=10.0,
        margin=margin,
        unrealized_pnl=unrealized_pnl,
        liquidation_price=0.0,
        status=status,
        trade_nature=trade_nature,
        reduce_count=reduce_count,
        last_reduce_at=last_reduce_at,
    )
    db_session.add(pos)
    db_session.flush()
    return pos


@pytest.mark.unit
class TestReviewOpen:
    @pytest.fixture(autouse=True)
    def setup(self):
        from backend.services.sub_position_manager import SubPositionManager
        # audit_only=True 时所有拦截都"只记录不生效"，测试需要真正生效以校验判定
        self.mgr = SubPositionManager(audit_only=False)

    def test_open_on_empty_portfolio_passes(self, db_session):
        ok, reason = self.mgr.review_open(
            db=db_session,
            account_id=1,
            symbol="BTC",
            side="long",
            trade_nature="swing",
            notional_usd=0,
            tp_pct=0,
            total_equity=0,
        )
        assert ok is True, reason

    def test_open_rejects_opposite_direction(self, db_session):
        _make_position(db_session, symbol="BTC", side="long", trade_nature="trend_follow")
        ok, reason = self.mgr.review_open(
            db=db_session,
            account_id=1,
            symbol="BTC",
            side="short",
            trade_nature="swing",
        )
        assert ok is False
        assert "long" in reason or "翻转" in reason

    def test_open_rejects_duplicate_nature(self, db_session):
        _make_position(db_session, symbol="BTC", side="long", trade_nature="swing")
        ok, reason = self.mgr.review_open(
            db=db_session,
            account_id=1,
            symbol="BTC",
            side="long",
            trade_nature="swing",
        )
        assert ok is False
        assert "swing" in reason

    def test_open_allows_different_nature(self, db_session):
        _make_position(db_session, symbol="BTC", side="long", trade_nature="trend_follow")
        ok, _reason = self.mgr.review_open(
            db=db_session,
            account_id=1,
            symbol="BTC",
            side="long",
            trade_nature="intraday",
        )
        assert ok is True

    def test_open_rejects_over_3_sub_positions(self, db_session):
        _make_position(db_session, symbol="BTC", side="long", trade_nature="trend_follow")
        _make_position(db_session, symbol="BTC", side="long", trade_nature="swing")
        _make_position(db_session, symbol="BTC", side="long", trade_nature="intraday")
        ok, reason = self.mgr.review_open(
            db=db_session,
            account_id=1,
            symbol="BTC",
            side="long",
            trade_nature="position",
        )
        assert ok is False
        assert "上限" in reason or "3" in reason


@pytest.mark.unit
class TestReviewReduce:
    @pytest.fixture(autouse=True)
    def setup(self):
        from backend.services.sub_position_manager import SubPositionManager
        self.mgr = SubPositionManager(audit_only=False)

    def test_reduce_in_cooldown_rejected(self, db_session):
        # trend_follow 冷却 24h，距上次 1h 应被拒绝
        pos = _make_position(
            db_session,
            trade_nature="trend_follow",
            margin=100.0,
            unrealized_pnl=6.0,
            reduce_count=1,
            last_reduce_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        ok, reason = self.mgr.review_reduce(
            db=db_session,
            position_id=pos.id,
            reduce_pct=0.3,
            is_stop_loss=False,
        )
        assert ok is False
        assert "冷却" in reason

    def test_reduce_stop_loss_bypasses_cooldown(self, db_session):
        pos = _make_position(
            db_session,
            trade_nature="trend_follow",
            margin=100.0,
            unrealized_pnl=-3.0,
            reduce_count=1,
            last_reduce_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        ok, _reason = self.mgr.review_reduce(
            db=db_session,
            position_id=pos.id,
            reduce_pct=1.0,
            is_stop_loss=True,
        )
        assert ok is True

    def test_reduce_over_max_ratio_rejected(self, db_session):
        # swing 单次最大减仓 50%，请求 70% 应被拒
        pos = _make_position(
            db_session,
            trade_nature="swing",
            margin=100.0,
            unrealized_pnl=10.0,  # +10% 浮盈，绕过 min_profit 拦截
            reduce_count=0,
            last_reduce_at=None,
        )
        ok, reason = self.mgr.review_reduce(
            db=db_session,
            position_id=pos.id,
            reduce_pct=0.7,
            is_stop_loss=False,
        )
        assert ok is False
        assert "比例" in reason or "上限" in reason

    def test_reduce_missing_position_is_passthrough(self, db_session):
        ok, reason = self.mgr.review_reduce(
            db=db_session,
            position_id=999999,
            reduce_pct=0.5,
            is_stop_loss=False,
        )
        assert ok is True
        assert "pass-through" in reason or "not found" in reason
