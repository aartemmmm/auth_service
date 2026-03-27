from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
try:
    from jose import JWTError
except Exception:  # pragma: no cover
    JWTError = Exception  # type: ignore[misc,assignment]
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.database import get_db
from app.security import create_access_token, create_refresh_token, decode_token, verify_password

app = FastAPI(title="Auth Service", description="Auth & Identity microservice")

bearer_scheme = HTTPBearer(auto_error=False)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# -------- Users --------

@app.get("/users", response_model=List[schemas.UserResponse], dependencies=[Depends(bearer_scheme)])
async def list_users(db: AsyncSession = Depends(get_db)):
    return await crud.list_users(db)


@app.post("/users", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(bearer_scheme)])
async def create_user(user_in: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    try:
        return await crud.create_user(db, user_in)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="User with this email/username already exists")


@app.get("/users/{user_id}", response_model=schemas.UserResponse, dependencies=[Depends(bearer_scheme)])
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.delete("/users/{user_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(bearer_scheme)])
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await crud.delete_user(db, user)
    return {"message": "User deleted successfully"}


# -------- Auth --------


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> schemas.UserResponse:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = credentials.credentials
    try:
        claims = decode_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if claims.get("typ") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await crud.get_user(db, int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return schemas.UserResponse.model_validate(user)


@app.get("/auth/whoami", response_model=schemas.UserResponse)
async def whoami(current_user: schemas.UserResponse = Depends(get_current_user)):
    return current_user

@app.post("/auth/login", response_model=schemas.TokenPairResponse)
async def login(payload: schemas.LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # username_or_email
    user = (await crud.get_user_by_email(db, payload.username_or_email)) or (
        await crud.get_user_by_username(db, payload.username_or_email)
    )
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Генерируем session_id один раз и используем его в JWT и в БД.
    # Это исключает рассинхрон между claim `sid` и записью `user_sessions.session_id`.
    session_id = str(uuid.uuid4())
    user_agent = request.headers.get("user-agent")
    ip = request.client.host if request.client else None

    refresh_token, refresh_expires_at = create_refresh_token(sub=str(user.id), session_id=session_id)
    await crud.create_session(
        db,
        user=user,
        session_id=session_id,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_expires_at,
        user_agent=user_agent,
        ip=ip,
    )

    access_token = create_access_token(sub=str(user.id), session_id=session_id, role=user.role)
    return schemas.TokenPairResponse(access_token=access_token, refresh_token=refresh_token)


@app.post("/auth/refresh", response_model=schemas.TokenPairResponse)
async def refresh(payload: schemas.RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        claims = decode_token(payload.refresh_token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if claims.get("typ") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = claims.get("sub")
    session_id = claims.get("sid")
    if not user_id or not session_id:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = await crud.get_user(db, int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    session = await crud.get_session_by_session_id(db, str(session_id))
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if session.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Session revoked")

    expires_at = session.refresh_expires_at
    # SQLite часто возвращает datetime без tzinfo, поэтому приводим к UTC-aware.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= _utcnow():
        raise HTTPException(status_code=401, detail="Refresh token expired")
    if not crud.verify_refresh_token_matches_session(session, payload.refresh_token):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Ротация refresh (обновляем сессию новым refresh)
    new_refresh_token, new_refresh_expires_at = create_refresh_token(sub=str(user.id), session_id=str(session.session_id))
    session.refresh_token_hash = crud._hash_refresh_token(new_refresh_token)  # type: ignore[attr-defined]
    session.refresh_expires_at = new_refresh_expires_at
    session.user_agent = request.headers.get("user-agent")
    session.ip = request.client.host if request.client else None
    db.add(session)
    await db.commit()
    await db.refresh(session)

    new_access_token = create_access_token(sub=str(user.id), session_id=str(session.session_id), role=user.role)
    return schemas.TokenPairResponse(access_token=new_access_token, refresh_token=new_refresh_token)


@app.post("/auth/logout", status_code=status.HTTP_200_OK)
async def logout(payload: schemas.LogoutRequest, db: AsyncSession = Depends(get_db)):
    try:
        claims = decode_token(payload.refresh_token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if claims.get("typ") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    session_id = claims.get("sid")
    if not session_id:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    session = await crud.get_session_by_session_id(db, str(session_id))
    if not session:
        # logout должен быть идемпотентным
        return {"message": "Logged out"}

    if session.revoked_at is None:
        await crud.revoke_session(db, session)
    return {"message": "Logged out"}


@app.get("/")
def root():
    return {"message": "Auth Service", "docs": "/docs", "status": "running with PostgreSQL"}
