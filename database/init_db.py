import asyncio
import os
from datetime import datetime
import uuid
from typing import Optional
from sqlalchemy import JSON, ForeignKey, String, Text, Boolean, Integer, LargeBinary, DateTime, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# 👥 1. BASE DATABASE ORM DECLARATION
class Base(DeclarativeBase):
    pass

class UserModel(Base):
    __tablename__ = "users"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user") # 'admin', 'user'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # email verification
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # optional MFA (email OTP)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_otp_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    mfa_otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # password reset (email OTP)
    password_reset_otp: Mapped[str | None] = mapped_column(String(6), nullable=True)
    password_reset_otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class UserSessionModel(Base):
    """One sign-in to laptop-frontend (its id is in the session cookie), so admins can see who is
    signed in. Ended on sign-out; a session past SESSION_MAX_AGE is over too."""
    __tablename__ = "user_sessions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

class UserChatModel(Base):
    __tablename__ = "user_chats"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="New Chat")
    model_id: Mapped[str] = mapped_column(String(100), nullable=False) # Tracks Groq/Gemini model used
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class MessageModel(Base):
    __tablename__ = "messages"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_chats.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False) # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    # Answers only: 1 = like, -1 = dislike, NULL = not rated.
    feedback: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Answers only: what the user said was wrong (with 👎), used when the answer is regenerated.
    feedback_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Answers only: how often this answer was regenerated (replaced in place).
    regenerations: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Answers only: the versions Regenerate replaced, oldest first ({content, feedback, created_at}),
    # so they can still be read.
    earlier_versions: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

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

# 🚀 2. ASYNC MIGRATION MACHINE RUNNER
DATABASE_URL = os.environ["INIT_DB_DATABASE_URL"]

async def run_migrations():
    print("⏳ Connecting to laptop database container...")
    engine = create_async_engine(
        DATABASE_URL, 
        pool_pre_ping=True, 
        echo=True # Prints raw SQL outputs to terminal for debugging
    )
    
    async with engine.begin() as conn:
        print("🛠️ Generating users, user_chats, and messages schemas...")
        await conn.run_sync(Base.metadata.create_all)

        # Backfill columns added to `users` after the table already existed
        # (create_all only creates missing tables, not missing columns on
        # existing ones). Existing accounts get password_changed_at set to
        # now, so their 180-day reset clock starts from this migration run
        # rather than appearing already expired.
        print("🛠️ Backfilling new users columns for pre-existing rows...")
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token VARCHAR(64)"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN DEFAULT FALSE"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_otp_code VARCHAR(6)"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_otp_expires_at TIMESTAMPTZ"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_otp VARCHAR(6)"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_otp_expires_at TIMESTAMPTZ"
        ))
        await conn.execute(text(
            "UPDATE users SET password_changed_at = NOW() WHERE password_changed_at IS NULL"
        ))
        await conn.execute(text(
            "ALTER TABLE users ALTER COLUMN password_changed_at SET NOT NULL"
        ))
        await conn.execute(text(
            "ALTER TABLE users ALTER COLUMN password_changed_at SET DEFAULT NOW()"
        ))

        # Performance Indexes Generation
        print("⚡ Injecting structural indexing optimization layers...")
        await conn.execute(Base.metadata.schema.create_index(name="idx_chats_user", table_name="user_chats", columns=["user_id"]))
        await conn.execute(Base.metadata.schema.create_index(name="idx_msgs_chat", table_name="messages", columns=["chat_id"]))
        
        print("✅ Database tables initialized successfully on your laptop!")
        
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(run_migrations())
