import asyncio
import os
from datetime import datetime
import uuid
from sqlalchemy import ForeignKey, String, Text, Boolean, Integer, DateTime, text
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
