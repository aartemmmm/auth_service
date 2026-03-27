from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.security import hash_password


async def get_user(db: AsyncSession, user_id: int) -> Optional[models.User]:
    return await db.get(models.User, user_id)


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[models.User]:
    res = await db.execute(select(models.User).where(models.User.email == email))
    return res.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[models.User]:
    res = await db.execute(select(models.User).where(models.User.username == username))
    return res.scalar_one_or_none()


async def list_users(db: AsyncSession, *, limit: int = 100, offset: int = 0) -> list[models.User]:
    res = await db.execute(select(models.User).order_by(models.User.id.asc()).offset(offset).limit(limit))
    return list(res.scalars().all())


async def create_user(db: AsyncSession, user_in: schemas.UserCreate, *, role: str = "user") -> models.User:
    db_user = models.User(
        email=user_in.email,
        username=user_in.username,
        hashed_password=hash_password(user_in.password),
        role=role,
        is_active=True,
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


async def delete_user(db: AsyncSession, user: models.User) -> None:
    await db.delete(user)
    await db.commit()


def _hash_refresh_token(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


async def create_session(
    db: AsyncSession,
    *,
    user: models.User,
    session_id: str,
    refresh_token: str,
    refresh_expires_at: datetime,
    user_agent: str | None = None,
    ip: str | None = None,
) -> models.UserSession:
    sess = models.UserSession(
        session_id=session_id,
        user_id=user.id,
        refresh_token_hash=_hash_refresh_token(refresh_token),
        refresh_expires_at=refresh_expires_at,
        revoked_at=None,
        user_agent=user_agent,
        ip=ip,
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return sess


async def get_session_by_session_id(db: AsyncSession, session_id: str) -> Optional[models.UserSession]:
    res = await db.execute(select(models.UserSession).where(models.UserSession.session_id == session_id))
    return res.scalar_one_or_none()


async def revoke_session(db: AsyncSession, session: models.UserSession) -> models.UserSession:
    session.revoked_at = datetime.now(timezone.utc)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


def verify_refresh_token_matches_session(session: models.UserSession, refresh_token: str) -> bool:
    return session.refresh_token_hash == _hash_refresh_token(refresh_token)

