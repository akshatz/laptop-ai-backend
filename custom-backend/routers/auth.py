import random
import secrets as secrets_module
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db import get_db, secrets
from models import UserModel
from schemas import (
    UserCreate, UserResponse, EmailVerifyRequest, LoginRequest, LoginResponse,
    MfaOtpVerifyRequest, PasswordChangeRequest, PasswordForgotRequest, PasswordResetRequest,
)

router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

OTP_VALIDITY = timedelta(minutes=10)
PASSWORD_MAX_AGE = timedelta(days=180)

# ✉️ email sending (Gmail SMTP relay, same creds Passbolt uses)
def _send_email(to_address: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = secrets["smtp_from"]
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(secrets["smtp_user"], secrets["smtp_password"])
        smtp.send_message(message)

@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
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

    verification_token = secrets_module.token_urlsafe(32)

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

@router.get("/users", response_model=List[UserResponse])
async def get_all_users(db: AsyncSession = Depends(get_db)):
    """
    Retrieves all records matching registered user accounts.
    """
    query = await db.execute(select(UserModel).order_by(UserModel.created_at.desc()))
    return query.scalars().all()

@router.post("/users/verify", response_model=UserResponse)
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

@router.post("/users/deactivate", response_model=UserResponse)
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

@router.post("/users/reactivate", response_model=UserResponse)
async def reactivate_user(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if not user or not pwd_context.verify(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    user.is_active = True
    await db.commit()
    await db.refresh(user)

    return user

@router.post("/mfa/enable", response_model=UserResponse)
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

@router.post("/mfa/disable", response_model=UserResponse)
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

@router.post("/login", response_model=LoginResponse)
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

@router.post("/mfa/verify-otp", response_model=UserResponse)
async def verify_mfa_otp(payload: MfaOtpVerifyRequest, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if (
        not user
        or not user.mfa_otp_code
        or not user.mfa_otp_expires_at
        or user.mfa_otp_expires_at < datetime.utcnow()
        or not secrets_module.compare_digest(user.mfa_otp_code, payload.otp_code)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired one-time code.")

    user.mfa_otp_code = None
    user.mfa_otp_expires_at = None
    await db.commit()
    await db.refresh(user)

    return user

@router.post("/password/change", response_model=UserResponse)
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

@router.post("/password/forgot")
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

@router.post("/password/reset", response_model=UserResponse)
async def reset_password(payload: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    query = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    user = query.scalar_one_or_none()

    if (
        not user
        or not user.password_reset_otp
        or not user.password_reset_otp_expires_at
        or user.password_reset_otp_expires_at < datetime.utcnow()
        or not secrets_module.compare_digest(user.password_reset_otp, payload.otp_code)
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
