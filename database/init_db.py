import asyncio
import os
from datetime import datetime
import uuid
from sqlalchemy import ForeignKey, String, Text, Boolean, Integer, DateTime
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
        
        # Performance Indexes Generation
        print("⚡ Injecting structural indexing optimization layers...")
        await conn.execute(Base.metadata.schema.create_index(name="idx_chats_user", table_name="user_chats", columns=["user_id"]))
        await conn.execute(Base.metadata.schema.create_index(name="idx_msgs_chat", table_name="messages", columns=["chat_id"]))
        
        print("✅ Database tables initialized successfully on your laptop!")
        
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(run_migrations())
