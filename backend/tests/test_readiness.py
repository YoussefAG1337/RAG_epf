"""Tests for the initial versioned readiness endpoint."""

from fastapi.testclient import TestClient

from app.main import app


def test_readiness_is_available_without_external_services() -> None:
    response = TestClient(app).get("/api/v1/readiness")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "service": "course-rag-api"}


def test_readiness_allows_local_frontend_origin() -> None:
    response = TestClient(app).get(
        "/api/v1/readiness", headers={"Origin": "http://localhost:3000"}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
