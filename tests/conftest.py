"""
Shared fixtures for the Hyper-Alpha-Arena test suite.

Provides:
- In-memory SQLite DB session for isolated unit/integration tests
- FastAPI TestClient for API endpoint tests
- Common mock objects (strategy, position, balance)
"""
import os
import sys
import pytest
from unittest.mock import MagicMock

# Ensure project root is on sys.path so `backend.*` imports work
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ──────────────────────────────────────────────────
# 落库隔离（v6 计划 1.6）：默认禁止测试直连生产库写数据
# ──────────────────────────────────────────────────
# 历史教训：unit 测试直连生产 PostgreSQL（SessionLocal/AnalyticsSessionLocal）
# 写入真实数据 —— walk_forward_reports 39 条 test_f*、opencode_evolution_proposals
# 150 条 test* 污染（已清理）。现按 nodeid 匹配已知写库测试类/函数，默认 skip；
# 确需真实落库的集成验证时设环境变量 PYTEST_ALLOW_DB_WRITE=1 显式放行。
_DB_WRITE_TEST_NODEIDS = (
    # test_opencode_layer.py：proposal 创建/评审/应用全部落 core 库
    "TestOpenCodeProposalApplier",
    "TestOpenCodeProposalReviewer",
    # test_training_phase.py：训练期 proposal 应用/合并落库
    "TestProposalApplierTraining",
    "TestProposalReviewerTrainingMajor",
    "TestTrainingOrchestrator",
    # test_mlto_chain.py：thesis 缓存落库直连 analytics 库
    "test_thesis_store_cache_restore",
)


def pytest_collection_modifyitems(config, items):
    """默认隔离：写生产库的测试跳过，PYTEST_ALLOW_DB_WRITE=1 放行。"""
    if os.environ.get("PYTEST_ALLOW_DB_WRITE") == "1":
        return
    skip_db_write = pytest.mark.skip(reason="直连生产库写测试：设 PYTEST_ALLOW_DB_WRITE=1 显式放行")
    for item in items:
        for frag in _DB_WRITE_TEST_NODEIDS:
            if frag in item.nodeid:
                item.add_marker(skip_db_write)
                break


# ──────────────────────────────────────────────────
# Database fixtures
# ──────────────────────────────────────────────────

@pytest.fixture(scope="session")
def engine():
    """Create an in-memory SQLite engine once per session."""
    from sqlalchemy import create_engine
    eng = create_engine("sqlite:///:memory:", echo=False)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def tables(engine):
    """Create all tables from the project's metadata (multi-database)."""
    from backend.database.models import Base, MarketBase, AnalyticsBase
    Base.metadata.create_all(engine)
    MarketBase.metadata.create_all(engine)
    AnalyticsBase.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    MarketBase.metadata.drop_all(engine)
    AnalyticsBase.metadata.drop_all(engine)


@pytest.fixture()
def db_session(engine, tables):
    """Per-test transactional DB session (auto-rolled-back)."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


# ──────────────────────────────────────────────────
# FastAPI TestClient
# ──────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """Import the FastAPI app once."""
    from backend.main import app as fastapi_app
    return fastapi_app


@pytest.fixture()
def client(app):
    """Provide a TestClient per test."""
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate_short_tier_circuit_state():
    """避免 data/short_tier_circuit_state.json 污染跨测试（BTC 熔断等）。"""
    try:
        from backend.services import short_tier_entry_gate as stg
        stg._symbol_loss_tracker.clear()
        stg._same_dir_short_opens.clear()
    except Exception:
        pass
    yield
    try:
        from backend.services import short_tier_entry_gate as stg
        stg._symbol_loss_tracker.clear()
        stg._same_dir_short_opens.clear()
    except Exception:
        pass


# ──────────────────────────────────────────────────
# Mock helpers
# ──────────────────────────────────────────────────

@pytest.fixture()
def mock_strategy():
    """Return a minimal mock AIStrategy object."""
    strat = MagicMock()
    strat.strategy_id = "test_strat_001"
    strat.primary_symbol = "BTC"
    strat.timeframe_tier = "mid"
    strat.status = "active"
    strat.account_id = 1
    strat.trade_nature = "swing"
    strat.genome = {}
    return strat


@pytest.fixture()
def mock_position():
    """Return a minimal mock PaperPosition."""
    pos = MagicMock()
    pos.id = 1
    pos.symbol = "BTC"
    pos.side = "long"
    pos.size = 0.01
    pos.entry_price = 84000.0
    pos.current_price = 85000.0
    pos.unrealized_pnl = 10.0
    pos.trade_nature = "swing"
    pos.strategy_id = "test_strat_001"
    pos.reduce_count = 0
    pos.last_reduce_at = None
    return pos


@pytest.fixture()
def mock_balance():
    """Return a minimal mock PaperBalance."""
    bal = MagicMock()
    bal.account_id = 1
    bal.initial_balance = 150.0
    bal.balance = 140.0
    bal.equity = 145.0
    return bal
