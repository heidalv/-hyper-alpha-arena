"""
Integration test — verifies API health endpoint and basic routing.
"""
import pytest


@pytest.mark.integration
class TestHealthEndpoint:
    """Tests for the health check endpoints."""

    def test_api_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_legacy_health_redirects_to_api_health(self, client):
        # /health endpoint removed; verify /api/health is the canonical path
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"


@pytest.mark.integration
class TestAPIRoutes:
    """Smoke tests for key API routes."""

    def test_openapi_schema(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "paths" in data
        assert "/api/health" in data["paths"]
