"""统一账户服务层测试（阶段 4）。

验证:
- UnifiedPaperAccountView / CombinedExposure 数据类契约
- UnifiedAccountService 归一化视图（AI 树 + 套利树）
- get_combined_exposure 跨系统合并
- transfer_capital 资金划转（记账层）
- rebate_positions 软关联（owner_account_id）
"""
import pytest
from unittest.mock import MagicMock, patch

from backend.services.unified_account_service import (
    CombinedExposure,
    UnifiedAccountService,
    UnifiedPaperAccountView,
    unified_account_service,
)


# ── 数据类契约 ──────────────────────────────────────────────────

def test_unified_paper_account_view_basic():
    v = UnifiedPaperAccountView(
        id=5, scope="ai", source_table="paper_balances",
        total_equity=10000.0, available_balance=8000.0,
        frozen_balance=2000.0,
    )
    assert v.id == 5
    assert v.scope == "ai"
    assert v.source_table == "paper_balances"
    assert v.total_equity == 10000.0
    d = v.to_dict()
    assert d["id"] == 5
    assert d["scope"] == "ai"
    assert d["total_equity"] == 10000.0


def test_unified_paper_account_view_arbitrage_scope():
    v = UnifiedPaperAccountView(
        id=3, scope="arbitrage", source_table="arbitrage_paper_accounts",
        owner_account_id=2, risk_profile="balanced",
    )
    assert v.scope == "arbitrage"
    assert v.source_table == "arbitrage_paper_accounts"
    assert v.owner_account_id == 2
    assert v.risk_profile == "balanced"


def test_combined_exposure_defaults():
    e = CombinedExposure()
    assert e.ai_equity == 0.0
    assert e.arbitrage_equity == 0.0
    assert e.total_equity == 0.0
    d = e.to_dict()
    assert d["ai_equity"] == 0.0
    assert d["total_equity"] == 0.0


def test_combined_exposure_aggregation():
    e = CombinedExposure(
        ai_equity=10000, ai_frozen=2000, ai_upnl=500,
        arbitrage_equity=5000, arbitrage_frozen=1000, arbitrage_upnl=200,
    )
    e.total_equity = e.ai_equity + e.arbitrage_equity
    e.total_frozen = e.ai_frozen + e.arbitrage_frozen
    e.total_upnl = e.ai_upnl + e.arbitrage_upnl
    assert e.total_equity == 15000
    assert e.total_frozen == 3000
    assert e.total_upnl == 700


# ── UnifiedAccountService 归一化（mock DB）─────────────────────

def test_get_unified_paper_account_ai_mock():
    """AI 树: mock PaperBalance + Account"""
    svc = UnifiedAccountService()
    db = MagicMock()

    # mock PaperBalance 查询
    mock_bal = MagicMock()
    mock_bal.account_id = 5
    mock_bal.total_equity = 10000.0
    mock_bal.available_balance = 8000.0
    mock_bal.frozen_margin = 2000.0
    mock_bal.unrealized_pnl = 500.0
    mock_bal.realized_pnl = 200.0
    mock_bal.total_fee_paid = 10.0
    mock_bal.initial_balance = 10000.0

    mock_account = MagicMock()
    mock_account.name = "AI-Trader-5"
    mock_account.selected_exchange = "asterdex"

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "PaperBalance":
            q.filter.return_value.first.return_value = mock_bal
        elif model.__name__ == "Account":
            q.filter.return_value.first.return_value = mock_account
        else:
            q.filter.return_value.first.return_value = None
        return q

    db.query.side_effect = query_side_effect

    with patch("backend.database.models.PaperBalance") as mock_pb, \
         patch("backend.database.models.Account") as mock_acc:
        mock_pb.__name__ = "PaperBalance"
        mock_acc.__name__ = "Account"
        view = svc._get_ai_paper_account(db, 5)

    assert view is not None
    assert view.id == 5
    assert view.scope == "ai"
    assert view.source_table == "paper_balances"
    assert view.total_equity == 10000.0
    assert view.available_balance == 8000.0
    assert view.exchange == "asterdex"


def test_get_unified_paper_account_none_when_missing():
    """账户不存在时返回 None"""
    svc = UnifiedAccountService()
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value.first.return_value = None
    db.query.return_value = q
    with patch("backend.database.models.PaperBalance"):
        view = svc._get_ai_paper_account(db, 999)
    assert view is None


