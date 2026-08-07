"""
Test fixtures for Hyper Alpha Arena
"""
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def mock_db_session():
    """Mock database session"""
    session = MagicMock()
    return session


@pytest.fixture
def mock_user():
    """Mock user object"""
    user = MagicMock()
    user.id = 1
    user.username = "test_user"
    user.email = "test@example.com"
    user.is_active = "true"
    return user


@pytest.fixture
def mock_account():
    """Mock account object"""
    account = MagicMock()
    account.id = 1
    account.user_id = 1
    account.name = "Test Account"
    account.account_type = "AI"
    account.trading_mode = "paper"
    account.current_cash = 10000.00
    account.initial_capital = 10000.00
    return account


@pytest.fixture
def sample_trade_data():
    """Sample trade data for testing"""
    return {
        "symbol": "BTC",
        "name": "Bitcoin",
        "side": "BUY",
        "price": 50000.00,
        "quantity": 0.01,
        "commission": 0.5,
    }


@pytest.fixture
def sample_order_data():
    """Sample order data for testing"""
    return {
        "symbol": "BTC",
        "name": "Bitcoin",
        "side": "BUY",
        "order_type": "MARKET",
        "price": 50000.00,
        "quantity": 1,
    }


@pytest.fixture(autouse=True)
def mock_env_variables():
    """Mock environment variables for testing"""
    with patch.dict(os.environ, {
        "DATABASE_URL": "sqlite:///./test.db",
        "SNAPSHOT_DATABASE_URL": "sqlite:///./test_snapshots.db",
        "BINANCE_ENCRYPTION_KEY": "test_key_for_testing_purposes_only",
    }):
        yield
