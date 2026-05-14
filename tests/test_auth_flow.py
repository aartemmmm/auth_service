"""Сквозной тест auth-flow через SQLite (без Docker)."""
import pytest


@pytest.mark.anyio
async def test_login_refresh_logout(client):
    """Регистрация → логин → refresh → logout → повторный refresh → 401."""
    await client.post("/users", json={
        "email": "flow@test.com", "username": "flowuser", "password": "FlowPass1",
    })

    r = await client.post("/auth/login", json={
        "username_or_email": "flowuser", "password": "FlowPass1",
    })
    assert r.status_code == 200
    tokens = r.json()

    r = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    tokens2 = r.json()

    r = await client.post("/auth/logout", json={"refresh_token": tokens2["refresh_token"]})
    assert r.status_code == 200

    r = await client.post("/auth/refresh", json={"refresh_token": tokens2["refresh_token"]})
    assert r.status_code == 401
