"""
Интеграционные тесты — реальная PostgreSQL.
NullPool + override get_db гарантируют изоляцию между тестами.
"""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

load_dotenv()

PG_URL = (
    f"postgresql+asyncpg://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST', '127.0.0.1')}:{os.getenv('DB_PORT', '5432')}"
    f"/{os.getenv('DB_NAME')}"
)

pytestmark = pytest.mark.anyio


@pytest.fixture()
async def pg_session():
    engine = create_async_engine(PG_URL, echo=False, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
async def http_client(pg_session: AsyncSession):
    from app.main import app
    from app.database import get_db

    async def override_get_db():
        yield pg_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ─── 1. Полный auth-цикл с проверкой БД ────────────────────────

async def test_full_auth_cycle(http_client, pg_session):
    """login → сессия в БД → whoami → refresh (ротация) → logout (revoke) → 401."""
    from app import crud, models, schemas
    from app.security import decode_token
    from app.crud import _hash_refresh_token

    user_in = schemas.UserCreate(email="int@test.com", username="intuser", password="IntPass123")
    existing = await crud.get_user_by_email(pg_session, user_in.email)
    if existing:
        await crud.delete_user(pg_session, existing)
    user = await crud.create_user(pg_session, user_in)

    # Логин
    r = await http_client.post("/auth/login", json={"username_or_email": "intuser", "password": "IntPass123"})
    assert r.status_code == 200
    tokens = r.json()
    sid = decode_token(tokens["refresh_token"])["sid"]

    # Сессия в БД
    sess = await crud.get_session_by_session_id(pg_session, sid)
    assert sess is not None and sess.revoked_at is None

    # whoami
    r = await http_client.get("/auth/whoami", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 200 and r.json()["email"] == "int@test.com"

    # Refresh → новый токен и обновлённый хэш в БД
    r = await http_client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new_rt = r.json()["refresh_token"]
    assert new_rt != tokens["refresh_token"]

    res = await pg_session.execute(select(models.UserSession).where(models.UserSession.session_id == sid))
    assert res.scalar_one().refresh_token_hash == _hash_refresh_token(new_rt)

    # Logout → revoked_at
    r = await http_client.post("/auth/logout", json={"refresh_token": new_rt})
    assert r.status_code == 200
    res = await pg_session.execute(select(models.UserSession).where(models.UserSession.session_id == sid))
    assert res.scalar_one().revoked_at is not None

    # Refresh после logout → 401
    r = await http_client.post("/auth/refresh", json={"refresh_token": new_rt})
    assert r.status_code == 401

    await crud.delete_user(pg_session, user)


# ─── 2. Дубликат email/username → 409 ──────────────────────────

async def test_duplicate_user(http_client, pg_session):
    from app import crud, schemas
    user_in = schemas.UserCreate(email="dup@test.com", username="dupuser", password="DupPass123")
    existing = await crud.get_user_by_email(pg_session, user_in.email)
    if existing:
        await crud.delete_user(pg_session, existing)

    r1 = await http_client.post("/users", json={"email": "dup@test.com", "username": "dupuser", "password": "DupPass123"})
    assert r1.status_code == 201
    r2 = await http_client.post("/users", json={"email": "dup@test.com", "username": "dupuser", "password": "DupPass123"})
    assert r2.status_code == 409

    u = await crud.get_user_by_email(pg_session, "dup@test.com")
    if u:
        await crud.delete_user(pg_session, u)
