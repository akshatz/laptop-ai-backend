"""Admin-only views (users.role == "admin")."""

from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import UserModel, UserSessionModel
from schemas import SignedInSession
from sessions import SESSION_MAX_AGE, current_user

router = APIRouter(prefix="/admin", tags=["admin"])

# Seen this recently = "active now" in the list.
ACTIVE_WINDOW = timedelta(minutes=5)


async def admin_user(user: UserModel = Depends(current_user)) -> UserModel:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only.")
    return user


@router.get("/sessions", response_model=List[SignedInSession])
async def signed_in_sessions(admin: UserModel = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    """Who is signed in to custom-frontend: every session not signed out and younger than
    SESSION_MAX_AGE, most recently seen first. Sessions from before user_sessions existed aren't
    listed (their cookies carry no session id)."""
    now = datetime.now(timezone.utc)
    rows = (await db.execute(
        select(UserSessionModel, UserModel)
        .join(UserModel, UserModel.id == UserSessionModel.user_id)
        .where(UserSessionModel.ended_at.is_(None),
               UserSessionModel.created_at > now - timedelta(seconds=SESSION_MAX_AGE),
               UserModel.is_active.is_(True))
        .order_by(UserSessionModel.last_seen_at.desc())
    )).all()
    return [
        SignedInSession(
            id=s.id, user_id=u.id, name=u.name, email=u.email, role=u.role,
            signed_in_at=s.created_at, last_seen_at=s.last_seen_at,
            expires_at=s.created_at + timedelta(seconds=SESSION_MAX_AGE),
            active_now=now - s.last_seen_at <= ACTIVE_WINDOW, user_agent=s.user_agent, is_you=u.id == admin.id,
        )
        for s, u in rows
    ]