def test_get_combined_exposure_mock():
    """get_combined_exposure 合并 AI + 套利"""
    svc = UnifiedAccountService()
    db = MagicMock()

    # mock 两个账户视图
    ai_view = UnifiedPaperAccountView(
        id=5, scope="ai", source_table="paper_balances",
        total_equity=10000, frozen_balance=2000, unrealized_pnl=500,
    )
    arb_view = UnifiedPaperAccountView(
        id=3, scope="arbitrage", source_table="arbitrage_paper_accounts",
        total_equity=5000, frozen_balance=1000, unrealized_pnl=200,
    )

    with patch.object(svc, "_get_ai_paper_account", return_value=ai_view), \
         patch.object(svc, "_get_arbitrage_paper_account", return_value=arb_view):
        exposure = svc.get_combined_exposure(db, ai_account_id=5, arbitrage_account_id=3)

    assert exposure.ai_equity == 10000
    assert exposure.arbitrage_equity == 5000
    assert exposure.total_equity == 15000
    assert exposure.total_frozen == 3000
    assert exposure.total_upnl == 700
    assert exposure.ai_account_id == 5
    assert exposure.arbitrage_account_id == 3


def test_get_combined_exposure_partial():
    """只查 AI 树（arbitrage_account_id=None）"""
    svc = UnifiedAccountService()
    db = MagicMock()
    ai_view = UnifiedPaperAccountView(
        id=5, scope="ai", source_table="paper_balances",
        total_equity=10000, frozen_balance=2000, unrealized_pnl=500,
    )
    with patch.object(svc, "_get_ai_paper_account", return_value=ai_view), \
         patch.object(svc, "_get_arbitrage_paper_account") as mock_arb:
        exposure = svc.get_combined_exposure(db, ai_account_id=5, arbitrage_account_id=None)
    # 套利树未查询
    mock_arb.assert_not_called()
    assert exposure.arbitrage_equity == 0.0
    assert exposure.ai_equity == 10000
    assert exposure.total_equity == 10000  # 只有 AI


# ── transfer_capital ────────────────────────────────────────────

def test_transfer_capital_invalid_amount():
    svc = UnifiedAccountService()
    db = MagicMock()
    result = svc.transfer_capital(db, "ai", 5, "arbitrage", 3, -100)
    assert result["success"] is False
    assert "金额" in result["error"]


def test_transfer_capital_insufficient_balance():
    svc = UnifiedAccountService()
    db = MagicMock()
    from_view = UnifiedPaperAccountView(
        id=5, scope="ai", source_table="paper_balances", available_balance=100.0,
    )
    with patch.object(svc, "get_unified_paper_account", return_value=from_view):
        result = svc.transfer_capital(db, "ai", 5, "arbitrage", 3, 500)
    assert result["success"] is False
    assert "余额不足" in result["error"]


def test_transfer_capital_success():
    svc = UnifiedAccountService()
    db = MagicMock()
    from_view = UnifiedPaperAccountView(
        id=5, scope="ai", source_table="paper_balances",
        available_balance=1000.0, total_equity=1000.0,
    )
    to_view = UnifiedPaperAccountView(
        id=3, scope="arbitrage", source_table="arbitrage_paper_accounts",
        available_balance=500.0, total_equity=500.0,
    )
    # 第一次调用返回 from_view（检查余额），第二次返回 to_view
    with patch.object(svc, "get_unified_paper_account", side_effect=[from_view, to_view]), \
         patch.object(svc, "_adjust_balance") as mock_adjust:
        result = svc.transfer_capital(db, "ai", 5, "arbitrage", 3, 300)
    assert result["success"] is True
    assert result["amount"] == 300
    # 验证两侧余额调整
    assert mock_adjust.call_count == 2
    # 第一次: from 扣减 -300
    assert mock_adjust.call_args_list[0].args[3] == -300
    # 第二次: to 增加 +300
    assert mock_adjust.call_args_list[1].args[3] == 300
    db.commit.assert_called_once()


# ── 单例 ────────────────────────────────────────────────────────

def test_unified_account_service_singleton():
    assert unified_account_service is not None
    assert isinstance(unified_account_service, UnifiedAccountService)


def test_service_methods_exist():
    """验证所有抽象方法已实现"""
    svc = UnifiedAccountService()
    assert hasattr(svc, "get_unified_paper_account")
    assert hasattr(svc, "list_all_paper_accounts")
    assert hasattr(svc, "get_combined_exposure")
    assert hasattr(svc, "get_arbitrage_positions_for_account")
    assert hasattr(svc, "transfer_capital")
    assert hasattr(svc, "_adjust_balance")


# ── rebate_positions owner_account_id 列（阶段 4.2）────────────

def test_rebate_position_model_has_owner_account_id():
    """验证 RebatePositionDB 模型有 owner_account_id 列（阶段 4.2）"""
    from backend.database.models import RebatePositionDB
    col = getattr(RebatePositionDB, "owner_account_id", None)
    assert col is not None
    # 列应 nullable（老数据留空）
    assert col.nullable is True
