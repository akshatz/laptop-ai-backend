import os
import uuid
from datetime import datetime
from typing import AsyncGenerator, List
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import String, Text, DateTime, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 🚀 1. DATABASE CONNECTION POOL CONFIGURATION
# Connects seamlessly to the internal container DNS name defined in docker-compose.yml
DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True
)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

# 👥 2. SQLALCHEMY ORM MODELS
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

# 📋 3. PYDANTIC DATA VALIDATION SCHEMAS
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str

class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    role: str

    class Config:
        from_attributes = True

# ⚡ 4. FASTAPI INSTANTIATION WITH RUST-BASED ORJSON
app = FastAPI(title="Laptop Dev Backend Engine", default_response_class=ORJSONResponse)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "environment": "laptop-sandbox"}

@app.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    """
    Securely registers new family user accounts into the laptop database instance.
    """
    # Verify if user profile email duplicate exists
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    existing_user = query.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account profile email address is already registered."
        )
    
    # In a production environment, pass this string payload through a hashing library like bcrypt/passlib
    new_user = UserModel(
        email=payload.email,
        password_hash=payload.password, 
        name=payload.name
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    return new_user

@app.get("/users", response_model=List[UserResponse])
async def get_all_users(db: AsyncSession = Depends(get_db)):
    """
    Retrieves all records matching registered user accounts.
    """
    query = await db.execute(select(UserModel).order_by(UserModel.created_at.desc()))
    return query.scalars().all()
