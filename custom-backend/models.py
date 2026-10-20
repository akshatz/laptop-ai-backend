import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 👥 SQLALCHEMY ORM MODELS
class Base(DeclarativeBase):
    pass

class UserModel(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # email verification
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # optional MFA (email OTP)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_otp_code: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    mfa_otp_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # password reset (email OTP)
    password_reset_otp: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    password_reset_otp_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

class UserChatModel(Base):
    __tablename__ = "user_chats"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="New Chat")
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class MessageModel(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_chats.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    # Answers only: 1 = like, -1 = dislike, NULL = not rated.
    feedback: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

class ChatDocumentModel(Base):
    """A file (or pasted text) added to a chat as context, kept so its owner can see and open it."""
    __tablename__ = "chat_documents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_chats.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    chunks: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # sha256 of `data`: the same content again in the same chat is a duplicate, not re-embedded.
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Per chat and filename; a changed file with the same name is the next version and replaces
    # the previous one's chunks in Milvus.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # embedding → ready | failed; an older version becomes replaced.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready", server_default="ready")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
