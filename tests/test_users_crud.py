import pytest


@pytest.mark.anyio
async def test_create_list_get_delete_user(client):
    # create
    r = await client.post(
        "/users",
        json={"email": "u1@example.com", "username": "user1", "password": "SuperSecret123"},
    )
    assert r.status_code == 201, r.text
    u = r.json()
    assert u["email"] == "u1@example.com"
    assert u["username"] == "user1"
    assert u["role"] == "user"
    user_id = u["id"]

    # list
    r = await client.get("/users")
    assert r.status_code == 200
    users = r.json()
    assert isinstance(users, list)
    assert any(x["id"] == user_id for x in users)

    # get
    r = await client.get(f"/users/{user_id}")
    assert r.status_code == 200
    assert r.json()["id"] == user_id

    # delete
    r = await client.delete(f"/users/{user_id}")
    assert r.status_code == 200

    # get missing
    r = await client.get(f"/users/{user_id}")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_create_user_conflict(client):
    payload = {"email": "dup@example.com", "username": "dupuser", "password": "SuperSecret123"}
    r = await client.post("/users", json=payload)
    assert r.status_code == 201, r.text
    r = await client.post("/users", json=payload)
    assert r.status_code == 409

