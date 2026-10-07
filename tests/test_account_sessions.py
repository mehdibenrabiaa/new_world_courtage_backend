"""Customer/partner account sessions: single-device logout and "log out of all devices"."""

import time


def register(client, email):
    resp = client.post("/api/accounts/register", json={"name": "Test Client", "email": email, "password": "motdepasse123", "type": "client"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_logout_revokes_the_refresh_token(client):
    tokens = register(client, "logout-one@example.com")
    resp = client.post("/api/accounts/logout", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 204
    resp = client.post("/api/accounts/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


def test_logout_all_kills_every_session_immediately(client):
    first = register(client, "logout-all@example.com")
    time.sleep(1)  # token "iat" has one-second resolution
    second = client.post("/api/accounts/login", json={"email": "logout-all@example.com", "password": "motdepasse123"}).json()
    assert client.get("/api/accounts/me", headers=headers(first["access_token"])).status_code == 200

    time.sleep(1)
    resp = client.post("/api/accounts/me/logout-all", headers=headers(second["access_token"]))
    assert resp.status_code == 204

    # Both devices: still-unexpired access tokens are rejected, refresh tokens are dead.
    for tokens in (first, second):
        assert client.get("/api/accounts/me", headers=headers(tokens["access_token"])).status_code == 401
        assert client.post("/api/accounts/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401

    # Logging in again afterwards works normally.
    time.sleep(1)
    fresh = client.post("/api/accounts/login", json={"email": "logout-all@example.com", "password": "motdepasse123"}).json()
    assert client.get("/api/accounts/me", headers=headers(fresh["access_token"])).status_code == 200


def test_logout_all_requires_a_valid_session(client):
    assert client.post("/api/accounts/me/logout-all").status_code == 401
