"""Saved conversations: questions, answers with their citations, and the search scope."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.answering import HistoryTurn
from app.models import Conversation, Message

TITLE_LENGTH = 80


class ConversationSummary(BaseModel):
    id: str
    title: str
    scope: dict[str, str]
    updated_at: datetime


class MessageView(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    payload: dict[str, Any]
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageView]


def _title(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= TITLE_LENGTH else text[: TITLE_LENGTH - 1].rstrip() + "…"


def _summary(conversation: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=str(conversation.id),
        title=conversation.title,
        scope=dict(conversation.scope or {}),
        updated_at=conversation.updated_at,
    )


def start_or_continue(
    session_factory: sessionmaker[Session],
    conversation_id: UUID | None,
    question: str,
    scope: dict[str, str],
) -> tuple[UUID, list[HistoryTurn]]:
    """Return the conversation to answer in (created if needed) and its prior turns.

    An unknown ``conversation_id`` starts a new conversation instead of failing.
    """

    with session_factory.begin() as session:
        conversation = (
            session.get(Conversation, conversation_id) if conversation_id is not None else None
        )
        if conversation is None:
            conversation = Conversation(title=_title(question), scope=scope)
            session.add(conversation)
            session.flush()
            return conversation.id, []
        conversation.scope = scope
        messages = session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.position)
        ).all()
        history = [
            HistoryTurn(
                role="user" if message.role == "user" else "assistant", content=message.content
            )
            for message in messages
        ]
        return conversation.id, history


def append_messages(
    session_factory: sessionmaker[Session],
    conversation_id: UUID,
    messages: list[tuple[Literal["user", "assistant"], str, dict[str, Any]]],
) -> list[UUID]:
    """Append messages in order and bump the conversation's update time."""

    with session_factory.begin() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise LookupError("conversation not found")
        position = session.scalar(
            select(func.coalesce(func.max(Message.position), -1)).where(
                Message.conversation_id == conversation_id
            )
        )
        next_position = int(position if position is not None else -1) + 1
        created: list[Message] = []
        for offset, (role, content, payload) in enumerate(messages):
            message = Message(
                conversation_id=conversation_id,
                position=next_position + offset,
                role=role,
                content=content,
                payload=payload,
            )
            session.add(message)
            created.append(message)
        conversation.updated_at = datetime.now(UTC)
        session.flush()
        return [message.id for message in created]


def list_conversations(
    session_factory: sessionmaker[Session], limit: int = 100
) -> list[ConversationSummary]:
    with session_factory() as session:
        conversations = session.scalars(
            select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)
        ).all()
        return [_summary(conversation) for conversation in conversations]


def get_conversation(
    session_factory: sessionmaker[Session], conversation_id: UUID
) -> ConversationDetail | None:
    with session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return None
        messages = session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.position)
        ).all()
        summary = _summary(conversation)
        return ConversationDetail(
            **summary.model_dump(),
            messages=[
                MessageView(
                    id=str(message.id),
                    role="user" if message.role == "user" else "assistant",
                    content=message.content,
                    payload=dict(message.payload or {}),
                    created_at=message.created_at,
                )
                for message in messages
            ],
        )


def rename_conversation(
    session_factory: sessionmaker[Session], conversation_id: UUID, title: str
) -> ConversationSummary | None:
    cleaned = _title(title)
    if not cleaned:
        raise ValueError("title must not be empty")
    with session_factory.begin() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return None
        conversation.title = cleaned
        session.flush()
        return _summary(conversation)


def delete_conversation(session_factory: sessionmaker[Session], conversation_id: UUID) -> bool:
    with session_factory.begin() as session:
        result = session.execute(delete(Conversation).where(Conversation.id == conversation_id))
        return bool(getattr(result, "rowcount", 0))
