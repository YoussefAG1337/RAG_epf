"""FastAPI application and initial readiness contract."""

from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import get_settings


class ReadinessResponse(BaseModel):
    """Non-sensitive API readiness response."""

    status: Literal["ready"] = "ready"
    service: Literal["course-rag-api"] = "course-rag-api"


def create_app() -> FastAPI:
    """Build the API application without contacting external providers."""

    application = FastAPI(title="Local Course RAG API", version="0.1.0")
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
