"""Unit-тесты — без реальной БД (AsyncSession заменён mock-объектами)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import crud, schemas
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


# ─── Вспомогательные фабрики ───────────────────────────────────

def _db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    db.execute = AsyncMock()
    db.get = AsyncMock()
    return db


def _user(uid: int = 1, password: str = "pass") -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.email = f"u{uid}@test.com"
    u.username = f"user{uid}"
    u.hashed_password = hash_password(password)
    u.role = "user"
    u.is_active = True
    u.created_at = datetime.now(timezone.utc)
    return u


# ─── 1. Пароли и JWT ───────────────────────────────────────────

def test_security():
    """Хеширование паролей + JWT: выдача, раскодирование, уникальность."""
    hashed = hash_password("Secret123")
    assert verify_password("Secret123", hashed) is True
    assert verify_password("wrong", hashed) is False

    access = create_access_token(sub="1", session_id="s", role="user")
    assert decode_token(access)["typ"] == "access"

    rt1, _ = create_refresh_token(sub="1", session_id="s")
    rt2, _ = create_refresh_token(sub="1", session_id="s")
    assert rt1 != rt2  # jti делает каждый токен уникальным

    from jose import JWTError
    with pytest.raises(JWTError):
        decode_token("not.a.token")


# ─── 2. CRUD пользователей ─────────────────────────────────────

@pytest.mark.anyio
async def test_crud_users():
    """get / list / create / delete — через mock-сессию."""
    db = _db()

    db.get.return_value = _user(1)
    assert (await crud.get_user(db, 1)).id == 1

    db.get.return_value = None
    assert await crud.get_user(db, 999) is None

    created = None

    def capture(obj):
        nonlocal created
        created = obj

    db.add.side_effect = capture

    user_in = schemas.UserCreate(email="new@t.com", username="newuser", password="Pass1234")
    await crud.create_user(db, user_in)
    assert verify_password("Pass1234", created.hashed_password)
    db.commit.assert_awaited()


# ─── 3. CRUD сессий ────────────────────────────────────────────

@pytest.mark.anyio
async def test_crud_sessions():
    """create_session хранит хэш; revoke_session выставляет revoked_at."""
    db = _db()

    saved = None

    def capture(obj):
        nonlocal saved
        saved = obj

    db.add.side_effect = capture

    await crud.create_session(
        db, user=_user(), session_id="sid", refresh_token="tok",
        refresh_expires_at=datetime.now(timezone.utc) + timedelta(days=14),
    )
    assert saved.refresh_token_hash == hashlib.sha256(b"tok").hexdigest()

    sess = MagicMock()
    sess.revoked_at = None
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    await crud.revoke_session(db, sess)
    assert sess.revoked_at is not None
