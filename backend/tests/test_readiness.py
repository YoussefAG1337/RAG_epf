"""Tests for the initial versioned readiness endpoint."""

from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.main import create_app
from app.schema_guard import EmbeddingSchemaMismatchError


def test_readiness_is_available_without_external_services() -> None:
    response = TestClient(create_app(schema_guard=None)).get("/api/v1/readiness")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "service": "course-rag-api"}


def test_readiness_allows_local_frontend_origin() -> None:
    response = TestClient(create_app(schema_guard=None)).get(
        "/api/v1/readiness", headers={"Origin": "http://localhost:3000"}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_startup_schema_mismatch_prevents_readiness() -> None:
    class ConnectionContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeEngine:
        def connect(self) -> ConnectionContext:
            return ConnectionContext()

        def dispose(self) -> None:
            return None

    def engine_factory(_settings: object) -> Engine:
        return cast(Engine, FakeEngine())

    def reject_startup(_connection: object, _settings: object) -> None:
        raise EmbeddingSchemaMismatchError(
            "runtime embedding configuration does not match migrated metadata"
        )

    application = create_app(
        schema_guard=reject_startup,  # type: ignore[arg-type]
        engine_factory=engine_factory,  # type: ignore[arg-type]
    )
    with pytest.raises(EmbeddingSchemaMismatchError, match="does not match migrated metadata"):
        with TestClient(application):
            pass
