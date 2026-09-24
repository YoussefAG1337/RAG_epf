"""FastAPI application and initial readiness contract."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.engine import Connection, Engine

from app.config import Settings, get_settings
from app.db import create_database_engine
from app.schema_guard import validate_embedding_schema


class ReadinessResponse(BaseModel):
    """Non-sensitive API readiness response."""

    status: Literal["ready"] = "ready"
    service: Literal["course-rag-api"] = "course-rag-api"


def create_app(
    *,
    schema_guard: Callable[[Connection, Settings], None] | None = validate_embedding_schema,
    engine_factory: Callable[[Settings], Engine] = create_database_engine,
) -> FastAPI:
    """Build the API application with an injectable startup schema check."""

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        if schema_guard is not None:
            settings = get_settings()
            engine = engine_factory(settings)
            try:
                with engine.connect() as connection:
                    schema_guard(connection, settings)
            finally:
                engine.dispose()
        yield

    application = FastAPI(title="Local Course RAG API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[get_settings().frontend_origin],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @application.get("/api/v1/readiness", response_model=ReadinessResponse, tags=["system"])
    def readiness() -> ReadinessResponse:
        return ReadinessResponse()

    return application


app = create_app()
