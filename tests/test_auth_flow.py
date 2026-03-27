import pytest


@pytest.mark.anyio
async def test_login_refresh_logout_flow(client):
    # create user
    r = await client.post(
        "/users",
        json={"email": "auth@example.com", "username": "authuser", "password": "SuperSecret123"},
    )
    assert r.status_code == 201, r.text

    # login
    r = await client.post("/auth/login", json={"username_or_email": "authuser", "password": "SuperSecret123"})
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    # refresh
    r = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200, r.text
    tokens2 = r.json()
    assert tokens2["access_token"]
    assert tokens2["refresh_token"]

    # logout
    r = await client.post("/auth/logout", json={"refresh_token": tokens2["refresh_token"]})
    assert r.status_code == 200, r.text

    # refresh after logout should fail
    r = await client.post("/auth/refresh", json={"refresh_token": tokens2["refresh_token"]})
    assert r.status_code == 401

