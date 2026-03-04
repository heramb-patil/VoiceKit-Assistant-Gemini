"""Database models for Gemini Live SaaS backend - self-contained."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid_str() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ── User tables ──────────────────────────────────────────────────────────────

class User(Base):
    """Represents a Google Workspace user who has signed in."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    picture: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class UserCredential(Base):
    """Stores per-user OAuth2 tokens for Google and Basecamp integrations."""
    __tablename__ = "user_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    user_email: Mapped[str] = mapped_column(String(255), index=True)
    provider: Mapped[str] = mapped_column(String(32))  # "google" | "basecamp"
    token_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (UniqueConstraint("user_email", "provider"),)


# ── MCP server configs ───────────────────────────────────────────────────────

class UserMCPServer(Base):
    """Stores a user-defined MCP server that runs as a subprocess."""
    __tablename__ = "user_mcp_servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    user_email: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(100))           # display name
    command: Mapped[str] = mapped_column(String(100))         # e.g. "npx"
    args_json: Mapped[str] = mapped_column(Text, default="[]")   # JSON array of args
    env_json: Mapped[str] = mapped_column(Text, default="{}")    # JSON object of env vars
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


# ── Background task tables ───────────────────────────────────────────────────

class TaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class BackgroundTask(Base):
    __tablename__ = "gemini_live_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    user_identity: Mapped[str] = mapped_column(String(255), index=True)
    tool_name: Mapped[str] = mapped_column(String(255))
    tool_args: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.pending)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered: Mapped[bool] = mapped_column(default=False)
