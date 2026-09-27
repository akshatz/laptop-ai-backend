import os
import random
import secrets
import smtplib
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import AsyncGenerator, List, Optional
import hvac
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import ORJSONResponse
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy import Boolean, String, Text, DateTime, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 🔐 0. FETCH SECRETS FROM OPENBAO
# custom-backend authenticates via AppRole (role/secret id only, no long-lived
# token) and reads the KV v2 secret written by openbao/bootstrap.sh.
def _load_secrets_from_openbao() -> dict:
    client = hvac.Client(url=os.environ["OPENBAO_ADDR"])
    client.auth.approle.login(
        role_id=os.environ["OPENBAO_ROLE_ID"],
        secret_id=os.environ["OPENBAO_SECRET_ID"],
    )
    return client.secrets.kv.v2.read_secret_version(
        path="custom-backend", raise_on_deleted_version=True
    )["data"]["data"]

_secrets = _load_secrets_from_openbao()

# 🚀 1. DATABASE CONNECTION POOL CONFIGURATION
# Connects seamlessly to the internal container DNS name defined in docker-compose.yml
DATABASE_URL = (
    f"postgresql+asyncpg://{_secrets['postgres_user']}:{_secrets['postgres_password']}"
    f"@postgres-db:5432/{_secrets['postgres_db']}"
)

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

# 🔑 password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ✉️ email sending (Gmail SMTP relay, same creds Passbolt uses)
def _send_email(to_address: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = _secrets["smtp_from"]
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(_secrets["smtp_user"], _secrets["smtp_password"])
        smtp.send_message(message)

OTP_VALIDITY = timedelta(minutes=10)
PASSWORD_MAX_AGE = timedelta(days=180)

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
    is_verified: bool
    mfa_enabled: bool
    is_active: bool

    class Config:
        from_attributes = True

class EmailVerifyRequest(BaseModel):
    token: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    mfa_required: bool
    password_reset_required: bool = False
    user: Optional[UserResponse] = None

class MfaOtpVerifyRequest(BaseModel):
    email: EmailStr
    otp_code: str

class PasswordChangeRequest(BaseModel):
    email: EmailStr
    current_password: str
    new_password: str

class PasswordForgotRequest(BaseModel):
    email: EmailStr

class PasswordResetRequest(BaseModel):
    email: EmailStr
    otp_code: str
    new_password: str

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
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    existing_user = query.scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account profile email address is already registered."
        )

    verification_token = secrets.token_urlsafe(32)

    new_user = UserModel(
        email=payload.email,
        password_hash=pwd_context.hash(payload.password),
        name=payload.name,
        is_verified=False,
        verification_token=verification_token,
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    _send_email(
        to_address=new_user.email,
        subject="Verify your account",
        body=f"Hi {new_user.name},\n\nVerify your account with this token:\n{verification_token}\n",
    )

    return new_user

@app.get("/users", response_model=List[UserResponse])
async def get_all_users(db: AsyncSession = Depends(get_db)):
    """
    Retrieves all records matching registered user accounts.
    """
    query = await db.execute(select(UserModel).order_by(UserModel.created_at.desc()))
    return query.scalars().all()

@app.post("/users/verify", response_model=UserResponse)
async def verify_email(payload: EmailVerifyRequest, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.verification_token == payload.token))
    user = query.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification token.")

    user.is_verified = True
    user.verification_token = None
    await db.commit()
    await db.refresh(user)

    return user

