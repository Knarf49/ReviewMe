import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TIMESTAMP,
    Uuid,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="user",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    threads: Mapped[list["Thread"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
    )


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="threads")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_threads_user_id", "user_id"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    thread: Mapped["Thread"] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="messages_role_check",
        ),
        Index("idx_messages_thread_created", "thread_id", "created_at"),
    )


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    __table_args__ = (
        Index("ix_refresh_sessions_user_id", "user_id"),
        Index("ix_refresh_sessions_family_id", "family_id"),
        Index("ix_refresh_sessions_expires_at", "expires_at"),
    )
