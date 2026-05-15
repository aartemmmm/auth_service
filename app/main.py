from __future__ import annotations

import asyncio
import logging
import time
import uuid as _uuid
from datetime import datetime, timezone
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
try:
    from jose import JWTError
except Exception:  # pragma: no cover
    JWTError = Exception  # type: ignore[misc,assignment]
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from app import crud, models, schemas
from app.database import get_db
from app.logging_config import correlation_id_var, setup_logging
from app.metrics import REQUEST_COUNT, REQUEST_LATENCY
from app.security import create_access_token, create_refresh_token, decode_token, verify_password

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Auth Service", description="Auth & Identity microservice")

bearer_scheme = HTTPBearer(auto_error=False)


# -------- Middleware --------

class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Присваивает каждому запросу уникальный X-Request-ID.
    Берёт значение из входящего заголовка или генерирует новый UUID.
    Сохраняет ID в request.state и ContextVar, добавляет в заголовки ответа.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        cid = request.headers.get("X-Request-ID") or str(_uuid.uuid4())
        request.state.correlation_id = cid
        token = correlation_id_var.set(cid)
        try:
            response: Response = await call_next(request)  # type: ignore[operator]
        finally:
            correlation_id_var.reset(token)
        response.headers["X-Request-ID"] = cid
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Фиксирует количество запросов и время их выполнения для Prometheus.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        start = time.perf_counter()
        response: Response = await call_next(request)  # type: ignore[operator]
        duration = time.perf_counter() - start

        endpoint = request.url.path
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status=str(response.status_code),
        ).inc()
        REQUEST_LATENCY.labels(
            method=request.method,
            endpoint=endpoint,
        ).observe(duration)
        return response


# Порядок важен: CorrelationID добавлен последним → выполняется первым
app.add_middleware(MetricsMiddleware)
app.add_middleware(CorrelationIDMiddleware)


# -------- Helpers --------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cid(request: Request) -> str:
    return str(getattr(request.state, "correlation_id", ""))


# -------- Metrics endpoint --------

@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Эндпоинт для Prometheus — отдаёт метрики в text/plain формате."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# -------- Test endpoints --------

@app.get("/test/error", tags=["test"])
async def test_error(request: Request) -> None:
    """Всегда возвращает 500 — для проверки error rate в Grafana."""
    logger.warning("test error endpoint called", extra={"correlation_id": _cid(request)})
    raise HTTPException(status_code=500, detail="Тестовая ошибка")


@app.get("/test/slow", tags=["test"])
async def test_slow(request: Request) -> dict[str, str]:
    """Имитирует долгую обработку (2 с) — для проверки latency в Grafana."""
    logger.info("test slow endpoint called", extra={"correlation_id": _cid(request)})
    await asyncio.sleep(2)
    return {"status": "ok", "message": "Медленный ответ после 2 секунд"}


# -------- Users --------

@app.get("/users", response_model=List[schemas.UserResponse], dependencies=[Depends(bearer_scheme)])
async def list_users(request: Request, db: AsyncSession = Depends(get_db)) -> list[models.User]:
    logger.info("list users", extra={"correlation_id": _cid(request)})
    return await crud.list_users(db)


@app.post(
    "/users",
    response_model=schemas.UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(bearer_scheme)],
)
async def create_user(
    user_in: schemas.UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> models.User:
    logger.info("create user", extra={"correlation_id": _cid(request), "email": user_in.email})
    try:
        user = await crud.create_user(db, user_in)
        logger.info("user created", extra={"correlation_id": _cid(request), "user_id": user.id})
        return user
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="User with this email/username already exists")


@app.get("/users/{user_id}", response_model=schemas.UserResponse, dependencies=[Depends(bearer_scheme)])
async def get_user(user_id: int, request: Request, db: AsyncSession = Depends(get_db)) -> models.User:
    logger.info("get user", extra={"correlation_id": _cid(request), "user_id": user_id})
    user = await crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.delete("/users/{user_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(bearer_scheme)])
async def delete_user(user_id: int, request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    logger.info("delete user", extra={"correlation_id": _cid(request), "user_id": user_id})
    user = await crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await crud.delete_user(db, user)
    logger.info("user deleted", extra={"correlation_id": _cid(request), "user_id": user_id})
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
async def whoami(
    request: Request,
    current_user: schemas.UserResponse = Depends(get_current_user),
) -> schemas.UserResponse:
    logger.info("whoami", extra={"correlation_id": _cid(request), "user_id": current_user.id})
    return current_user


@app.post("/auth/login", response_model=schemas.TokenPairResponse)
async def login(
    payload: schemas.LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenPairResponse:
    logger.info("login attempt", extra={"correlation_id": _cid(request)})
    user = (await crud.get_user_by_email(db, payload.username_or_email)) or (
        await crud.get_user_by_username(db, payload.username_or_email)
    )
    if not user or not user.is_active:
        logger.warning("login failed: user not found", extra={"correlation_id": _cid(request)})
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(payload.password, user.hashed_password):
        logger.warning("login failed: wrong password", extra={"correlation_id": _cid(request)})
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session_id = str(_uuid.uuid4())
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
    logger.info("login success", extra={"correlation_id": _cid(request), "user_id": user.id})
    return schemas.TokenPairResponse(access_token=access_token, refresh_token=refresh_token)


@app.post("/auth/refresh", response_model=schemas.TokenPairResponse)
async def refresh(
    payload: schemas.RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenPairResponse:
    logger.info("token refresh", extra={"correlation_id": _cid(request)})
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
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= _utcnow():
        raise HTTPException(status_code=401, detail="Refresh token expired")
    if not crud.verify_refresh_token_matches_session(session, payload.refresh_token):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    new_refresh_token, new_refresh_expires_at = create_refresh_token(
        sub=str(user.id), session_id=str(session.session_id)
    )
    session.refresh_token_hash = crud._hash_refresh_token(new_refresh_token)  # type: ignore[attr-defined]
    session.refresh_expires_at = new_refresh_expires_at
    session.user_agent = request.headers.get("user-agent")
    session.ip = request.client.host if request.client else None
    db.add(session)
    await db.commit()
    await db.refresh(session)

    new_access_token = create_access_token(
        sub=str(user.id), session_id=str(session.session_id), role=user.role
    )
    logger.info("token refreshed", extra={"correlation_id": _cid(request), "user_id": user.id})
    return schemas.TokenPairResponse(access_token=new_access_token, refresh_token=new_refresh_token)


@app.post("/auth/logout", status_code=status.HTTP_200_OK)
async def logout(
    payload: schemas.LogoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    logger.info("logout", extra={"correlation_id": _cid(request)})
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
        return {"message": "Logged out"}

    if session.revoked_at is None:
        await crud.revoke_session(db, session)
    return {"message": "Logged out"}


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Auth Service", "docs": "/docs", "status": "running with PostgreSQL"}