@app.post("/users/deactivate", response_model=UserResponse)
async def deactivate_user(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Soft-delete: user rows are never removed from the database, only flagged
    inactive so they can no longer log in.
    """
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if not user or not pwd_context.verify(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    user.is_active = False
    await db.commit()
    await db.refresh(user)

    return user

@app.post("/users/reactivate", response_model=UserResponse)
async def reactivate_user(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if not user or not pwd_context.verify(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    user.is_active = True
    await db.commit()
    await db.refresh(user)

    return user

@app.post("/mfa/enable", response_model=UserResponse)
async def enable_mfa(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Opts a user into email-OTP MFA. MFA remains optional; users who don't
    call this endpoint keep logging in with just a password.
    """
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if not user or not pwd_context.verify(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    user.mfa_enabled = True
    await db.commit()
    await db.refresh(user)

    return user

@app.post("/mfa/disable", response_model=UserResponse)
async def disable_mfa(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if not user or not pwd_context.verify(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    user.mfa_enabled = False
    user.mfa_otp_code = None
    user.mfa_otp_expires_at = None
    await db.commit()
    await db.refresh(user)

    return user

@app.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if not user or not pwd_context.verify(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated.")

    if not user.is_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email address is not verified yet.")

    if datetime.utcnow() - user.password_changed_at > PASSWORD_MAX_AGE:
        otp_code = f"{random.randint(0, 999999):06d}"
        user.password_reset_otp = otp_code
        user.password_reset_otp_expires_at = datetime.utcnow() + OTP_VALIDITY
        await db.commit()

        _send_email(
            to_address=user.email,
            subject="Your password reset code",
            body=(
                "Your password is over 180 days old and must be reset before you can log in.\n"
                f"Your one-time password reset code is: {otp_code}\nIt expires in 10 minutes."
            ),
        )

        return LoginResponse(mfa_required=False, password_reset_required=True, user=None)

    if not user.mfa_enabled:
        return LoginResponse(mfa_required=False, user=user)

    otp_code = f"{random.randint(0, 999999):06d}"
    user.mfa_otp_code = otp_code
    user.mfa_otp_expires_at = datetime.utcnow() + OTP_VALIDITY
    await db.commit()

    _send_email(
        to_address=user.email,
        subject="Your login verification code",
        body=f"Your one-time login code is: {otp_code}\nIt expires in 10 minutes.",
    )

    return LoginResponse(mfa_required=True, user=None)

@app.post("/mfa/verify-otp", response_model=UserResponse)
async def verify_mfa_otp(payload: MfaOtpVerifyRequest, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if (
        not user
        or not user.mfa_otp_code
        or not user.mfa_otp_expires_at
        or user.mfa_otp_expires_at < datetime.utcnow()
        or not secrets.compare_digest(user.mfa_otp_code, payload.otp_code)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired one-time code.")

    user.mfa_otp_code = None
    user.mfa_otp_expires_at = None
    await db.commit()
    await db.refresh(user)

    return user

@app.post("/password/change", response_model=UserResponse)
async def change_password(payload: PasswordChangeRequest, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if not user or not pwd_context.verify(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    user.password_hash = pwd_context.hash(payload.new_password)
    user.password_changed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)

    _send_email(
        to_address=user.email,
        subject="Your password was changed",
        body=(
            f"Hi {user.name},\n\nYour account password was just changed. "
            "If this wasn't you, reset your password immediately via the /password/forgot flow."
        ),
    )

    return user

@app.post("/password/forgot")
async def forgot_password(payload: PasswordForgotRequest, db: AsyncSession = Depends(get_db)):
    """
    Always returns a generic success message, whether or not the email is
    registered, so this endpoint can't be used to enumerate accounts.
    """
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if user:
        otp_code = f"{random.randint(0, 999999):06d}"
        user.password_reset_otp = otp_code
        user.password_reset_otp_expires_at = datetime.utcnow() + OTP_VALIDITY
        await db.commit()

        _send_email(
            to_address=user.email,
            subject="Your password reset code",
            body=f"Your one-time password reset code is: {otp_code}\nIt expires in 10 minutes.",
        )

    return {"message": "If that email is registered, a password reset code has been sent."}

@app.post("/password/reset", response_model=UserResponse)
async def reset_password(payload: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if (
        not user
        or not user.password_reset_otp
        or not user.password_reset_otp_expires_at
        or user.password_reset_otp_expires_at < datetime.utcnow()
        or not secrets.compare_digest(user.password_reset_otp, payload.otp_code)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired one-time code.")

    user.password_hash = pwd_context.hash(payload.new_password)
    user.password_reset_otp = None
    user.password_reset_otp_expires_at = None
    user.password_changed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)

    _send_email(
        to_address=user.email,
        subject="Your password was reset",
        body=f"Hi {user.name},\n\nYour account password was just reset using a one-time code.",
    )

    return user
