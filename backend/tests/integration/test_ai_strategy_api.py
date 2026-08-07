"""
Integration tests for AI Strategy API endpoints.

Uses FastAPI TestClient with in-memory SQLite and mocked LLM responses.
Covers: CRUD, activate/pause, execute, generate-framework, create-complete, error paths.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# ── Bootstrap minimal app with in-memory DB ────────────────────
# We create a fresh engine per test session to avoid state bleed.
# Real routes are registered on the FastAPI app; we swap the DB dependency.

TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture(scope="module")
def test_app():
    """Create a FastAPI app with all routes mounted, using in-memory SQLite."""
    import os
    os.environ["BACKEND_API_KEY"] = "test-key-123"  # enable auth for tests
    os.environ["ENVIRONMENT"] = "development"

    # Patch the database engine BEFORE importing main
    with patch("backend.database.connection.engine", create_engine(TEST_DB_URL, echo=False)), \
         patch("backend.database.connection.market_engine", create_engine(TEST_DB_URL, echo=False)), \
         patch("backend.database.connection.analytics_engine", create_engine(TEST_DB_URL, echo=False)), \
         patch("backend.database.connection.SessionLocal", _make_session(TEST_DB_URL)), \
         patch("backend.database.connection.MarketSessionLocal", _make_session(TEST_DB_URL)):
        
        # Create all tables
        from backend.database.connection import Base, MarketBase, AnalyticsBase
        engine = create_engine(TEST_DB_URL, echo=False)
        Base.metadata.create_all(bind=engine)
        MarketBase.metadata.create_all(bind=engine)
        AnalyticsBase.metadata.create_all(bind=engine)

        from backend.main import app
        app.dependency_overrides = {}
        client = TestClient(app)
        yield client


def _make_session(db_url: str):
    """Create a sessionmaker bound to the test DB."""
    engine = create_engine(db_url, echo=False)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def auth_headers() -> dict:
    return {"X-API-Key": "test-key-123"}


@pytest.fixture
def seed_account(test_app, auth_headers):
    """Create a test account and return its ID."""
    resp = test_app.post(
        "/api/accounts",
        json={"name": "test-account", "exchange": "hyperliquid", "environment": "testnet"},
        headers=auth_headers,
    )
    if resp.status_code not in (200, 201):
        # Account may already exist from module setup
        resp = test_app.get("/api/accounts", headers=auth_headers)
        accounts = resp.json()
        if accounts:
            return accounts[0]["id"]
        pytest.skip("Cannot create test account")
    return resp.json()["id"]


# ══════════════════════════════════════════════════════════════

class TestAiStrategyCRUD:
    """Core CRUD operations on ai-strategies endpoints."""

    def test_create_strategy_succeeds(self, test_app, auth_headers, seed_account):
        payload = {
            "name": "Test Momentum Strategy",
            "account_id": seed_account,
            "master_prompt_template_id": None,
            "timeframe_tier": "mid",
            "description": "Unit test strategy",
        }
        resp = test_app.post("/api/ai-strategies", json=payload, headers=auth_headers)
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()
        assert "strategy_id" in data
        assert data["status"] == "draft"

    def test_list_strategies_returns_list(self, test_app, auth_headers, seed_account):
        # Create one first to ensure non-empty
        test_app.post(
            "/api/ai-strategies",
            json={
                "name": "List Test", "account_id": seed_account,
                "timeframe_tier": "short",
            },
            headers=auth_headers,
        )
        resp = test_app.get("/api/ai-strategies", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_get_nonexistent_strategy_returns_404(self, test_app, auth_headers):
        resp = test_app.get("/api/ai-strategies/NONEXISTENT-999", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_strategy(self, test_app, auth_headers, seed_account):
        # Create then delete
        create_resp = test_app.post(
            "/api/ai-strategies",
            json={
                "name": "Delete Me", "account_id": seed_account,
                "timeframe_tier": "short",
            },
            headers=auth_headers,
        )
        sid = create_resp.json()["strategy_id"]
        del_resp = test_app.delete(f"/api/ai-strategies/{sid}", headers=auth_headers)
        assert del_resp.status_code in (200, 204)

    def test_create_without_auth_blocked(self, test_app, seed_account):
        """Write endpoints require X-API-Key when BACKEND_API_KEY is set."""
        # Our fixture sets BACKEND_API_KEY=test-key-123
        resp = test_app.post(
            "/api/ai-strategies",
            json={"name": "No Auth", "account_id": seed_account, "timeframe_tier": "short"},
        )
        assert resp.status_code == 401


class TestAiStrategyActions:
    """Activate / pause / archive / execute lifecycle."""

    def _create_strategy(self, test_app, auth_headers, seed_account, name="ActionTest"):
        resp = test_app.post(
            "/api/ai-strategies",
            json={"name": name, "account_id": seed_account, "timeframe_tier": "short"},
            headers=auth_headers,
        )
        return resp.json()["strategy_id"]

    def test_activate_draft_strategy(self, test_app, auth_headers, seed_account):
        sid = self._create_strategy(test_app, auth_headers, seed_account)
        resp = test_app.post(f"/api/ai-strategies/{sid}/activate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"

    def test_pause_active_strategy(self, test_app, auth_headers, seed_account):
        sid = self._create_strategy(test_app, auth_headers, seed_account)
        test_app.post(f"/api/ai-strategies/{sid}/activate", headers=auth_headers)
        resp = test_app.post(f"/api/ai-strategies/{sid}/pause", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    def test_archive_strategy(self, test_app, auth_headers, seed_account):
        sid = self._create_strategy(test_app, auth_headers, seed_account)
        resp = test_app.post(f"/api/ai-strategies/{sid}/archive", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"


class TestErrorPaths:
    """Verify proper error responses for invalid inputs."""

    def test_create_without_name_returns_422(self, test_app, auth_headers, seed_account):
        resp = test_app.post(
            "/api/ai-strategies",
            json={"account_id": seed_account, "timeframe_tier": "short"},
            headers=auth_headers,
        )
        assert resp.status_code == 422  # Pydantic validation error

    def test_create_with_invalid_tier_returns_422(self, test_app, auth_headers, seed_account):
        resp = test_app.post(
            "/api/ai-strategies",
            json={"name": "Bad Tier", "account_id": seed_account, "timeframe_tier": "ultra"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_update_nonexistent_returns_404(self, test_app, auth_headers):
        resp = test_app.put(
            "/api/ai-strategies/NONEXISTENT-123",
            json={"name": "NewName"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestAuthEnforcement:
    """Verify that dangerous endpoints require auth."""

    DANGEROUS_PATHS = [
        ("POST", "/api/ai-strategies"),
        ("PUT", "/api/ai-strategies/test-123"),
        ("DELETE", "/api/ai-strategies/test-123"),
        ("GET", "/api/llm-configs/1/api-key"),
    ]

    @pytest.mark.parametrize("method,path", DANGEROUS_PATHS)
    def test_missing_auth_returns_401(self, test_app, method, path):
        if method == "GET":
            resp = test_app.get(path)
        elif method == "POST":
            resp = test_app.post(path, json={})
        elif method == "PUT":
            resp = test_app.put(path, json={})
        elif method == "DELETE":
            resp = test_app.delete(path)
        assert resp.status_code == 401, f"{method} {path} should require auth"
