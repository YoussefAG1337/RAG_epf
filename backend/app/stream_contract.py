"""Versioned NDJSON contract for answer streams (version 2).

A stream starts with ``conversation``, may send ``status`` updates, then either:
``claim``* ``citations`` ``completed``, or one terminal ``abstention`` or ``error``.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.answering import Citation


class StreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[2] = 2
    type: str


class ConversationEvent(StreamEvent):
    type: Literal["conversation"] = "conversation"
    conversation_id: str


class StatusEvent(StreamEvent):
    type: Literal["status"] = "status"
    stage: Literal["searching", "writing"]
    # The standalone question actually searched, when a follow-up was rewritten.
    retrieval_query: str | None = None


class ClaimEvent(StreamEvent):
    type: Literal["claim"] = "claim"
    index: int
    text: str
    citations: list[int]


class CitationsEvent(StreamEvent):
    type: Literal["citations"] = "citations"
    citations: list[Citation]


class CompletedEvent(StreamEvent):
    type: Literal["completed"] = "completed"
    message_id: str | None = None


class AbstentionEvent(StreamEvent):
    type: Literal["abstention"] = "abstention"
    message: str


class ErrorEvent(StreamEvent):
    type: Literal["error"] = "error"
    message: str
