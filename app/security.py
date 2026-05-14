import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import jwt

_SECRET = os.getenv("JWT_SECRET") or secrets.token_urlsafe(48)


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:72]
    hashed = bcrypt.hashpw(pw, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    pw = password.encode("utf-8")[:72]
    return bcrypt.checkpw(pw, hashed_password.encode("utf-8"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_secret() -> str:
    return _SECRET


def create_access_token(*, sub: str, session_id: str, role: str, expires_minutes: int = 15) -> str:
    now = _utcnow()
    payload = {
        "typ": "access",
        "sub": sub,
        "sid": session_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, _get_secret(), algorithm="HS256")


def create_refresh_token(*, sub: str, session_id: str, expires_days: int = 14) -> tuple[str, datetime]:
    now = _utcnow()
    exp_dt = now + timedelta(days=expires_days)
    payload = {
        "typ": "refresh",
        "sub": sub,
        "sid": session_id,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(exp_dt.timestamp()),
    }
    token = jwt.encode(payload, _get_secret(), algorithm="HS256")
    return token, exp_dt


def decode_token(token: str) -> dict:
    return jwt.decode(token, _get_secret(), algorithms=["HS256"])

