"""Durable chat runs and immutable UI candidate snapshots."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatRun(Base):
    __tablename__ = "chat_runs"
    __table_args__ = (
        UniqueConstraint("conversation_id", "request_id"),
        Index("uq_chat_runs_active", "conversation_id", unique=True,
              postgresql_where=text("status IN ('queued', 'running')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversation.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    stage: Mapped[str] = mapped_column(String(80), default="")
    request_data: Mapped[dict] = mapped_column(JSON)
    partial_text: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())


class SkillCard(Base):
    __tablename__ = "skill_cards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversation.id"), index=True)
    skill_name: Mapped[str] = mapped_column(String(80))
    planning_version: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    submission_hash: Mapped[str | None] = mapped_column(String(64))
    submission: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
