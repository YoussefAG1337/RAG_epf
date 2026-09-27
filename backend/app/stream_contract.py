"""Versioned NDJSON contract for answer streams."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    type: str


class DeltaEvent(StreamEvent):
    type: Literal["delta"] = "delta"
    text: str


class CitationItem(BaseModel):
    citation_id: str
    subject: str
    course_id: str
    document_title: str | None
    section_title: str | None
    source_filename: str
    physical_page_number: int
    excerpt: str


class CitationsEvent(StreamEvent):
    type: Literal["citations"] = "citations"
    citations: list[CitationItem]


class CompletedEvent(StreamEvent):
    type: Literal["completed"] = "completed"


class ClarificationEvent(StreamEvent):
    type: Literal["clarification"] = "clarification"
    message: str


class AbstentionEvent(StreamEvent):
    type: Literal["abstention"] = "abstention"
    message: str


class ErrorEvent(StreamEvent):
    type: Literal["error"] = "error"
    message: str
